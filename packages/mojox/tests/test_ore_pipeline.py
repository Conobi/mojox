"""Regression tests for ore pipeline bugs found by smoke testing.

Each test class targets a specific bug that unit tests could not catch
because the failure was in argument construction, symbol parsing, or
subprocess interaction logic inside the pipeline.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from mojox_core import Command, CommandKind
from mojox._ore import (
    OreContext,
    OreProbeResult,
    _build_ore,
    _defines_to_dict,
    _extract_planner_flags,
    _extract_user_symbols,
    run_ore_pipeline,
)


# -- Helpers ------------------------------------------------------------------


def _make_probe(**overrides: str | None) -> OreProbeResult:
    """Build an OreProbeResult with all tools available."""
    defaults = {
        "available": True,
        "missing_tool": None,
        "llvm_extract": "/usr/bin/llvm-extract",
        "llc": "/usr/bin/llc",
        "llvm_nm": "/usr/bin/llvm-nm",
        "clang": "/usr/bin/clang",
    }
    defaults.update(overrides)
    return OreProbeResult(**defaults)


def _make_ore_context(
    tmp_path: Path,
    include_paths: tuple[str, ...] = ("/deps/navette/include",),
    **overrides,
) -> OreContext:
    """Build an OreContext with sensible defaults."""
    defaults = {
        "enabled": True,
        "seed": None,
        "include_paths": include_paths,
        "compiler_version": "2025.6.1",
        "mojo_path": "/usr/bin/mojo",
        "runtime_lib_dir": tmp_path / "runtime",
        "dep_versions": (("navette", "0.5.0"),),
    }
    defaults.update(overrides)
    return OreContext(**defaults)


def _make_command(
    source: str = "test_smoke.mojo",
    extra_argv: tuple[str, ...] = (),
    cwd: str = "/workspace",
) -> Command:
    """Build a Command for ore pipeline testing."""
    argv = ("/usr/bin/mojo", "run") + extra_argv + (source,)
    return Command(
        argv=argv,
        cwd=PurePosixPath(cwd),
        env={"PATH": "/usr/bin", "HOME": ""},
        kind=CommandKind.RUN_TEST,
        target_id=f"test::{source}",
        timeout_s=300,
        outputs=(),
        depends_on=(),
    )


class PipelineMock:
    """Reusable subprocess mock for the 7-step ore pipeline.

    Handles file creation for intermediate artifacts and captures
    arguments at specified steps for assertion.

    Steps: 1=mojo build, 2=llvm-nm(user), 3=llvm-nm(ore),
           4=llvm-extract, 5=llc, 6=clang link, 7=execute binary.
    """

    def __init__(
        self,
        *,
        nm_stdout: str = "0000000000001000 T user_main\n",
        step6_exit: int = 0,
        capture_steps: tuple[int, ...] = (),
    ) -> None:
        self.nm_stdout = nm_stdout
        self.step6_exit = step6_exit
        self.capture_steps = capture_steps
        self.captured: dict[int, list[str]] = {s: [] for s in capture_steps}
        self._call_count = 0

    def __call__(self, args, **kwargs):
        self._call_count += 1
        n = self._call_count

        if n in self.capture_steps:
            self.captured[n].extend(args)

        if n == 1:
            bc_path = args[args.index("-o") + 1]
            Path(bc_path).touch()
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        elif n in (2, 3):
            stdout = self.nm_stdout if n == 2 else ""
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        elif n == 4:
            Path(args[-1]).touch()
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        elif n == 5:
            o_idx = args.index("-o")
            Path(args[o_idx + 1]).touch()
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        elif n == 6:
            o_idx = args.index("-o")
            Path(args[o_idx + 1]).touch()
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        elif n == 7:
            return subprocess.CompletedProcess(
                args, self.step6_exit, stdout="output\n", stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


# -- Bug 1: llvm-nm parser with space-containing symbol names ----------------


class TestLlvmNmParserSpaceInSymbols:
    """Mojo's mangled names can contain spaces (e.g. ``operator [](…)``).

    The original parser used ``split()`` which broke on these names,
    truncating at the first space in the symbol. Fixed by using
    ``split(maxsplit=2)`` to capture the full name after address+type.
    """

    def test_symbol_with_spaces_is_fully_captured(self, tmp_path: Path):
        """A symbol like 'Foo::operator [](Int)' must not be truncated."""
        llvm_nm_output = textwrap.dedent("""\
            0000000000001000 T simple_function
            0000000000002000 T Foo::operator [](Int, Int)
            0000000000003000 T Bar::method with spaces()
        """)

        probe = _make_probe()

        with patch("mojox._ore.subprocess.run") as mock_run:
            # First call: llvm-nm on full_bc (user symbols)
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=llvm_nm_output, stderr="",
            )

            full_bc = tmp_path / "full.bc"
            full_bc.touch()
            ore_path = tmp_path / "lib.ore"
            ore_path.touch()

            # With empty ore symbols, all user symbols are returned.
            # Second call returns empty (ore has no symbols).
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    [], 0, stdout=llvm_nm_output, stderr="",
                ),
                subprocess.CompletedProcess(
                    [], 0, stdout="", stderr="",
                ),
            ]

            user_syms = _extract_user_symbols(full_bc, ore_path, probe)

        assert "simple_function" in user_syms
        assert "Foo::operator [](Int, Int)" in user_syms
        assert "Bar::method with spaces()" in user_syms
        # The old parser would have captured "Foo::operator" only.
        assert "Foo::operator" not in user_syms

    def test_symbol_diff_excludes_ore_symbols(self, tmp_path: Path):
        """Symbols present in both full_bc and ore are excluded."""
        user_output = textwrap.dedent("""\
            0000000000001000 T user_main
            0000000000002000 T lib::shared func()
        """)
        ore_output = textwrap.dedent("""\
            0000000000001000 T lib::shared func()
        """)

        probe = _make_probe()

        with patch("mojox._ore.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 0, stdout=user_output, stderr=""),
                subprocess.CompletedProcess([], 0, stdout=ore_output, stderr=""),
            ]

            full_bc = tmp_path / "full.bc"
            full_bc.touch()
            ore_path = tmp_path / "lib.ore"
            ore_path.touch()

            user_syms = _extract_user_symbols(full_bc, ore_path, probe)

        assert user_syms == {"user_main"}


# -- Bug 2: static_string/global_constant treated as globals, not funcs ------


class TestGlobalsHandledViaRglob:
    """static_string_* and global_constant_* are LLVM globals, not functions.

    Passing them via ``--func=`` to ``llvm-extract`` silently fails. They
    must be captured via ``--rglob=static_string`` and ``--rglob=global_constant``.
    """

    def test_static_strings_excluded_from_func_args(self, tmp_path: Path):
        """static_string_* symbols must NOT appear as --func= arguments."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        mock = PipelineMock(
            nm_stdout=(
                "0000000000001000 T user_main\n"
                "0000000000002000 D static_string_42\n"
                "0000000000003000 D global_constant_99\n"
                "0000000000004000 T real_user_func\n"
            ),
            capture_steps=(4,),
        )

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        step3_args = mock.captured[4]
        func_args = [a for a in step3_args if a.startswith("--func=")]
        rglob_args = [a for a in step3_args if a.startswith("--rglob=")]

        for fa in func_args:
            assert "static_string" not in fa, f"static_string in --func: {fa}"
            assert "global_constant" not in fa, f"global_constant in --func: {fa}"

        assert "--rglob=static_string" in rglob_args
        assert "--rglob=global_constant" in rglob_args
        assert "--func=user_main" in func_args
        assert "--func=real_user_func" in func_args


# -- Bug 3: _build_ore strips module-specific main and startup wrappers ------


class TestBuildOreMainStripping:
    """Ore seed build must strip all entry-point variants, not just ``main``.

    Mojo emits ``module::main()``, ``main``, and
    ``__wrap_and_execute_raising_main`` for a module with a ``main()``
    function. Leaving any of these in the .ore causes stale user code
    to execute on subsequent runs.
    """

    def test_strips_module_main_when_seed_module_provided(self, tmp_path: Path):
        """With seed_module='test_smoke', must pass --func=test_smoke::main()."""
        probe = _make_probe()
        seed_bc = tmp_path / "seed.bc"
        seed_bc.touch()
        output = tmp_path / "lib.ore"

        captured_extract_args = []

        def mock_run(args, **kwargs):
            if args[0] == probe.llvm_extract:
                captured_extract_args.extend(args)
                # Create the output file for llvm-extract.
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
            elif args[0] == probe.llc:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            success, stderr = _build_ore(
                seed_bc, probe, output, seed_module="test_smoke",
            )

        assert success is True
        assert "--func=main" in captured_extract_args
        assert "--rfunc=__wrap_and_execute_raising_main" in captured_extract_args
        assert "--func=test_smoke::main()" in captured_extract_args
        assert "--delete" in captured_extract_args

    def test_no_module_main_when_seed_module_empty(self, tmp_path: Path):
        """Without seed_module, only bare main and wrapper are stripped."""
        probe = _make_probe()
        seed_bc = tmp_path / "seed.bc"
        seed_bc.touch()
        output = tmp_path / "lib.ore"

        captured_extract_args = []

        def mock_run(args, **kwargs):
            if args[0] == probe.llvm_extract:
                captured_extract_args.extend(args)
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
            elif args[0] == probe.llc:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            success, _ = _build_ore(seed_bc, probe, output, seed_module="")

        assert success is True
        assert "--func=main" in captured_extract_args
        assert "--rfunc=__wrap_and_execute_raising_main" in captured_extract_args
        # No module-specific main when seed_module is empty.
        module_mains = [a for a in captured_extract_args if "::main()" in a]
        assert module_mains == []


# -- Bug 4: native lib -rpath for lib/ subdirs in include paths --------------


class TestNativeLibRpath:
    """Clang link step must add -L/-rpath for lib/ subdirs in include paths.

    When a dependency ships a native shared library (e.g. librustls_mojo.so)
    in its ``lib/`` directory, the linker needs ``-L`` to find it at link
    time and ``-rpath`` to find it at runtime.
    """

    def test_rpath_added_for_existing_lib_dir(self, tmp_path: Path):
        """A lib/ subdir in include_paths gets -L and -rpath in link args."""
        inc_dir = tmp_path / "deps" / "navette" / "include"
        inc_dir.mkdir(parents=True)
        lib_dir = inc_dir / "lib"
        lib_dir.mkdir()

        ore_context = _make_ore_context(
            tmp_path, include_paths=(str(inc_dir),),
        )
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        mock = PipelineMock(capture_steps=(6,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        link_args = mock.captured[6]
        assert f"-L{lib_dir}" in link_args
        assert f"-Wl,-rpath,{lib_dir}" in link_args

    def test_no_rpath_when_lib_dir_missing(self, tmp_path: Path):
        """No -L/-rpath added when include path has no lib/ subdir."""
        inc_dir = tmp_path / "deps" / "navette" / "include"
        inc_dir.mkdir(parents=True)

        ore_context = _make_ore_context(
            tmp_path, include_paths=(str(inc_dir),),
        )
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        mock = PipelineMock(capture_steps=(6,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        link_args = mock.captured[6]
        lib_dir = inc_dir / "lib"
        assert f"-L{lib_dir}" not in link_args
        assert f"-Wl,-rpath,{lib_dir}" not in link_args


# -- Bug 5: --num-threads forwarding to ore's mojo build --------------------


class TestNumThreadsForwarding:
    """The ore pipeline must forward --num-threads from the original command.

    Without this, ore's step 1 (mojo build --emit llvm-bitcode) uses the
    default thread count, which can differ from the planner's calculation
    and cause inconsistent compilation behavior.
    """

    def test_num_threads_forwarded_to_mojo_build(self, tmp_path: Path):
        """--num-threads N in cmd.argv appears in step 1's mojo build args."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command(extra_argv=("--num-threads", "4"))

        mock = PipelineMock(capture_steps=(1,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        step1_args = mock.captured[1]
        assert "--num-threads" in step1_args
        nt_idx = step1_args.index("--num-threads")
        assert step1_args[nt_idx + 1] == "4"

    def test_no_num_threads_when_absent_from_command(self, tmp_path: Path):
        """When cmd.argv has no --num-threads, step 1 shouldn't either."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        mock = PipelineMock(capture_steps=(1,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        assert "--num-threads" not in mock.captured[1]


# -- Bug 6: -D define forwarding in ore pipeline ----------------------------


class TestDefineForwarding:
    """The ore pipeline must forward -D defines to mojo build.

    Both ``-D KEY=VALUE`` (space-separated) and ``-DKEY=VALUE`` (joined)
    forms must be forwarded.
    """

    def test_defines_forwarded_to_step1(self, tmp_path: Path):
        """Both -D forms are forwarded to ore's mojo build step."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command(extra_argv=("-D", "ASSERT=all", "-DDEBUG=1"))

        mock = PipelineMock(capture_steps=(1,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        step1_args = mock.captured[1]
        d_idx = step1_args.index("-D")
        assert step1_args[d_idx + 1] == "ASSERT=all"
        assert "-DDEBUG=1" in step1_args


# -- Bug 7: source file must be last in ore's mojo build argv ---------------


class TestSourceFileLastInOreBuild:
    """The source file must appear last in ore's mojo build argv.

    ``mojo build`` (like ``mojo run``) treats arguments after the source
    file as source-level arguments, not compiler flags. If -I paths come
    after the source file, they are silently ignored.
    """

    def test_source_file_is_last_arg_in_step1(self, tmp_path: Path):
        """In step 1 (mojo build --emit llvm-bitcode), source is argv[-1]."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command(
            source="my_test.mojo",
            extra_argv=("-I", "/deps/include", "-D", "FOO=1"),
        )

        mock = PipelineMock(capture_steps=(1,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        assert mock.captured[1][-1] == "my_test.mojo"


# -- Bug 8: ore pipeline outcome propagation --------------------------------


class TestOrePipelineOutcomeKinds:
    """Verify correct OutcomeKind for different exit scenarios."""

    def _run_pipeline_with_step6_exit(
        self, tmp_path: Path, exit_code: int,
    ) -> "Outcome":
        """Run the pipeline where step 6 exits with the given code."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        mock = PipelineMock(step6_exit=exit_code)

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            return run_ore_pipeline(cmd, ore_context, probe, ore_path)

    def test_exit_0_is_pass(self, tmp_path: Path):
        """Exit code 0 from the binary produces PASS."""
        from mojox._types import OutcomeKind

        outcome = self._run_pipeline_with_step6_exit(tmp_path, 0)
        assert outcome.kind == OutcomeKind.PASS

    def test_exit_1_is_fail(self, tmp_path: Path):
        """Exit code 1 from the binary produces FAIL."""
        from mojox._types import OutcomeKind

        outcome = self._run_pipeline_with_step6_exit(tmp_path, 1)
        assert outcome.kind == OutcomeKind.FAIL

    def test_negative_exit_is_crash(self, tmp_path: Path):
        """Negative exit code (signal) from the binary produces CRASH."""
        from mojox._types import OutcomeKind

        outcome = self._run_pipeline_with_step6_exit(tmp_path, -11)
        assert outcome.kind == OutcomeKind.CRASH

    def test_compile_error_on_step1_failure(self, tmp_path: Path):
        """Mojo build failure in step 1 produces COMPILE_ERROR."""
        from mojox._types import OutcomeKind

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        def mock_run(args, **kwargs):
            # Step 1 fails.
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="error: compilation failed",
            )

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            outcome = run_ore_pipeline(cmd, ore_context, probe, ore_path)

        assert outcome.kind == OutcomeKind.COMPILE_ERROR


# -- Bug 9: _try_ore_run guard for empty include paths ----------------------


class TestTryOreRunGuards:
    """_try_ore_run returns None (fallback) when preconditions aren't met."""

    def test_returns_none_when_no_include_paths(self):
        """Empty include_paths → no deps → ore is pointless → fallback."""
        from mojox._ore import _try_ore_run

        ctx = OreContext(
            enabled=True,
            seed=None,
            include_paths=(),
            compiler_version="2025.6.1",
            mojo_path="/usr/bin/mojo",
            runtime_lib_dir=Path("/usr/lib"),
            dep_versions=(),
        )
        cmd = _make_command()
        result = _try_ore_run(cmd, ctx)
        assert result is None

    def test_returns_none_when_llvm_tools_missing(self, monkeypatch):
        """Missing LLVM tools → fallback."""
        from mojox._ore import _try_ore_run
        import mojox._ore as ore_mod

        # Reset cached probe so our monkeypatch takes effect.
        monkeypatch.setattr(ore_mod, "_cached_probe", None)
        monkeypatch.setattr(
            "shutil.which", lambda _name: None,
        )

        ctx = OreContext(
            enabled=True,
            seed=None,
            include_paths=("/deps/include",),
            compiler_version="2025.6.1",
            mojo_path="/usr/bin/mojo",
            runtime_lib_dir=Path("/usr/lib"),
            dep_versions=(("pkg", "1.0"),),
        )
        cmd = _make_command()
        result = _try_ore_run(cmd, ctx)
        assert result is None

        # Restore to avoid poisoning other tests.
        monkeypatch.setattr(ore_mod, "_cached_probe", None)


# -- Bug 10: forward ALL planner flags to ore pipeline step 1 ----------------


class TestFlagCompleteness:
    """The ore pipeline must forward ALL planner flags to mojo build.

    The planner emits -O{level}, --debug-level, -I, -D, --num-threads,
    lint flags, and policy.flags. Ore's step 1 must forward all of them.
    Failure to do so silently changes compilation semantics (e.g. compiling
    at -O3 instead of the dev profile's -O0).
    """

    def test_optimization_level_forwarded(self, tmp_path: Path):
        """-O0 from the original command appears in step 1 args."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command(extra_argv=("-O0",))

        mock = PipelineMock(capture_steps=(1,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        assert "-O0" in mock.captured[1]

    def test_debug_level_not_forwarded(self, tmp_path: Path):
        """--debug-level is excluded — Mojo debug metadata is incompatible with system LLVM."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command(extra_argv=("--debug-level", "line-tables"))

        mock = PipelineMock(capture_steps=(1,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        step1 = mock.captured[1]
        assert "--debug-level" not in step1
        assert "line-tables" not in step1

    def test_arbitrary_policy_flags_forwarded(self, tmp_path: Path):
        """Extra flags from policy.flags appear in step 1 args."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command(
            extra_argv=("-O0", "--debug-level", "line-tables", "--some-flag"),
        )

        mock = PipelineMock(capture_steps=(1,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        assert "--some-flag" in mock.captured[1]

    def test_all_dev_profile_flags_forwarded(self, tmp_path: Path):
        """A realistic dev-profile command has all flags in step 1."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command(
            extra_argv=(
                "-O0",
                "--debug-level", "line-tables",
                "-I", "/deps/include",
                "-D", "ASSERT=all",
                "--num-threads", "4",
            ),
        )

        mock = PipelineMock(capture_steps=(1,))

        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        step1 = mock.captured[1]
        assert "-O0" in step1
        assert "--debug-level" not in step1
        assert "-D" in step1
        assert "--num-threads" in step1


# -- Bug 11: forward optimization/debug flags in ore seed build ---------------


class TestBuildAndCacheOre:
    """_build_and_cache_ore must forward all planner flags to the seed build
    and handle failure paths correctly.
    """

    def _run_build_and_cache(
        self,
        tmp_path: Path,
        cmd: Command,
        *,
        seed_compile_exit: int = 0,
        ore_build_exit: int = 0,
        capture_seed_args: bool = False,
    ) -> tuple[Path | None, list[str]]:
        """Run _build_and_cache_ore with mocked subprocess."""
        from mojox._ore import _build_and_cache_ore, OreCache

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        cache = OreCache(cache_dir=tmp_path / "ore-cache")

        captured_args: list[str] = []
        call_count = {"n": 0}

        def mock_run(args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                if capture_seed_args:
                    captured_args.extend(args)
                if seed_compile_exit != 0:
                    return subprocess.CompletedProcess(
                        args, seed_compile_exit, stdout="", stderr="compile error",
                    )
                bc_path = args[args.index("-o") + 1]
                Path(bc_path).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 2:
                if ore_build_exit != 0:
                    return subprocess.CompletedProcess(
                        args, ore_build_exit, stdout="", stderr="extract error",
                    )
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 3:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            result = _build_and_cache_ore(
                cmd, ore_context, probe, cache, "test_key_000000000000",
            )

        return result, captured_args

    def test_seed_build_forwards_optimization_level(self, tmp_path: Path):
        """-O0 from the command appears in the seed's mojo build args."""
        cmd = _make_command(extra_argv=("-O0",))
        _, seed_args = self._run_build_and_cache(
            tmp_path, cmd, capture_seed_args=True,
        )
        assert "-O0" in seed_args

    def test_seed_build_excludes_debug_level(self, tmp_path: Path):
        """--debug-level is excluded from seed build — incompatible with system LLVM."""
        cmd = _make_command(extra_argv=("--debug-level", "line-tables"))
        _, seed_args = self._run_build_and_cache(
            tmp_path, cmd, capture_seed_args=True,
        )
        assert "--debug-level" not in seed_args
        assert "line-tables" not in seed_args

    def test_seed_compile_failure_returns_none(self, tmp_path: Path):
        """Seed compilation failure returns None (fallback)."""
        cmd = _make_command()
        result, _ = self._run_build_and_cache(
            tmp_path, cmd, seed_compile_exit=1,
        )
        assert result is None

    def test_seed_module_derived_from_source_stem(self, tmp_path: Path):
        """seed_module passed to _build_ore is the source filename stem."""
        from mojox._ore import _build_and_cache_ore, OreCache

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        cache = OreCache(cache_dir=tmp_path / "ore-cache")
        cmd = _make_command(source="my_module.mojo")

        captured_build_ore_args: list[str] = []
        call_count = {"n": 0}

        def mock_run(args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                bc_path = args[args.index("-o") + 1]
                Path(bc_path).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 2:
                captured_build_ore_args.extend(args)
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 3:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            _build_and_cache_ore(
                cmd, ore_context, probe, cache, "test_key_000000000001",
            )

        assert "--func=my_module::main()" in captured_build_ore_args

    def test_seed_include_mismatch_returns_none(self, tmp_path: Path):
        """Explicit seed with mismatched -I paths returns None."""
        from mojox._ore import _build_and_cache_ore, OreCache

        ore_context = _make_ore_context(
            tmp_path,
            include_paths=("/path/a",),
            seed=tmp_path / "seed.mojo",
        )
        (tmp_path / "seed.mojo").touch()
        probe = _make_probe()
        cache = OreCache(cache_dir=tmp_path / "ore-cache")
        cmd = _make_command(extra_argv=("-I", "/path/b"))

        with patch("mojox._ore.subprocess.run") as mock_run:
            result = _build_and_cache_ore(
                cmd, ore_context, probe, cache, "test_key_000000000002",
            )

        assert result is None
        mock_run.assert_not_called()


# -- Bug 12: pipeline step failure propagation --------------------------------


class TestPipelineStepFailures:
    """Each pipeline step failure must produce COMPILE_ERROR."""

    def test_step3_llvm_extract_failure(self, tmp_path: Path):
        """llvm-extract failure in step 3 → COMPILE_ERROR."""
        from mojox._types import OutcomeKind

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        call_count = {"n": 0}

        def mock_run(args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                bc_path = args[args.index("-o") + 1]
                Path(bc_path).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] in (2, 3):
                return subprocess.CompletedProcess(
                    args, 0,
                    stdout="0000000000001000 T user_main\n",
                    stderr="",
                )
            elif call_count["n"] == 4:
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="llvm-extract: error",
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            outcome = run_ore_pipeline(cmd, ore_context, probe, ore_path)

        assert outcome.kind == OutcomeKind.COMPILE_ERROR

    def test_step4_llc_failure(self, tmp_path: Path):
        """llc failure in step 4 → COMPILE_ERROR."""
        from mojox._types import OutcomeKind

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        call_count = {"n": 0}

        def mock_run(args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                bc_path = args[args.index("-o") + 1]
                Path(bc_path).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] in (2, 3):
                return subprocess.CompletedProcess(
                    args, 0,
                    stdout="0000000000001000 T user_main\n",
                    stderr="",
                )
            elif call_count["n"] == 4:
                Path(args[-1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 5:
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="llc: error",
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            outcome = run_ore_pipeline(cmd, ore_context, probe, ore_path)

        assert outcome.kind == OutcomeKind.COMPILE_ERROR

    def test_step5_clang_link_failure(self, tmp_path: Path):
        """clang link failure in step 5 → COMPILE_ERROR."""
        from mojox._types import OutcomeKind

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        call_count = {"n": 0}

        def mock_run(args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                bc_path = args[args.index("-o") + 1]
                Path(bc_path).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] in (2, 3):
                return subprocess.CompletedProcess(
                    args, 0,
                    stdout="0000000000001000 T user_main\n",
                    stderr="",
                )
            elif call_count["n"] == 4:
                Path(args[-1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 5:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 6:
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="clang: error: linker failed",
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            outcome = run_ore_pipeline(cmd, ore_context, probe, ore_path)

        assert outcome.kind == OutcomeKind.COMPILE_ERROR


# -- Edge cases in the ore pipeline -------------------------------------------


class TestPipelineEdgeCases:
    """Edge cases in the ore pipeline."""

    def test_step6_timeout(self, tmp_path: Path):
        """Binary timeout in step 6 → TIMEOUT outcome."""
        from mojox._types import OutcomeKind

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        call_count = {"n": 0}

        def mock_run(args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                bc_path = args[args.index("-o") + 1]
                Path(bc_path).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] in (2, 3):
                return subprocess.CompletedProcess(
                    args, 0,
                    stdout="0000000000001000 T user_main\n",
                    stderr="",
                )
            elif call_count["n"] == 4:
                Path(args[-1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 5:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 6:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 7:
                raise subprocess.TimeoutExpired(args, 300, output=b"partial", stderr=b"")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            outcome = run_ore_pipeline(cmd, ore_context, probe, ore_path)

        assert outcome.kind == OutcomeKind.TIMEOUT

    def test_zero_user_symbols_produces_valid_step3(self, tmp_path: Path):
        """When all symbols are in the ore, step 3 has no --func= args."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()
        cmd = _make_command()

        nm_output = "0000000000001000 T lib_func\n"

        mock = PipelineMock(
            nm_stdout=nm_output,
            capture_steps=(4,),
        )
        original_call = mock.__call__

        def patched_call(args, **kwargs):
            mock._call_count += 1
            n = mock._call_count
            if n in mock.capture_steps:
                mock.captured[n].extend(args)
            if n in (2, 3):
                return subprocess.CompletedProcess(
                    args, 0, stdout=nm_output, stderr="",
                )
            mock._call_count -= 1
            return original_call(args, **kwargs)

        with patch("mojox._ore.subprocess.run", side_effect=patched_call):
            outcome = run_ore_pipeline(cmd, ore_context, probe, ore_path)

        step3_args = mock.captured[4]
        func_args = [a for a in step3_args if a.startswith("--func=")]
        assert func_args == []
        assert "--rglob=static_string" in step3_args
        assert "--rglob=global_constant" in step3_args


# -- Invariant 5: argv-completeness ------------------------------------------


class TestArgvCompleteness:
    """Every planner flag must survive the ore pipeline's flag extraction.

    Instead of checking hardcoded flag names, this test compares the
    set of flags in the original command against those in ore's step 1,
    accounting for the expected differences (verb change, -I from
    ore_context, source file position).
    """

    def test_all_source_flags_appear_in_step1(self, tmp_path: Path):
        """Step 1 argv contains every flag from the source command."""
        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        ore_path = tmp_path / "lib.ore"
        ore_path.touch()

        cmd = _make_command(
            extra_argv=(
                "-O0",
                "--debug-level", "line-tables",
                "-I", "/deps/include",
                "-D", "ASSERT=all",
                "--num-threads", "4",
                "--some-lint-flag",
                "--custom-policy-flag",
            ),
        )

        mock = PipelineMock(capture_steps=(1,))
        with patch("mojox._ore.subprocess.run", side_effect=mock):
            run_ore_pipeline(cmd, ore_context, probe, ore_path)

        step1 = mock.captured[1]

        source_flags = set(cmd.argv) - {cmd.argv[0], cmd.argv[1], cmd.argv[-1]}
        # -I paths handled via ore_context; --debug-level excluded (LLVM incompatibility)
        source_flags -= {"-I", "/deps/include", "--debug-level", "line-tables"}

        for flag in source_flags:
            assert flag in step1, f"planner flag {flag!r} missing from ore step 1"

    def test_extract_planner_flags_roundtrips(self):
        """_extract_planner_flags captures every flag type the planner emits."""
        argv = (
            "/usr/bin/mojo", "run",
            "-O0",
            "--debug-level", "line-tables",
            "-I", "/deps/include",
            "-D", "ASSERT=all",
            "-DDEBUG=1",
            "--num-threads", "4",
            "--some-lint-flag",
            "test.mojo",
        )
        defines, extra = _extract_planner_flags(argv, "test.mojo")

        assert "-D" in defines
        assert "ASSERT=all" in defines
        assert "-DDEBUG=1" in defines
        assert "-O0" in extra
        assert "--num-threads" in extra
        assert "4" in extra
        assert "--some-lint-flag" in extra
        # -I, source file, and --debug-level must NOT appear
        assert "-I" not in defines and "-I" not in extra
        assert "/deps/include" not in defines and "/deps/include" not in extra
        assert "test.mojo" not in defines and "test.mojo" not in extra
        assert "--debug-level" not in defines and "--debug-level" not in extra
        assert "line-tables" not in defines and "line-tables" not in extra


# -- Missing coverage: define forwarding to seed build -----------------------


class TestSeedDefineForwarding:
    """_build_and_cache_ore must forward -D defines to the seed compilation."""

    def test_defines_forwarded_to_seed_build(self, tmp_path: Path):
        """-D ASSERT=all from the command appears in seed mojo build args."""
        from mojox._ore import _build_and_cache_ore, OreCache

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        cache = OreCache(cache_dir=tmp_path / "ore-cache")
        cmd = _make_command(extra_argv=("-D", "ASSERT=all", "-DDEBUG=1"))

        captured_args: list[str] = []
        call_count = {"n": 0}

        def mock_run(args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                captured_args.extend(args)
                bc_path = args[args.index("-o") + 1]
                Path(bc_path).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 2:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 3:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            _build_and_cache_ore(
                cmd, ore_context, probe, cache, "test_key_seed_defines",
            )

        d_idx = captured_args.index("-D")
        assert captured_args[d_idx + 1] == "ASSERT=all"
        assert "-DDEBUG=1" in captured_args

    def test_num_threads_forwarded_to_seed_build(self, tmp_path: Path):
        """--num-threads from the command appears in seed build args."""
        from mojox._ore import _build_and_cache_ore, OreCache

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        cache = OreCache(cache_dir=tmp_path / "ore-cache")
        cmd = _make_command(extra_argv=("--num-threads", "4"))

        captured_args: list[str] = []
        call_count = {"n": 0}

        def mock_run(args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                captured_args.extend(args)
                bc_path = args[args.index("-o") + 1]
                Path(bc_path).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 2:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 3:
                o_idx = args.index("-o")
                Path(args[o_idx + 1]).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            _build_and_cache_ore(
                cmd, ore_context, probe, cache, "test_key_seed_threads",
            )

        assert "--num-threads" in captured_args
        nt_idx = captured_args.index("--num-threads")
        assert captured_args[nt_idx + 1] == "4"


# -- Missing coverage: _try_ore_run full orchestration -----------------------


class TestTryOreRunOrchestration:
    """_try_ore_run cache-miss → _build_and_cache_ore → run_ore_pipeline."""

    def test_cache_miss_builds_and_runs(self, tmp_path: Path, monkeypatch):
        """On cache miss, _try_ore_run builds .ore, caches it, and runs."""
        from mojox._types import OutcomeKind
        from mojox._ore import _try_ore_run
        import mojox._ore as ore_mod

        monkeypatch.setattr(ore_mod, "_cached_probe", _make_probe())

        ctx = _make_ore_context(tmp_path)
        cmd = _make_command(cwd=str(tmp_path))

        call_count = {"n": 0}
        phase = {"current": "seed"}

        def mock_run(args, **kwargs):
            call_count["n"] += 1

            if phase["current"] == "seed":
                if call_count["n"] == 1:
                    bc_path = args[args.index("-o") + 1]
                    Path(bc_path).touch()
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                elif call_count["n"] == 2:
                    o_idx = args.index("-o")
                    Path(args[o_idx + 1]).touch()
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                elif call_count["n"] == 3:
                    o_idx = args.index("-o")
                    Path(args[o_idx + 1]).touch()
                    phase["current"] = "pipeline"
                    call_count["n"] = 0
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            if phase["current"] == "pipeline":
                if call_count["n"] == 1:
                    bc_path = args[args.index("-o") + 1]
                    Path(bc_path).touch()
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                elif call_count["n"] in (2, 3):
                    return subprocess.CompletedProcess(
                        args, 0,
                        stdout="0000000000001000 T user_main\n",
                        stderr="",
                    )
                elif call_count["n"] == 4:
                    Path(args[-1]).touch()
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                elif call_count["n"] == 5:
                    o_idx = args.index("-o")
                    Path(args[o_idx + 1]).touch()
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                elif call_count["n"] == 6:
                    o_idx = args.index("-o")
                    Path(args[o_idx + 1]).touch()
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                elif call_count["n"] == 7:
                    return subprocess.CompletedProcess(
                        args, 0, stdout="hello from ore\n", stderr="",
                    )

            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            outcome = _try_ore_run(cmd, ctx)

        assert outcome is not None
        assert outcome.kind == OutcomeKind.PASS
        assert "hello from ore" in outcome.stdout

        cache_dir = tmp_path / ".mojox" / "cache" / "ore"
        assert cache_dir.exists()

        monkeypatch.setattr(ore_mod, "_cached_probe", None)


# -- _defines_to_dict unit tests --------------------------------------------


class TestDefinesToDict:
    """_defines_to_dict converts raw define arg lists to dicts."""

    def test_empty_list(self):
        """Empty input returns empty dict."""
        assert _defines_to_dict([]) == {}

    def test_space_separated_form(self):
        """-D KEY=VALUE → {KEY: VALUE}."""
        assert _defines_to_dict(["-D", "ASSERT=all"]) == {"ASSERT": "all"}

    def test_joined_form(self):
        """-DKEY=VALUE → {KEY: VALUE}."""
        assert _defines_to_dict(["-DDEBUG=1"]) == {"DEBUG": "1"}

    def test_mixed_forms(self):
        """Both forms in one list."""
        result = _defines_to_dict(["-D", "ASSERT=all", "-DDEBUG=1"])
        assert result == {"ASSERT": "all", "DEBUG": "1"}

    def test_define_without_equals_skipped(self):
        """A define without '=' is silently skipped."""
        assert _defines_to_dict(["-D", "NOEQUALS"]) == {}

    def test_bare_d_at_end_of_list(self):
        """Bare -D at end of list is silently skipped."""
        assert _defines_to_dict(["-D"]) == {}

    def test_value_with_equals(self):
        """Value containing '=' is preserved."""
        result = _defines_to_dict(["-D", "PATH=/usr/bin:/bin"])
        assert result == {"PATH": "/usr/bin:/bin"}


# -- _build_and_cache_ore ore-build failure ----------------------------------


class TestBuildAndCacheOreFailure:
    """_build_and_cache_ore returns None when _build_ore fails."""

    def test_ore_build_failure_returns_none(self, tmp_path: Path):
        """_build_ore failure (llvm-extract/llc) returns None."""
        from mojox._ore import _build_and_cache_ore, OreCache

        ore_context = _make_ore_context(tmp_path)
        probe = _make_probe()
        cache = OreCache(cache_dir=tmp_path / "ore-cache")
        cmd = _make_command()

        call_count = {"n": 0}

        def mock_run(args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                bc_path = args[args.index("-o") + 1]
                Path(bc_path).touch()
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            elif call_count["n"] == 2:
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="llvm-extract failed",
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("mojox._ore.subprocess.run", side_effect=mock_run):
            result = _build_and_cache_ore(
                cmd, ore_context, probe, cache, "test_key_ore_build_fail",
            )

        assert result is None
