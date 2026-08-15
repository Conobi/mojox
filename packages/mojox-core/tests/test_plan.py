"""Build planner: pure function from data to Command tuples."""

from __future__ import annotations

from pathlib import PurePosixPath

from mojox_core._types import (
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
    defaults = {
        "include_sequence": (
            DistEntry(
                name="navette",
                include_dir="/venv/mojo_packages",
                kind=DistKind.PRECOMPILED,
                packages=("navette",),
                provenance="1.0.0",
            ),
        ),
        "mojo_path": "/venv/bin/mojo",
        "mojo_version": "1.0.0b2",
        "path_mojo": None,
        "lock_version": 1,
        "diagnostics": (),
    }
    defaults.update(overrides)
    return ResolvedEnv(**defaults)


def _make_policy(**overrides) -> Policy:
    defaults = {
        "optimize": 0,
        "debug_level": "line-tables",
        "defines": {"ASSERT": "all"},
        "flags": (),
        "include_paths": (),
        "lints": LintConfig(),
        "jobs": 1,
        "jobs_compile": 1,
        "jobs_tests": 1,
        "timeout_s": 300,
    }
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
                Target(TargetKind.LIB, "src/mylib", "src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
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
        import inspect

        import mojox_core.plan as plan_mod

        source = inspect.getsource(plan_mod)
        assert "import subprocess" not in source
        assert "import os" not in source
        assert "os.environ" not in source


class TestCommandGeneration:
    def test_cwd_is_manifest_directory(self):
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        host = _make_host()
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), host)
        for c in cmds:
            assert c.cwd == host.manifest_dir

    def test_argv_starts_with_mojo(self):
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        for c in cmds:
            assert c.argv[0] == "/venv/bin/mojo"

    def test_every_include_path_traces_to_known_source(self):
        env = _make_env()
        pol = _make_policy(include_paths=("/extra/path",))
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
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
                    assert any(path.startswith(s) or path == s for s in valid_sources) or path.startswith(
                        str(_make_host().manifest_dir)
                    ), f"include path {path} traces to no known source"
                    i += 2
                else:
                    i += 1


class TestPrecompilation:
    def test_precompile_precedes_dependent_targets(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "tests/test_b.mojo"),
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
                Target(TargetKind.LIB, "src/mylib", "src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "tests/test_b.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        test_cmds = [c for c in cmds if c.kind == CommandKind.RUN_TEST]
        if any(c.kind == CommandKind.COMPILE_PACKAGE for c in cmds):
            for c in test_cmds:
                argv = list(c.argv)
                i_paths = [argv[i + 1] for i in range(len(argv) - 1) if argv[i] == "-I"]
                assert any(".mojox/build/pkg" in p for p in i_paths), (
                    f"expected precompile output dir in -I paths, got {i_paths}"
                )

    def test_plan_below_threshold_skips_precompile(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        kinds = [c.kind for c in cmds]
        assert CommandKind.COMPILE_PACKAGE not in kinds


class TestThreadDivision:
    def test_num_threads_divided_by_applied_concurrency(self):
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        pol = _make_policy(jobs=1, jobs_tests=4)
        host = HostFacts(cpu_count=8, available_memory_mb=16384, manifest_dir=PurePosixPath("/project"))
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), host)
        for c in cmds:
            if c.kind == CommandKind.RUN_TEST:
                argv = list(c.argv)
                if "--num-threads" in argv:
                    idx = argv.index("--num-threads")
                    threads = int(argv[idx + 1])
                    assert threads == max(1, host.cpu_count // 4)


class TestBinaryOutputName:
    def test_binary_output_uses_configured_name(self):
        graph = TargetGraph(
            targets=(Target(TargetKind.BIN, "src/app.mojo", "myapp"),),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        bin_cmds = [c for c in cmds if c.kind == CommandKind.COMPILE_BINARY]
        assert len(bin_cmds) == 1
        argv = list(bin_cmds[0].argv)
        o_idx = argv.index("-o")
        assert argv[o_idx + 1] == "myapp"


class TestCheckExampleThreads:
    def test_check_example_gets_num_threads(self):
        graph = TargetGraph(
            targets=(Target(TargetKind.EXAMPLE, "examples/demo.mojo", "examples/demo.mojo"),),
            edges=(),
        )
        pol = _make_policy(jobs=1, jobs_compile=4)
        host = HostFacts(cpu_count=8, available_memory_mb=16384, manifest_dir=PurePosixPath("/project"))
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), host)
        for c in cmds:
            if c.kind == CommandKind.CHECK_EXAMPLE:
                argv = list(c.argv)
                assert "--num-threads" in argv
                idx = argv.index("--num-threads")
                threads = int(argv[idx + 1])
                assert threads == max(1, host.cpu_count // 4)


class TestFlagStripping:
    def test_optimize_never_reaches_lib_target(self):
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "tests/test_b.mojo"),
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
                Target(TargetKind.LIB, "src/mylib", "src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "tests/test_b.mojo"),
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
                Target(TargetKind.LIB, "src/mylib", "src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "tests/test_b.mojo"),
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
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        for c in cmds:
            assert isinstance(c.env, dict)
            assert set(c.env.keys()) == {"PATH", "HOME", "MODULAR_DEBUG"}, (
                f"env should contain PATH, HOME, and MODULAR_DEBUG, got {set(c.env.keys())}"
            )
            assert c.env["PATH"] == "/venv/bin:/usr/local/bin:/usr/bin:/bin"
            assert c.env["MODULAR_DEBUG"] == "stack-trace-on-error"


class TestRunArgvOrdering:
    """mojo run treats post-file args as script args, so flags must precede the source file."""

    def test_source_file_is_last_in_run_test_argv(self):
        """All compiler flags (-I, -D, -O, --num-threads) must appear before the source file."""
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        pol = _make_policy(
            optimize=2,
            defines={"ASSERT": "all"},
            include_paths=("/extra/inc",),
        )
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        assert len(cmds) == 1
        argv = list(cmds[0].argv)
        assert argv[-1] == "tests/test_a.mojo", f"source file must be last in argv, got: {argv}"
        file_idx = argv.index("tests/test_a.mojo")
        for flag in ("-I", "-D", "-O2", "--num-threads"):
            if flag in argv:
                assert argv.index(flag) < file_idx, f"{flag} must appear before source file in mojo run argv"

    def test_source_file_after_include_paths_with_precompile(self):
        """When precompilation is active, -I for the pkg dir still precedes the source file."""
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "tests/test_b.mojo"),
            ),
            edges=(),
        )
        cmds = plan(graph, _make_env(), _make_policy(), _make_toolchain(), _make_host())
        for c in cmds:
            if c.kind == CommandKind.RUN_TEST:
                argv = list(c.argv)
                assert argv[-1] == c.target_id.split("::")[-1], f"source file must be last, got: {argv}"


class TestLintFlagTranslation:
    """Lint flags must appear on non-lib commands and never on lib commands."""

    def test_warnings_as_errors_emits_werror(self):
        """Policy with warnings_as_errors=True emits --Werror on test commands."""
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        pol = _make_policy(lints=LintConfig(warnings_as_errors=True))
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        assert len(cmds) == 1
        assert "--Werror" in cmds[0].argv

    def test_check_doc_strings_emits_flag(self):
        """Policy with check_doc_strings=True emits -check-docstrings."""
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        pol = _make_policy(lints=LintConfig(check_doc_strings=True))
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        assert len(cmds) == 1
        assert "-check-docstrings" in cmds[0].argv

    def test_missing_doc_strings_emits_flag(self):
        """Policy with missing_doc_strings=True emits --diagnose-missing-doc-strings."""
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        pol = _make_policy(lints=LintConfig(missing_doc_strings=True))
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        assert len(cmds) == 1
        assert "--diagnose-missing-doc-strings" in cmds[0].argv

    def test_unstable_apis_emits_flag(self):
        """Policy with unstable_apis=True emits --warn-on-unstable-apis."""
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        pol = _make_policy(lints=LintConfig(unstable_apis=True))
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        assert len(cmds) == 1
        assert "--warn-on-unstable-apis" in cmds[0].argv

    def test_lint_flags_never_reach_lib_target(self):
        """Lint flags must never appear in COMPILE_PACKAGE commands."""
        graph = TargetGraph(
            targets=(
                Target(TargetKind.LIB, "src/mylib", "src/mylib"),
                Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
                Target(TargetKind.TEST, "tests/test_b.mojo", "tests/test_b.mojo"),
            ),
            edges=(),
        )
        pol = _make_policy(
            lints=LintConfig(
                warnings_as_errors=True,
                check_doc_strings=True,
                missing_doc_strings=True,
                unstable_apis=True,
            ),
        )
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        lint_flags = {"--Werror", "-check-docstrings", "--diagnose-missing-doc-strings", "--warn-on-unstable-apis"}
        for c in cmds:
            if c.kind == CommandKind.COMPILE_PACKAGE:
                for flag in lint_flags:
                    assert flag not in c.argv, f"lint flag {flag} must not appear in COMPILE_PACKAGE command"

    def test_no_lint_flags_when_all_false(self):
        """Default LintConfig (all False) emits no lint flags."""
        graph = TargetGraph(
            targets=(Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),),
            edges=(),
        )
        pol = _make_policy()  # default LintConfig: all False
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        lint_flags = {"--Werror", "-check-docstrings", "--diagnose-missing-doc-strings", "--warn-on-unstable-apis"}
        for c in cmds:
            for flag in lint_flags:
                assert flag not in c.argv, f"lint flag {flag} should not appear when LintConfig is default"

    def test_lint_flags_on_example_and_binary(self):
        """Lint flags appear on EXAMPLE and BIN target commands."""
        graph = TargetGraph(
            targets=(
                Target(TargetKind.EXAMPLE, "examples/demo.mojo", "examples/demo.mojo"),
                Target(TargetKind.BIN, "src/app.mojo", "myapp"),
            ),
            edges=(),
        )
        pol = _make_policy(lints=LintConfig(warnings_as_errors=True))
        cmds = plan(graph, _make_env(), pol, _make_toolchain(), _make_host())
        assert len(cmds) == 2
        for c in cmds:
            assert "--Werror" in c.argv, f"--Werror missing from {c.kind.name} command"
