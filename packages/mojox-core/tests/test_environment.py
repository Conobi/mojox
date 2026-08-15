"""Environment builder: distribution data -> ResolvedEnv."""

from __future__ import annotations

import pytest
from mojox_core._errors import ConfigError
from mojox_core._types import DistKind
from mojox_core.environment import build_env


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
