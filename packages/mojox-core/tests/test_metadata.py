"""Metadata serialization: everything -> JSON dict."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import PurePosixPath

from mojox_core.metadata import serialize
from mojox_core.types import (
    Command,
    CommandKind,
    Diagnostic,
    DistEntry,
    DistKind,
    HostFacts,
    LintConfig,
    Policy,
    ResolvedEnv,
    Target,
    TargetGraph,
    TargetKind,
    Toolchain,
)


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
        compile_threads=1,
        jobs_spec="1",
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


_LEGACY_POLICY_KEYS = ["optimize", "debug_level", "defines", "flags", "jobs", "jobs_compile", "jobs_tests", "timeout_s"]


class TestParallelism:
    def test_policy_reports_compile_threads_and_jobs_spec(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        doc = serialize(graph, env, policy, commands, toolchain, ())
        assert list(doc["policy"]) == [*_LEGACY_POLICY_KEYS, "compile_threads", "jobs_spec", "test_bundle", "origins"]
        assert doc["policy"]["compile_threads"] == 1
        assert doc["policy"]["jobs_spec"] == "1"

    def test_host_reports_cpu_count_and_source(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        host = HostFacts(
            cpu_count=8,
            available_memory_mb=16384,
            manifest_dir=PurePosixPath("/project"),
            locale_env={"LANG": "C.UTF-8"},
            cpu_count_source="cgroup",
        )
        doc = serialize(graph, env, policy, commands, toolchain, (), host=host)
        assert doc["host"] == {"cpu_count": 8, "cpu_count_source": "cgroup", "forwarded_env": ["PATH"]}

    def test_host_is_null_when_not_given(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        assert serialize(graph, env, policy, commands, toolchain, ())["host"] is None

    def test_forwarded_names_are_listed_without_values(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        secret = "s3cr3t-token-value"
        host = HostFacts(
            cpu_count=2,
            available_memory_mb=1024,
            manifest_dir=PurePosixPath("/project"),
            forwarded_env={"NIX_CC": secret, "TMPDIR": secret, "NIX_CONFIG": secret, "FOO": secret},
            path_entries=(f"/{secret}/bin",),
        )
        leaky = tuple(
            replace(c, env={**c.env, "NIX_CC": secret, "TMPDIR": secret, "PATH": f"/{secret}/bin:/usr/bin"})
            for c in commands
        )
        doc = serialize(graph, env, policy, leaky, toolchain, (), host=host)
        assert doc["host"]["forwarded_env"] == ["NIX_CC", "PATH", "TMPDIR"]
        assert secret not in json.dumps(doc)
        for command in doc["commands"]:
            assert "PATH" not in command["env"]
            assert "NIX_CC" not in command["env"]

    def test_fixed_command_variables_are_still_reported(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        leaky = tuple(
            replace(c, env={"PATH": "/p", "HOME": "", "MODULAR_DEBUG": "x", "LC_ALL": "C.UTF-8"}) for c in commands
        )
        doc = serialize(graph, env, policy, leaky, toolchain, ())
        assert doc["commands"][0]["env"] == {"HOME": "", "MODULAR_DEBUG": "x", "LC_ALL": "C.UTF-8"}

    def test_existing_top_level_keys_keep_their_order(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        keys = [k for k in serialize(graph, env, policy, commands, toolchain, ()) if k != "host"]
        assert keys == [
            "metadata_version",
            "toolchain",
            "environment",
            "policy",
            "targets",
            "commands",
            "commands_mode",
            "diagnostics",
            "plan_complete",
            "unstable",
        ]


class TestOrigins:
    def test_policy_origins_use_lists_for_appended_values(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        policy = replace(
            policy,
            origins={"jobs": "--jobs", "flags": ("tool.mojox.flags", "--flag"), "defines.ASSERT": "builtin:dev"},
        )
        doc = serialize(graph, env, policy, commands, toolchain, ())
        assert isinstance(doc["policy"]["origins"]["flags"], list)
        assert doc["policy"]["origins"] == {
            "jobs": "--jobs",
            "flags": ["tool.mojox.flags", "--flag"],
            "defines.ASSERT": "builtin:dev",
        }

    def test_origins_are_unstable(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        assert "/policy/origins" in serialize(graph, env, policy, commands, toolchain, ())["unstable"]


class TestBundleMode:
    def test_policy_reports_test_bundle(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        doc = serialize(graph, env, replace(policy, test_bundle=True), commands, toolchain, ())
        assert doc["policy"]["test_bundle"] is True

    def test_commands_are_the_per_file_plan_even_in_bundle_mode(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        doc = serialize(graph, env, replace(policy, test_bundle=True), commands, toolchain, ())
        assert doc["commands_mode"] == "files"
        assert [c["target_id"] for c in doc["commands"]] == ["tests/test_a.mojo"]

    def test_bundle_fields_are_stable(self):
        graph, env, policy, commands, toolchain = _make_fixture()
        unstable = serialize(graph, env, policy, commands, toolchain, ())["unstable"]
        assert not {"/commands_mode", "/policy/test_bundle"} & set(unstable)
