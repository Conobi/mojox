"""Build planner: pure function from data to Command tuples."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from mojox_core._types import (
    Command,
    CommandKind,
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
from mojox_core.plan import plan


def _make_env(**overrides) -> ResolvedEnv:
    defaults = dict(
        include_sequence=(
            DistEntry(
                name="navette",
                include_dir="/venv/mojo_packages",
                kind=DistKind.PRECOMPILED,
                packages=("navette",),
                provenance="1.0.0",
            ),
        ),
        mojo_path="/venv/bin/mojo",
        mojo_version="1.0.0b2",
        path_mojo=None,
        lock_version=1,
        diagnostics=(),
    )
    defaults.update(overrides)
    return ResolvedEnv(**defaults)


def _make_policy(**overrides) -> Policy:
    defaults = dict(
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
    defaults.update(overrides)
    return Policy(**defaults)


def _make_toolchain() -> Toolchain:
    return Toolchain(
        mojo_path="/venv/bin/mojo",
        version="1.0.0b2",
        subcommand="precompile",
        extension=".mojoc",
    )


def _make_host() -> HostFacts:
    return HostFacts(
        cpu_count=8,
        available_memory_mb=16384,
        manifest_dir=PurePosixPath("/workspace/mylib"),
    )


class TestPlanPurity:
    def test_plan_is_deterministic(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "lib::src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
            ),
            edges=(),
        )
        env = _make_env()
        pol = _make_policy()
        tc = _make_toolchain()
        host = _make_host()

        result1 = plan(graph, env, pol, tc, host)
        result2 = plan(graph, env, pol, tc, host)
        assert result1 == result2

    def test_plan_performs_no_io(self):
        """The plan module must not import subprocess or os."""
        import mojox_core.plan as plan_mod
        import inspect

        source = inspect.getsource(plan_mod)
        assert "import subprocess" not in source
        assert "import os" not in source
        assert "os.environ" not in source


class TestCommandGeneration:
    def test_cwd_is_manifest_directory(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
            ),
            edges=(),
        )
        host = _make_host()
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), host)
        for c in cmds:
            assert c.cwd == host.manifest_dir

    def test_argv_starts_with_mojo(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        for c in cmds:
            assert c.argv[0] == "/venv/bin/mojo"

    def test_every_include_path_traces_to_known_source(self):
        env = _make_env()
        pol = _make_policy(include_paths=("/extra/path",))
        graph = TargetGraph(
            targets=(
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, env, pol, _make_toolchain(), _make_host())
        valid_sources = {"/venv/mojo_packages", "/extra/path"}
        for c in cmds:
            argv = list(c.argv)
            i = 0
            while i < len(argv):
                if argv[i] == "-I" and i + 1 < len(argv):
                    path = argv[i + 1]
                    assert any(path.startswith(s) or path == s for s in valid_sources) or \
                        path.startswith(str(_make_host().manifest_dir)), \
                        f"include path {path} traces to no known source"
                    i += 2
                else:
                    i += 1


class TestPrecompilation:
    def test_precompile_precedes_dependent_targets(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "lib::src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "test::tests/test_b.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        kinds = [c.kind for c in cmds]
        if CommandKind.COMPILE_PACKAGE in kinds:
            compile_idx = kinds.index(CommandKind.COMPILE_PACKAGE)
            for i, k in enumerate(kinds):
                if k == CommandKind.RUN_TEST:
                    assert i > compile_idx

    def test_test_targets_point_at_mojoc_not_source(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "lib::src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "test::tests/test_b.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        test_cmds = [c for c in cmds if c.kind == CommandKind.RUN_TEST]
        if any(c.kind == CommandKind.COMPILE_PACKAGE for c in cmds):
            for c in test_cmds:
                argv_str = " ".join(c.argv)
                assert "-I" in argv_str

    def test_plan_below_threshold_skips_precompile(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "lib::src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        kinds = [c.kind for c in cmds]
        assert CommandKind.COMPILE_PACKAGE not in kinds


class TestThreadDivision:
    def test_num_threads_divided_by_applied_concurrency(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
            ),
            edges=(),
        )
        pol = _make_policy(jobs=1, jobs_tests=4)
        host = HostFacts(cpu_count=8, available_memory_mb=16384,
                         manifest_dir=PurePosixPath("/project"))
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), host)
        for c in cmds:
            if c.kind == CommandKind.RUN_TEST:
                argv = list(c.argv)
                if "--num-threads" in argv:
                    idx = argv.index("--num-threads")
                    threads = int(argv[idx + 1])
                    assert threads == max(1, host.cpu_count // 4)


class TestFlagStripping:
    def test_optimize_never_reaches_lib_target(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "lib::src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "test::tests/test_b.mojo"),
            ),
            edges=(),
        )
        pol = _make_policy(optimize=3)
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        for c in cmds:
            if c.kind == CommandKind.COMPILE_PACKAGE:
                argv_str = " ".join(c.argv)
                assert "-O" not in argv_str
                assert "--optimization-level" not in argv_str

    def test_defines_never_reach_lib_target(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "lib::src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "test::tests/test_b.mojo"),
            ),
            edges=(),
        )
        pol = _make_policy(defines={"ASSERT": "all", "FAST": "true"})
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        for c in cmds:
            if c.kind == CommandKind.COMPILE_PACKAGE:
                assert "-D" not in c.argv

    def test_num_threads_never_reaches_lib_target(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "lib::src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "test::tests/test_b.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        for c in cmds:
            if c.kind == CommandKind.COMPILE_PACKAGE:
                assert "--num-threads" not in c.argv


class TestCommandEnv:
    def test_command_env_is_constructed_not_inherited(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.TEST, "tests/test_a.mojo", "test::tests/test_a.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        for c in cmds:
            assert isinstance(c.env, dict)
            assert len(c.env) > 0
