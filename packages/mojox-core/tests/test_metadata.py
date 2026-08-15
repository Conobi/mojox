"""Metadata serialization: everything -> JSON dict."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from mojox_core._types import (
    Command,
    CommandKind,
    Diagnostic,
    DistEntry,
    DistKind,
    LintConfig,
    Policy,
    ResolvedEnv,
    Target,
    TargetGraph,
    TargetKind,
    Toolchain,
)
from mojox_core.metadata import serialize


def _make_fixture():
    graph = TargetGraph(
        targets=(
            Target(TargetKind.LIB, "src/mylib", "src/mylib"),
            Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
        ),
        edges=(),
    )
    env = ResolvedEnv(
        include_sequence=(DistEntry("navette", "/venv/mojo_packages", DistKind.PRECOMPILED, ("navette",), "1.0.0"),),
        mojo_path="/venv/bin/mojo",
        mojo_version="1.0.0b2",
        path_mojo=None,
        lock_version=1,
    )
    policy = Policy(
        optimize=0,
        debug_level="line-tables",
        defines={"ASSERT": "all"},
        flags=(),
        include_paths=(),
        lints=LintConfig(),
        jobs=1,
        jobs_compile=1,
        jobs_tests=1,
        timeout_s=300,
    )
    commands = (
        Command(
            argv=("/venv/bin/mojo", "run", "tests/test_a.mojo"),
            cwd=PurePosixPath("/project"),
            env={"PATH": "/venv/bin"},
            kind=CommandKind.RUN_TEST,
            target_id="tests/test_a.mojo",
            timeout_s=300,
            outputs=(),
            depends_on=(),
        ),
    )
    toolchain = Toolchain("/venv/bin/mojo", "1.0.0b2", "precompile", ".mojoc")
    return graph, env, policy, commands, toolchain


class TestSerialization:
    def test_metadata_round_trips_plan(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        doc = serialize(graph, env, policy, commands, toolchain, ())
        assert doc["commands"][0]["argv"] == list(commands[0].argv)

    def test_metadata_is_deterministic(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        doc1 = serialize(graph, env, policy, commands, toolchain, ())
        doc2 = serialize(graph, env, policy, commands, toolchain, ())
        assert json.dumps(doc1) == json.dumps(doc2)

    def test_stdout_is_json_only(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        doc = serialize(graph, env, policy, commands, toolchain, ())
        output = json.dumps(doc)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_metadata_version_present(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        doc = serialize(graph, env, policy, commands, toolchain, ())
        assert "metadata_version" in doc

    def test_toolchain_version_present(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        doc = serialize(graph, env, policy, commands, toolchain, ())
        assert doc["toolchain"]["version"] == "1.0.0b2"

    def test_unstable_pointers_present(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        doc = serialize(graph, env, policy, commands, toolchain, ())
        assert "unstable" in doc
        assert isinstance(doc["unstable"], list)

    def test_diagnostics_included(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        diags = (Diagnostic(kind="warning", message="test warning"),)
        doc = serialize(graph, env, policy, commands, toolchain, diags)
        assert len(doc["diagnostics"]) == 1
        assert doc["diagnostics"][0]["message"] == "test warning"
