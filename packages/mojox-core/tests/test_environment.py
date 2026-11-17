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


class TestUsablePathEntries:
    """The PATH filter that keeps a binary planted in the project from shadowing ``cc``."""

    @staticmethod
    def _project(tmp_path):
        root = tmp_path / "proj"
        (root / ".direnv" / "bin").mkdir(parents=True)
        (root / "node_modules" / ".bin").mkdir(parents=True)
        (root / "bin").mkdir()
        outside = tmp_path / "outside" / "bin"
        outside.mkdir(parents=True)
        return root, outside

    def test_absolute_entries_inside_the_project_are_dropped(self, tmp_path):
        from mojox_core.io.environment import usable_path_entries

        root, outside = self._project(tmp_path)
        raw = f"{root}/.direnv/bin:{root}/node_modules/.bin:{outside}:{root}/bin:{root}"
        assert usable_path_entries(raw, root) == (str(outside),)

    def test_a_symlink_into_the_project_is_dropped(self, tmp_path):
        from mojox_core.io.environment import usable_path_entries

        root, outside = self._project(tmp_path)
        link = tmp_path / "link"
        link.symlink_to(root / "bin")
        assert usable_path_entries(f"{link}:{outside}", root) == (str(outside),)

    def test_a_sibling_with_the_same_prefix_is_kept(self, tmp_path):
        from mojox_core.io.environment import usable_path_entries

        root, _ = self._project(tmp_path)
        sibling = tmp_path / "proj-tools" / "bin"
        sibling.mkdir(parents=True)
        assert usable_path_entries(str(sibling), root) == (str(sibling),)

    def test_relative_empty_and_duplicate_entries_never_come_back(self, tmp_path):
        from mojox_core.io.environment import usable_path_entries

        root, outside = self._project(tmp_path)
        raw = f"::.:bin:./bin:../x:{outside}:{outside}:"
        assert usable_path_entries(raw, root) == (str(outside),)

    def test_entries_that_do_not_exist_are_kept(self, tmp_path):
        from mojox_core.io.environment import usable_path_entries

        root, _ = self._project(tmp_path)
        assert usable_path_entries("/nonexistent/dir", root) == ("/nonexistent/dir",)

    def test_traversal_and_trailing_slash_forms_are_dropped(self, tmp_path):
        from mojox_core.io.environment import usable_path_entries

        root, outside = self._project(tmp_path)
        raw = f"{root}/../{root.name}/.direnv/bin:{root}/bin/:{root}/./bin:{outside}"
        assert usable_path_entries(raw, root) == (str(outside),)

    def test_a_symlink_outside_the_project_pointing_into_it_is_dropped(self, tmp_path):
        from mojox_core.io.environment import usable_path_entries

        root, outside = self._project(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "tools").symlink_to(root / ".direnv" / "bin")
        assert usable_path_entries(f"{elsewhere}/tools:{outside}", root) == (str(outside),)


def _executable(path, text="#!/bin/sh\nexit 0\n"):
    """Write an executable file, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class TestReadHostFactsToolchain:
    """What ``read_host_facts`` learns about the PATH, the forwarded variables and the C compiler."""

    def test_variables_off_the_allowlist_are_not_forwarded(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        environ = {
            "LD_LIBRARY_PATH": "/evil",
            "PYTHONPATH": "/evil",
            "FOO": "bar",
            "NIX_CONFIG": "access-tokens = github.com=ghp_SECRET",
            "NIX_PATH": "nixpkgs=/secret",
            "NIX_REMOTE": "daemon",
            "NIX_FOO": "x",
        }
        host = read_host_facts(tmp_path, environ=environ)
        assert host.forwarded_env == {}
        assert "ghp_SECRET" not in repr(host)

    def test_listed_variables_are_forwarded(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        environ = {
            "NIX_CC": "/nix/store/x-gcc-wrapper",
            "NIX_BUILD_CORES": "4",
            "PKG_CONFIG_PATH": "/p",
            "CARGO_HOME": "/c",
            "TMPDIR": "/t",
        }
        host = read_host_facts(tmp_path, environ=environ)
        assert host.forwarded_env == environ
        assert host.nix_cc == "/nix/store/x-gcc-wrapper"

    def test_nix_cc_is_empty_when_unset(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        assert read_host_facts(tmp_path, environ={}).nix_cc == ""

    def test_path_entries_follow_the_project_rule(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        root = tmp_path / "proj"
        (root / ".direnv" / "bin").mkdir(parents=True)
        outside = tmp_path / "tools" / "bin"
        outside.mkdir(parents=True)
        raw = f"::.:{root}/.direnv/bin:bin:{outside}:{outside}"
        host = read_host_facts(root, environ={"PATH": raw})
        assert host.path_entries == (str(outside),)

    def test_cc_realpath_is_the_resolved_cc_on_the_constructed_path(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        real = _executable(tmp_path / "store" / "gcc-wrapper" / "bin" / "gcc")
        link_dir = tmp_path / "profile" / "bin"
        link_dir.mkdir(parents=True)
        (link_dir / "cc").symlink_to(real)
        mojo = tmp_path / "venv" / "bin" / "mojo"
        host = read_host_facts(tmp_path / "proj", environ={"PATH": str(link_dir)}, mojo_path=str(mojo))
        assert host.cc_realpath == str(real.resolve())

    def test_the_mojo_dir_comes_first_when_looking_for_cc(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        mojo_cc = _executable(tmp_path / "venv" / "bin" / "cc")
        other = _executable(tmp_path / "other" / "cc")
        host = read_host_facts(
            tmp_path / "proj", environ={"PATH": str(other.parent)}, mojo_path=str(tmp_path / "venv" / "bin" / "mojo")
        )
        assert host.cc_realpath == str(mojo_cc.resolve())

    def test_a_repo_local_cc_reachable_only_through_a_relative_entry_is_not_found(self, tmp_path, monkeypatch):
        from mojox_core.io.environment import read_host_facts

        root = tmp_path / "proj"
        planted = _executable(root / "bin" / "cc")
        monkeypatch.chdir(root)
        for raw in (".", "bin", "./bin", "::", ":bin:", "../proj/bin"):
            host = read_host_facts(root, environ={"PATH": raw})
            assert not host.cc_realpath.startswith(str(root)), raw
            assert str(planted) not in host.path_entries

    def test_a_repo_local_cc_through_an_absolute_entry_in_the_project_is_not_found(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        root = tmp_path / "proj"
        _executable(root / ".direnv" / "bin" / "cc")
        host = read_host_facts(root, environ={"PATH": f"{root}/.direnv/bin"})
        assert not host.cc_realpath.startswith(str(root))

    def test_no_cc_anywhere_gives_an_empty_realpath(self, tmp_path, monkeypatch):
        from mojox_core.io import environment as env_mod

        monkeypatch.setattr(env_mod.shutil, "which", lambda *_args, **_kwargs: None)
        assert env_mod.read_host_facts(tmp_path, environ={"PATH": "/x"}).cc_realpath == ""

    @pytest.mark.parametrize("value", ["./cc", "bin/cc", "../x/cc", "nonexistent-compiler-xyz"])
    def test_cc_and_cxx_that_are_relative_or_unresolvable_are_dropped(self, tmp_path, value):
        from mojox_core.io.environment import read_host_facts

        host = read_host_facts(tmp_path, environ={"PATH": "/usr/bin", "CC": value, "CXX": value})
        assert "CC" not in host.forwarded_env
        assert "CXX" not in host.forwarded_env

    def test_a_whitespace_only_cc_is_dropped(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        host = read_host_facts(tmp_path, environ={"PATH": "/usr/bin", "CC": " ", "CXX": "\t"})
        assert "CC" not in host.forwarded_env
        assert "CXX" not in host.forwarded_env

    def test_a_cc_with_spaces_is_dropped(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        host = read_host_facts(tmp_path / "proj", environ={"PATH": "/usr/bin", "CC": "ccache gcc", "CXX": "ccache g++"})
        assert "CC" not in host.forwarded_env
        assert "CXX" not in host.forwarded_env

    def test_an_absolute_cc_under_a_spaced_directory_is_forwarded(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        gcc = _executable(tmp_path / "my dir" / "gcc")
        host = read_host_facts(tmp_path / "proj", environ={"CC": str(gcc)})
        assert host.forwarded_env == {"CC": str(gcc)}

    def test_an_absolute_cc_that_exists_is_forwarded(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        gcc = _executable(tmp_path / "store" / "bin" / "gcc")
        gxx = _executable(tmp_path / "store" / "bin" / "g++")
        host = read_host_facts(tmp_path / "proj", environ={"CC": str(gcc), "CXX": str(gxx)})
        assert host.forwarded_env == {"CC": str(gcc), "CXX": str(gxx)}

    def test_an_absolute_cc_inside_the_project_is_dropped(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        root = tmp_path / "proj"
        planted = _executable(root / "bin" / "gcc")
        assert "CC" not in read_host_facts(root, environ={"CC": str(planted)}).forwarded_env

    def test_a_missing_absolute_cc_is_dropped(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        assert "CC" not in read_host_facts(tmp_path, environ={"CC": str(tmp_path / "nope" / "gcc")}).forwarded_env

    def test_a_bare_cc_name_that_resolves_on_the_constructed_path_is_forwarded(self, tmp_path):
        from mojox_core.io.environment import read_host_facts

        _executable(tmp_path / "bin" / "gcc")
        host = read_host_facts(tmp_path / "proj", environ={"PATH": str(tmp_path / "bin"), "CC": "gcc"})
        assert host.forwarded_env["CC"] == "gcc"

    def test_a_planned_command_never_carries_a_nix_config_token(self, tmp_path):
        from mojox_core.io.environment import read_host_facts
        from mojox_core.plan import _construct_env
        from mojox_core.types import Toolchain

        environ = {
            "NIX_CONFIG": "access-tokens = github.com=ghp_SECRET",
            "NIX_PATH": "nixpkgs=/secret",
            "NIX_CC": "/nix/store/x-gcc-wrapper",
            "PATH": "/usr/bin",
        }
        toolchain = Toolchain(mojo_path="/venv/bin/mojo", version="1.1.0", subcommand="precompile", extension=".mojoc")
        env = _construct_env(toolchain, read_host_facts(tmp_path, environ=environ, mojo_path=toolchain.mojo_path))
        assert env["NIX_CC"] == "/nix/store/x-gcc-wrapper"
        assert "NIX_CONFIG" not in env
        assert "NIX_PATH" not in env
        assert "ghp_SECRET" not in repr(env)
