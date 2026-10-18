"""Environment builder: distribution data -> ResolvedEnv."""

from __future__ import annotations

import os

import pytest
from mojox_core.environment import build_env
from mojox_core.errors import ConfigError
from mojox_core.types import DistKind


def _dist(name, include_dir, kind=DistKind.PRECOMPILED, packages=None, provenance="unknown"):
    return {
        "name": name,
        "include_dir": include_dir,
        "kind": kind,
        "packages": packages or (name,),
        "provenance": provenance,
        "native_lib_dirs": (),
    }


class TestBuildEnv:
    def test_empty_distributions(self):
        env = build_env([], None, "/venv/bin/mojo", "1.0.0b2")
        assert env.include_sequence == ()
        assert env.mojo_path == "/venv/bin/mojo"

    def test_include_sequence_preserves_order(self):
        dists = [
            _dist("a", "/venv/mojo_packages/a"),
            _dist("b", "/venv/mojo_packages/b"),
        ]
        env = build_env(dists, None, "/venv/bin/mojo", "1.0.0b2")
        assert len(env.include_sequence) == 2
        assert env.include_sequence[0].name == "a"
        assert env.include_sequence[1].name == "b"

    def test_include_sequence_is_tuple_not_set(self):
        dists = [_dist("a", "/a"), _dist("b", "/b")]
        env = build_env(dists, None, "/venv/bin/mojo", "1.0.0b2")
        assert isinstance(env.include_sequence, tuple)

    def test_missing_lockfile_degrades_provenance(self):
        dists = [_dist("a", "/a")]
        env = build_env(dists, None, "/venv/bin/mojo", "1.0.0b2")
        assert env.lock_version is None

    def test_lockfile_version_recorded(self):
        lock_data = {"version": 1, "packages": []}
        dists = [_dist("a", "/a")]
        env = build_env(dists, lock_data, "/venv/bin/mojo", "1.0.0b2")
        assert env.lock_version == 1

    def test_path_mojo_divergence_reported(self):
        dists = []
        env = build_env(
            dists,
            None,
            "/venv/bin/mojo",
            "1.0.0b2",
            path_mojo="/usr/bin/mojo",
        )
        assert env.path_mojo == "/usr/bin/mojo"
        assert len(env.diagnostics) >= 1
        assert any("PATH" in d.message for d in env.diagnostics)

    def test_source_and_precompiled_in_same_dir_errors(self):
        dists = [
            _dist("x", "/pkg", kind=DistKind.SOURCE, packages=("x",)),
            _dist("x-pre", "/pkg", kind=DistKind.PRECOMPILED, packages=("x",)),
        ]
        with pytest.raises(ConfigError, match="source-shadows-precompiled"):
            build_env(dists, None, "/venv/bin/mojo", "1.0.0b2")


class TestReadLocaleEnv:
    """Only locale variables of the host environment ever reach HostFacts."""

    def test_keeps_lang_and_lc_names(self):
        from mojox_core.plan import select_locale_env

        environ = {
            "LANG": "fr_FR.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LC_CTYPE": "en_US.UTF-8",
            "LC_MEASUREMENT": "fr_FR.UTF-8",
            "SECRET": "hunter2",
            "AWS_SECRET_ACCESS_KEY": "x",
            "PATH": "/usr/bin",
            "LANGX": "x",
            "LC_": "x",
            "lc_all": "x",
            "LC_ALL ": "x",
        }
        assert select_locale_env(environ) == {
            "LANG": "fr_FR.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LC_CTYPE": "en_US.UTF-8",
            "LC_MEASUREMENT": "fr_FR.UTF-8",
        }

    def test_empty_values_count_as_unset(self):
        from mojox_core.plan import select_locale_env

        assert select_locale_env({"LANG": "", "LC_ALL": ""}) == {}

    def test_read_host_facts_uses_given_environ(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        host = read_host_facts(tmp_path, environ={"LANG": "fr_FR.UTF-8", "SECRET": "x"})
        assert host.locale_env == {"LANG": "fr_FR.UTF-8"}

    def test_read_host_facts_defaults_to_process_environ(self, tmp_path, monkeypatch):
        from mojox_core.io.environment import read_host_facts

        for name in [n for n in os.environ if n == "LANG" or n.startswith("LC_")]:
            monkeypatch.delenv(name)
        monkeypatch.setenv("LC_CTYPE", "en_US.UTF-8")
        monkeypatch.setenv("SECRET", "x")
        assert read_host_facts(tmp_path).locale_env == {"LC_CTYPE": "en_US.UTF-8"}


class TestDetectCpuCount:
    """CPU count = min(affinity, cgroup v2 quota up the hierarchy, os), or MOJOX_CPU_COUNT verbatim."""

    @staticmethod
    def _cgroup(root, proc_line: str, levels: dict[str, str]) -> None:
        """Fake ``proc/self/cgroup`` and ``cpu.max`` files under *root*; keys are paths below the mount."""
        (root / "proc" / "self").mkdir(parents=True)
        (root / "proc" / "self" / "cgroup").write_text(proc_line)
        mount = root / "sys" / "fs" / "cgroup"
        mount.mkdir(parents=True)
        for rel, cpu_max in levels.items():
            level = mount / rel if rel else mount
            level.mkdir(parents=True, exist_ok=True)
            (level / "cpu.max").write_text(cpu_max)

    @staticmethod
    def _detect(root, environ=None, *, affinity=None, os_count=None):
        from mojox_core.io.environment import detect_cpu_count

        return detect_cpu_count(environ or {}, root=root, affinity=lambda: affinity, os_count=lambda: os_count)

    def test_affinity_below_os(self, tmp_path):
        assert self._detect(tmp_path, affinity=4, os_count=8) == (4, "affinity")

    def test_os_only(self, tmp_path):
        assert self._detect(tmp_path, os_count=8) == (8, "os")

    def test_nothing_available_is_one(self, tmp_path):
        assert self._detect(tmp_path) == (1, "os")

    def test_tie_reports_the_least_specific_source(self, tmp_path):
        self._cgroup(tmp_path, "0::/a\n", {"a": "800000 100000"})
        assert self._detect(tmp_path, affinity=8, os_count=8) == (8, "os")

    @pytest.mark.parametrize(
        ("cpu_max", "expected"),
        [
            ("max 100000", (16, "os")),
            ("50000 100000", (1, "cgroup")),
            ("150000 100000", (2, "cgroup")),
            ("200000 100000", (2, "cgroup")),
            ("200000 100000\n", (2, "cgroup")),
            ("  300000   100000 \n", (3, "cgroup")),
            ("1 1000000", (1, "cgroup")),
            ("9" * 40 + " 100000", (16, "os")),
        ],
    )
    def test_leaf_quota(self, tmp_path, cpu_max, expected):
        self._cgroup(tmp_path, "0::/a/b\n", {"a/b": cpu_max})
        assert self._detect(tmp_path, os_count=16) == expected

    def test_tighter_parent_wins(self, tmp_path):
        self._cgroup(tmp_path, "0::/a/b\n", {"a/b": "400000 100000", "a": "200000 100000"})
        assert self._detect(tmp_path, os_count=16) == (2, "cgroup")

    def test_mount_root_is_included(self, tmp_path):
        self._cgroup(tmp_path, "0::/a\n", {"a": "max 100000", "": "300000 100000"})
        assert self._detect(tmp_path, os_count=16) == (3, "cgroup")

    def test_cgroup_namespace_root(self, tmp_path):
        self._cgroup(tmp_path, "0::/\n", {"": "100000 100000"})
        assert self._detect(tmp_path, os_count=16) == (1, "cgroup")

    def test_path_missing_under_mount_reads_mount_root_only(self, tmp_path):
        self._cgroup(tmp_path, "0::/docker/abc\n", {"": "200000 100000"})
        assert self._detect(tmp_path, os_count=16) == (2, "cgroup")

    def test_path_missing_and_no_mount(self, tmp_path):
        (tmp_path / "proc" / "self").mkdir(parents=True)
        (tmp_path / "proc" / "self" / "cgroup").write_text("0::/a\n")
        assert self._detect(tmp_path, os_count=4) == (4, "os")

    def test_v1_only_content_is_ignored(self, tmp_path):
        self._cgroup(tmp_path, "3:cpu,cpuacct:/docker/x\n", {"docker/x": "100000 100000", "": "100000 100000"})
        assert self._detect(tmp_path, os_count=8) == (8, "os")

    def test_hybrid_uses_the_unified_line(self, tmp_path):
        self._cgroup(
            tmp_path,
            "12:cpu,cpuacct:/elsewhere\n1:name=systemd:/x\n0::/a\n",
            {"a": "200000 100000", "elsewhere": "100000 100000"},
        )
        assert self._detect(tmp_path, os_count=8) == (2, "cgroup")

    @pytest.mark.parametrize("cgroup_path", ["/../a", "/a/../..", "/a/../../b", "/.."])
    def test_dotdot_never_escapes_the_mount(self, tmp_path, cgroup_path):
        # Quota outside the fake mount must never be read.
        (tmp_path / "sys" / "fs").mkdir(parents=True)
        (tmp_path / "sys" / "fs" / "cpu.max").write_text("100000 100000")
        (tmp_path / "sys" / "fs" / "a").mkdir()
        (tmp_path / "sys" / "fs" / "a" / "cpu.max").write_text("100000 100000")
        (tmp_path / "sys" / "fs" / "b").mkdir()
        (tmp_path / "sys" / "fs" / "b" / "cpu.max").write_text("100000 100000")
        self._cgroup(tmp_path, f"0::{cgroup_path}\n", {"a": "max 100000"})
        assert self._detect(tmp_path, os_count=8) == (8, "os")

    @pytest.mark.parametrize(
        "cpu_max", ["garbage", "0 100000", "100000 0", "", "-100000 100000", "100000", "1 2 3", "1.5 1", "max"]
    )
    def test_malformed_level_is_ignored(self, tmp_path, cpu_max):
        self._cgroup(tmp_path, "0::/a\n", {"a": cpu_max, "": "300000 100000"})
        assert self._detect(tmp_path, os_count=8) == (3, "cgroup")

    def test_non_text_cpu_max_is_ignored(self, tmp_path):
        self._cgroup(tmp_path, "0::/a\n", {"a": ""})
        (tmp_path / "sys" / "fs" / "cgroup" / "a" / "cpu.max").write_bytes(b"\xff\xfe 1")
        assert self._detect(tmp_path, os_count=8) == (8, "os")

    def test_malformed_proc_cgroup_is_ignored(self, tmp_path):
        self._cgroup(tmp_path, "nonsense", {"": "100000 100000"})
        assert self._detect(tmp_path, os_count=8) == (8, "os")

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads mode-000 files")
    def test_unreadable_level_is_ignored(self, tmp_path):
        self._cgroup(tmp_path, "0::/a\n", {"a": "100000 100000", "": "300000 100000"})
        leaf = tmp_path / "sys" / "fs" / "cgroup" / "a" / "cpu.max"
        leaf.chmod(0)
        try:
            assert self._detect(tmp_path, os_count=8) == (3, "cgroup")
        finally:
            leaf.chmod(0o644)

    def test_affinity_below_cgroup_below_os(self, tmp_path):
        self._cgroup(tmp_path, "0::/a\n", {"a": "400000 100000"})
        assert self._detect(tmp_path, affinity=2, os_count=16) == (2, "affinity")

    def test_nonpositive_probe_values_are_ignored(self, tmp_path):
        assert self._detect(tmp_path, affinity=0, os_count=8) == (8, "os")

    def test_raising_probes_never_escape(self, tmp_path):
        from mojox_core.io.environment import detect_cpu_count

        def boom() -> int:
            raise OSError("no affinity here")

        assert detect_cpu_count({}, root=tmp_path, affinity=boom, os_count=lambda: 6) == (6, "os")

    def test_env_override_is_used_verbatim(self, tmp_path):
        self._cgroup(tmp_path, "0::/a\n", {"a": "100000 100000"})
        assert self._detect(tmp_path, {"MOJOX_CPU_COUNT": "3"}, affinity=1, os_count=1) == (3, "MOJOX_CPU_COUNT")

    def test_env_override_tolerates_surrounding_whitespace(self, tmp_path):
        assert self._detect(tmp_path, {"MOJOX_CPU_COUNT": " 12 "}, os_count=4) == (12, "MOJOX_CPU_COUNT")

    @pytest.mark.parametrize("value", ["0", "-2", "abc", "", "2.5", "1_000", "+0", "9" * 5000])
    def test_bad_env_override_raises(self, tmp_path, value):
        with pytest.raises(ConfigError) as exc_info:
            self._detect(tmp_path, {"MOJOX_CPU_COUNT": value}, os_count=4)
        assert exc_info.value.key_path == "MOJOX_CPU_COUNT"

    def test_real_host_never_raises(self):
        from mojox_core.io.environment import detect_cpu_count

        count, source = detect_cpu_count({})
        assert count >= 1
        assert source in {"affinity", "cgroup", "os"}

    def test_read_host_facts_takes_cpu_count_from_environ(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        host = read_host_facts(tmp_path, environ={"MOJOX_CPU_COUNT": "5"})
        assert (host.cpu_count, host.cpu_count_source) == (5, "MOJOX_CPU_COUNT")

    def test_read_host_facts_rejects_bad_override(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        with pytest.raises(ConfigError):
            read_host_facts(tmp_path, environ={"MOJOX_CPU_COUNT": "0"})
