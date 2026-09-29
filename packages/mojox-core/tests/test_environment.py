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
