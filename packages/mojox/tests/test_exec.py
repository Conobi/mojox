"""Exec runner: Command → Outcome via subprocess."""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath

import pytest
from mojox.cache import read_cache_meta, stamp_include_dirs, write_cache_meta
from mojox.exec import (
    CacheContext,
    _build_test_cache_key,
    _extract_test_source_and_flags,
    _precompile_cache_key,
    run_cached_precompile,
    run_cached_test,
    run_command,
    run_commands,
)
from mojox.types import Outcome, OutcomeKind
from mojox_core import Command, CommandKind


def _cmd(argv: tuple[str, ...], **overrides) -> Command:
    """Build a test command with sensible defaults."""
    defaults = {
        "argv": argv,
        "cwd": PurePosixPath("."),
        "env": {"PATH": "/usr/bin", "HOME": ""},
        "kind": CommandKind.RUN_TEST,
        "target_id": "t.mojo",
        "timeout_s": 30,
        "outputs": (),
        "depends_on": (),
    }
    defaults.update(overrides)
    return Command(**defaults)


class TestRunCommand:
    def test_successful_command(self):
        cmd = _cmd((sys.executable, "-c", "print('hello')"))
        outcome = run_command(cmd)
        assert outcome.kind == OutcomeKind.PASS
        assert outcome.exit_code == 0
        assert "hello" in outcome.stdout
        assert outcome.elapsed_s > 0

    def test_failing_command(self):
        cmd = _cmd((sys.executable, "-c", "import sys; sys.exit(1)"))
        outcome = run_command(cmd)
        assert outcome.kind == OutcomeKind.FAIL
        assert outcome.exit_code == 1

    def test_stderr_captured(self):
        cmd = _cmd((sys.executable, "-c", "import sys; print('err', file=sys.stderr)"))
        outcome = run_command(cmd)
        assert outcome.kind == OutcomeKind.PASS
        assert "err" in outcome.stderr

    def test_timeout_produces_timeout_outcome(self):
        cmd = _cmd(
            (sys.executable, "-c", "import time; time.sleep(60)"),
            timeout_s=1,
        )
        outcome = run_command(cmd)
        assert outcome.kind == OutcomeKind.TIMEOUT
        assert outcome.exit_code is None

    def test_env_is_constructed_not_inherited(self):
        cmd = _cmd(
            (sys.executable, "-c", "import os; print(os.environ.get('MOJOX_TEST_MARKER', 'absent'))"),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": "", "MOJOX_TEST_MARKER": "present"},
        )
        outcome = run_command(cmd)
        assert "present" in outcome.stdout

    def test_command_with_no_timeout(self):
        cmd = _cmd(
            (sys.executable, "-c", "print('ok')"),
            timeout_s=None,
        )
        outcome = run_command(cmd)
        assert outcome.kind == OutcomeKind.PASS

    def test_extra_env_merged(self):
        cmd = _cmd(
            (sys.executable, "-c", "import os; print(os.environ.get('EXTRA', 'missing'))"),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
        )
        outcome = run_command(cmd, extra_env={"EXTRA": "found"})
        assert "found" in outcome.stdout

    def test_command_not_found(self):
        cmd = _cmd(("/nonexistent/binary",))
        outcome = run_command(cmd)
        assert outcome.kind == OutcomeKind.COMPILE_ERROR
        assert outcome.exit_code is None

    @pytest.mark.skipif(sys.platform == "win32", reason="signals not available on Windows")
    def test_signal_death_produces_crash_outcome(self):
        """A process killed by a signal produces a CRASH outcome."""
        cmd = _cmd((sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"))
        outcome = run_command(cmd)
        assert outcome.kind == OutcomeKind.CRASH
        assert outcome.exit_code is not None
        assert outcome.exit_code < 0


class TestRunCommands:
    def test_empty_command_list(self):
        """Empty input returns empty output."""
        results = run_commands(())
        assert results == ()

    def test_sequential_execution(self):
        """Multiple independent commands all execute."""
        cmd1 = _cmd((sys.executable, "-c", "print('one')"), target_id="one")
        cmd2 = _cmd((sys.executable, "-c", "print('two')"), target_id="two")
        results = run_commands((cmd1, cmd2), max_workers=1)
        assert len(results) == 2
        assert results[0].kind == OutcomeKind.PASS
        assert results[1].kind == OutcomeKind.PASS

    def test_depends_on_ordering(self):
        """Commands with depends_on wait for dependencies to complete."""
        precompile = _cmd(
            (sys.executable, "-c", "import time; time.sleep(0.1); print('precompiled')"),
            kind=CommandKind.COMPILE_PACKAGE,
            target_id="mylib",
        )
        test = _cmd(
            (sys.executable, "-c", "print('tested')"),
            target_id="t.mojo",
            depends_on=("mylib",),
        )
        results = run_commands((precompile, test), max_workers=2)
        assert len(results) == 2
        assert results[0].command.target_id == "mylib"
        assert results[1].command.target_id == "t.mojo"
        assert results[0].kind == OutcomeKind.PASS
        assert results[1].kind == OutcomeKind.PASS

    def test_failure_in_dependency_skips_dependents(self):
        """When a dependency fails, its dependents are skipped."""
        precompile = _cmd(
            (sys.executable, "-c", "import sys; sys.exit(1)"),
            kind=CommandKind.COMPILE_PACKAGE,
            target_id="mylib",
        )
        test = _cmd(
            (sys.executable, "-c", "print('should not run')"),
            target_id="t.mojo",
            depends_on=("mylib",),
        )
        results = run_commands((precompile, test), max_workers=2)
        assert results[0].kind == OutcomeKind.FAIL
        assert results[1].kind == OutcomeKind.SKIPPED
        assert "dependency" in results[1].stderr.lower()

    def test_concurrent_independent_commands(self):
        """Independent commands run concurrently."""
        cmds = tuple(
            _cmd(
                (sys.executable, "-c", f"print({i})"),
                target_id=f"t{i}.mojo",
            )
            for i in range(4)
        )
        results = run_commands(cmds, max_workers=4)
        assert len(results) == 4
        assert all(r.kind == OutcomeKind.PASS for r in results)

    def test_extra_env_passed_through(self):
        """extra_env is forwarded to each command."""
        cmd = _cmd(
            (sys.executable, "-c", "import os; print(os.environ.get('MY_VAR', 'absent'))"),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
        )
        results = run_commands((cmd,), extra_env={"MY_VAR": "present"})
        assert "present" in results[0].stdout


class TestOutputDirectoryCreation:
    """Output parent directories must be created before execution."""

    def test_output_dirs_created(self, tmp_path: Path):
        """Parent directories for Command.outputs are created automatically."""
        out_file = tmp_path / "deep" / "nested" / "pkg" / "lib.mojoc"
        cmd = _cmd(
            (sys.executable, "-c", "print('ok')"),
            outputs=(str(out_file),),
        )
        outcome = run_command(cmd)
        assert outcome.kind == OutcomeKind.PASS
        assert out_file.parent.is_dir()

    def test_relative_output_dir_created_under_command_cwd(self, tmp_path: Path):
        """Relative outputs resolve against ``cmd.cwd``, not the process cwd,
        and exist before the command runs."""
        out_dir = tmp_path / ".mojox" / "cache" / "examples"
        cmd = _cmd(
            (sys.executable, "-c", f"import os, sys; sys.exit(0 if os.path.isdir({str(out_dir)!r}) else 1)"),
            cwd=PurePosixPath(tmp_path),
            outputs=(".mojox/cache/examples/main-0123abcd",),
        )
        outcome = run_command(cmd)
        assert outcome.kind == OutcomeKind.PASS


class TestFailFast:
    def test_fail_fast_cancels_remaining(self):
        """After first failure, remaining queued commands are SKIPPED."""
        fast_fail = _cmd(
            (sys.executable, "-c", "import sys; sys.exit(1)"),
            target_id="fail.mojo",
        )
        cmds = (fast_fail,) + tuple(
            _cmd(
                (sys.executable, "-c", "import time; time.sleep(2)"),
                target_id=f"t{i}.mojo",
            )
            for i in range(5)
        )
        results = run_commands(cmds, max_workers=1, fail_fast=True)
        skipped = [r for r in results if r.kind == OutcomeKind.SKIPPED]
        assert len(skipped) >= 1
        assert results[0].kind == OutcomeKind.FAIL

    def test_no_fail_fast_runs_all(self):
        """Without fail-fast, all commands run even after failure."""
        cmds = (
            _cmd((sys.executable, "-c", "import sys; sys.exit(1)"), target_id="f1.mojo"),
            _cmd((sys.executable, "-c", "print('ok')"), target_id="t1.mojo"),
        )
        results = run_commands(cmds, max_workers=1, fail_fast=False)
        assert results[0].kind == OutcomeKind.FAIL
        assert results[1].kind == OutcomeKind.PASS

    def test_fail_fast_default_is_false(self):
        """Default fail_fast parameter is False (backward compatible)."""
        cmds = (
            _cmd((sys.executable, "-c", "import sys; sys.exit(1)"), target_id="f.mojo"),
            _cmd((sys.executable, "-c", "print('ok')"), target_id="t.mojo"),
        )
        results = run_commands(cmds, max_workers=1)
        assert results[1].kind == OutcomeKind.PASS

    def test_fail_fast_skips_phase2_after_phase1_failure(self):
        """If fail-fast triggers in phase 1, phase 2 commands are SKIPPED."""
        compile_fail = _cmd(
            (sys.executable, "-c", "import sys; sys.exit(1)"),
            kind=CommandKind.COMPILE_PACKAGE,
            target_id="mylib",
        )
        compile_ok = _cmd(
            (sys.executable, "-c", "print('ok')"),
            kind=CommandKind.COMPILE_PACKAGE,
            target_id="otherlib",
        )
        test = _cmd(
            (sys.executable, "-c", "print('test')"),
            target_id="t.mojo",
            depends_on=("mylib",),
        )
        results = run_commands(
            (compile_fail, compile_ok, test),
            max_workers=1,
            fail_fast=True,
        )
        assert results[2].kind == OutcomeKind.SKIPPED


class TestOnStartCallback:
    def test_on_start_called_for_each_command(self):
        """on_start fires once per executed command."""
        started: list[str] = []
        cmds = (
            _cmd((sys.executable, "-c", "print('a')"), target_id="a.mojo"),
            _cmd((sys.executable, "-c", "print('b')"), target_id="b.mojo"),
        )
        run_commands(
            cmds,
            max_workers=1,
            on_start=lambda cmd: started.append(cmd.target_id),
        )
        assert set(started) == {"a.mojo", "b.mojo"}

    def test_on_start_not_called_for_skipped(self):
        """SKIPPED commands do not fire on_start."""
        started: list[str] = []
        compile_fail = _cmd(
            (sys.executable, "-c", "import sys; sys.exit(1)"),
            kind=CommandKind.COMPILE_PACKAGE,
            target_id="mylib",
        )
        test = _cmd(
            (sys.executable, "-c", "print('test')"),
            target_id="t.mojo",
            depends_on=("mylib",),
        )
        run_commands(
            (compile_fail, test),
            max_workers=1,
            on_start=lambda cmd: started.append(cmd.target_id),
        )
        assert "mylib" in started
        assert "t.mojo" not in started


class TestNativeLibPathInjection:
    """Standard mojo run path must set LD_LIBRARY_PATH for native libs.

    The subprocess path must find native shared libraries shipped by
    dependencies in their lib/ subdirectories.
    """

    def test_ld_library_path_injected_for_native_libs(self, tmp_path: Path):
        """lib/ subdirs from include_paths appear in LD_LIBRARY_PATH."""
        inc_dir = tmp_path / "deps" / "navette"
        lib_dir = inc_dir / "lib"
        lib_dir.mkdir(parents=True)

        cmd = _cmd(
            (sys.executable, "-c", "import os; print(os.environ.get('LD_LIBRARY_PATH', ''))"),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
        )
        outcome = run_command(cmd, include_paths=(str(inc_dir),))
        assert outcome.kind == OutcomeKind.PASS
        assert str(lib_dir) in outcome.stdout

    def test_no_ld_library_path_when_no_lib_dirs(self, tmp_path: Path):
        """Without lib/ subdirs, LD_LIBRARY_PATH is not injected."""
        inc_dir = tmp_path / "deps" / "navette"
        inc_dir.mkdir(parents=True)

        cmd = _cmd(
            (sys.executable, "-c", "import os; print(os.environ.get('LD_LIBRARY_PATH', 'NONE'))"),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
        )
        outcome = run_command(cmd, include_paths=(str(inc_dir),))
        assert outcome.kind == OutcomeKind.PASS
        assert "NONE" in outcome.stdout

    def test_ld_library_path_appended_to_existing(self, tmp_path: Path):
        """New lib dirs are appended to any existing LD_LIBRARY_PATH."""
        inc_dir = tmp_path / "deps" / "navette"
        lib_dir = inc_dir / "lib"
        lib_dir.mkdir(parents=True)

        cmd = _cmd(
            (sys.executable, "-c", "import os; print(os.environ.get('LD_LIBRARY_PATH', ''))"),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": "", "LD_LIBRARY_PATH": "/existing/lib"},
        )
        outcome = run_command(cmd, include_paths=(str(inc_dir),))
        assert outcome.kind == OutcomeKind.PASS
        assert "/existing/lib" in outcome.stdout
        assert str(lib_dir) in outcome.stdout

    def test_multiple_lib_dirs_all_included(self, tmp_path: Path):
        """Multiple include paths with lib/ dirs all appear."""
        inc1 = tmp_path / "deps" / "navette"
        lib1 = inc1 / "lib"
        lib1.mkdir(parents=True)
        inc2 = tmp_path / "deps" / "boucle"
        lib2 = inc2 / "lib"
        lib2.mkdir(parents=True)

        cmd = _cmd(
            (sys.executable, "-c", "import os; print(os.environ.get('LD_LIBRARY_PATH', ''))"),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
        )
        outcome = run_command(cmd, include_paths=(str(inc1), str(inc2)))
        assert outcome.kind == OutcomeKind.PASS
        assert str(lib1) in outcome.stdout
        assert str(lib2) in outcome.stdout


def _build_test_cmd(
    build_argv: tuple[str, ...],
    output_path: str,
    **overrides,
) -> Command:
    """Build a BUILD_TEST command with sensible defaults.

    Args:
        build_argv: The argv for the build step (e.g. a Python script
            that creates the binary).
        output_path: Path to the expected compiled binary.
        **overrides: Additional Command field overrides.

    Returns:
        A Command with ``kind=BUILD_TEST`` and ``outputs=(output_path,)``.
    """
    defaults = {
        "argv": (*build_argv, "-o", output_path),
        "cwd": PurePosixPath("."),
        "env": {"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
        "kind": CommandKind.BUILD_TEST,
        "target_id": "test_hello",
        "timeout_s": 30,
        "outputs": (output_path,),
        "depends_on": (),
    }
    defaults.update(overrides)
    return Command(**defaults)


class TestRunCachedTest:
    """Tests for the compound build+execute path with caching."""

    def test_cache_miss_builds_and_executes(self, tmp_path: Path):
        """On a cache miss the build runs, binary executes, PASS returned."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"

        # Build script: reads -o from argv to find output path
        build_script = (
            "import stat, pathlib, sys\n"
            "idx = sys.argv.index('-o')\n"
            "p = pathlib.Path(sys.argv[idx + 1])\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\necho hello-from-binary\\n')\n"
            "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
        )

        cmd = _build_test_cmd(
            (sys.executable, "-c", build_script),
            str(binary),
        )
        outcome = run_cached_test(
            cmd,
            cache_key="abc123",
            meta_dir=meta_dir,
            compiler_version="mojo-test-1.0",
        )

        assert outcome.kind == OutcomeKind.PASS
        assert "hello-from-binary" in outcome.stdout
        # Cache metadata should now exist
        assert (meta_dir / "test_hello.json").exists()

    def test_cache_hit_skips_build(self, tmp_path: Path):
        """On a cache hit the build command is NOT executed."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"

        # Pre-create the binary
        binary.write_text("#!/bin/sh\necho cached-result\n")
        binary.chmod(0o755)

        # Pre-create matching cache metadata
        write_cache_meta(
            meta_dir / "test_hello.json",
            cache_key="match-key",
            compiler_version="mojo-test-1.0",
        )

        # The build command should NOT run — use a command that would
        # fail if executed
        cmd = _build_test_cmd(
            ("/nonexistent/should-not-run",),
            str(binary),
        )
        outcome = run_cached_test(
            cmd,
            cache_key="match-key",
            meta_dir=meta_dir,
            compiler_version="mojo-test-1.0",
        )

        assert outcome.kind == OutcomeKind.PASS
        assert "cached-result" in outcome.stdout

    def test_build_failure_returns_compile_error(self, tmp_path: Path):
        """When the build exits non-zero, a COMPILE_ERROR outcome is returned."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"

        cmd = _build_test_cmd(
            (sys.executable, "-c", "import sys; sys.exit(1)"),
            str(binary),
        )
        outcome = run_cached_test(
            cmd,
            cache_key="abc123",
            meta_dir=meta_dir,
            compiler_version="mojo-test-1.0",
        )

        assert outcome.kind == OutcomeKind.COMPILE_ERROR
        # No metadata should be written on failure
        assert not (meta_dir / "test_hello.json").exists()

    def test_missing_binary_after_build(self, tmp_path: Path):
        """Build exits 0 but binary missing produces COMPILE_ERROR."""
        binary = tmp_path / "nonexistent_binary"
        meta_dir = tmp_path / "meta"

        # Build script succeeds but does NOT create the binary
        cmd = _build_test_cmd(
            (sys.executable, "-c", "print('built nothing')"),
            str(binary),
        )
        outcome = run_cached_test(
            cmd,
            cache_key="abc123",
            meta_dir=meta_dir,
            compiler_version="mojo-test-1.0",
        )

        assert outcome.kind == OutcomeKind.COMPILE_ERROR
        assert "binary not found" in outcome.stderr.lower()

    def test_timeout_shared_budget(self, tmp_path: Path):
        """The timeout budget is shared between build and execute steps."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"

        # Build script: sleeps 1s, then creates a binary that sleeps 30s
        build_script = (
            "import time, stat, pathlib, sys\n"
            "time.sleep(1)\n"
            "idx = sys.argv.index('-o')\n"
            "p = pathlib.Path(sys.argv[idx + 1])\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\nsleep 30\\n')\n"
            "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
        )

        cmd = _build_test_cmd(
            (sys.executable, "-c", build_script),
            str(binary),
            timeout_s=4,
        )
        outcome = run_cached_test(
            cmd,
            cache_key="abc123",
            meta_dir=meta_dir,
            compiler_version="mojo-test-1.0",
        )

        # The execute step should time out because the build consumed
        # part of the 4-second budget
        assert outcome.kind == OutcomeKind.TIMEOUT
        assert outcome.elapsed_s < 6  # should not run full 30s

    def test_build_warnings_prepended_to_stderr(self, tmp_path: Path):
        """Build stderr (warnings) appears before execution stderr."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"

        # Build script: emits a warning on stderr, then creates binary
        # that also writes to stderr
        build_script = (
            "import sys, stat, pathlib\n"
            "print('BUILD-WARNING', file=sys.stderr)\n"
            "idx = sys.argv.index('-o')\n"
            "p = pathlib.Path(sys.argv[idx + 1])\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\necho EXEC-STDERR >&2\\necho ok\\n')\n"
            "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
        )

        cmd = _build_test_cmd(
            (sys.executable, "-c", build_script),
            str(binary),
        )
        outcome = run_cached_test(
            cmd,
            cache_key="abc123",
            meta_dir=meta_dir,
            compiler_version="mojo-test-1.0",
        )

        assert outcome.kind == OutcomeKind.PASS
        assert "BUILD-WARNING" in outcome.stderr
        assert "EXEC-STDERR" in outcome.stderr
        # Build warnings come first
        build_pos = outcome.stderr.index("BUILD-WARNING")
        exec_pos = outcome.stderr.index("EXEC-STDERR")
        assert build_pos < exec_pos


class TestCacheContext:
    """Tests for the CacheContext dataclass."""

    def test_construction_with_defaults(self, tmp_path: Path):
        """CacheContext can be constructed; enabled defaults to True."""
        ctx = CacheContext(
            project_hash="abc",
            tests_tree_hash="def",
            deps_stamp="ccc",
            compiler_version="25.4.0",
            meta_dir=tmp_path / "meta",
        )
        assert ctx.project_hash == "abc"
        assert ctx.tests_tree_hash == "def"
        assert ctx.compiler_version == "25.4.0"
        assert ctx.meta_dir == tmp_path / "meta"
        assert ctx.enabled is True

    def test_construction_disabled(self, tmp_path: Path):
        """CacheContext can be constructed with enabled=False."""
        ctx = CacheContext(
            project_hash="abc",
            tests_tree_hash="def",
            deps_stamp="ccc",
            compiler_version="25.4.0",
            meta_dir=tmp_path / "meta",
            enabled=False,
        )
        assert ctx.enabled is False

    def test_frozen(self, tmp_path: Path):
        """CacheContext is immutable."""
        ctx = CacheContext(
            project_hash="abc",
            tests_tree_hash="def",
            deps_stamp="ccc",
            compiler_version="25.4.0",
            meta_dir=tmp_path / "meta",
        )
        with pytest.raises(AttributeError):
            ctx.project_hash = "new"  # type: ignore[misc]


class TestExtractTestSourceAndFlags:
    """Tests for _extract_test_source_and_flags against the planner's argv layout."""

    def test_typical_build_command(self):
        """``<mojo> build <src> -o <out> <flags...>``: source is argv[2], flags follow -o."""
        argv = ("/usr/bin/mojo", "build", "tests/test_hello.mojo", "-o", ".mojox/cache/bin/t", "-O0")
        source, flags = _extract_test_source_and_flags(argv)
        assert source == "tests/test_hello.mojo"
        assert flags == ("-O0",)

    def test_flags_keep_order(self):
        """-I order is first-match-wins, so flags are returned unsorted."""
        argv = ("/usr/bin/mojo", "build", "t.mojo", "-o", "out", "-I", "/b", "-I", "/a")
        _source, flags = _extract_test_source_and_flags(argv)
        assert flags == ("-I", "/b", "-I", "/a")

    def test_no_flags(self):
        argv = ("/usr/bin/mojo", "build", "test.mojo", "-o", "out")
        source, flags = _extract_test_source_and_flags(argv)
        assert source == "test.mojo"
        assert flags == ()

    def test_define_ending_in_mojo_does_not_hijack_source(self):
        argv = ("/usr/bin/mojo", "build", "tests/test_a.mojo", "-o", "out", "-D", "FOO=x.mojo")
        source, flags = _extract_test_source_and_flags(argv)
        assert source == "tests/test_a.mojo"
        assert flags == ("-D", "FOO=x.mojo")

    @pytest.mark.parametrize(
        "argv",
        [
            ("/usr/bin/mojo", "build", "-O0", "t.mojo", "-o", "out"),
            ("/usr/bin/mojo", "run", "t.mojo", "-o", "out"),
            ("/usr/bin/mojo", "build", "t.mojo", "out"),
            ("/usr/bin/mojo", "build", "t.txt", "-o", "out"),
            ("/usr/bin/mojo", "build"),
        ],
    )
    def test_malformed_argv_rejected(self, argv):
        with pytest.raises(ValueError):
            _extract_test_source_and_flags(argv)


def _planner_cmd(tmp_path: Path, *flags: str, mojo: str = "/opt/mojo/bin/mojo") -> Command:
    """A BUILD_TEST command shaped exactly like the planner's."""
    src = tmp_path / "tests" / "test_a.mojo"
    src.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        src.write_text("def test_a(): pass")
    return Command(
        argv=(mojo, "build", "tests/test_a.mojo", "-o", ".mojox/cache/bin/test_a", *flags),
        cwd=PurePosixPath(str(tmp_path)),
        env={"PATH": "/opt/mojo/bin:/usr/bin", "HOME": ""},
        kind=CommandKind.BUILD_TEST,
        target_id="tests/test_a.mojo",
        timeout_s=30,
        outputs=(".mojox/cache/bin/test_a",),
        depends_on=(),
    )


def _ctx(tmp_path: Path, include_dirs: tuple[str, ...]) -> CacheContext:
    """A CacheContext whose dependency stamp is taken now, as the CLI does once per run."""
    return CacheContext(
        project_hash="aaa",
        tests_tree_hash="bbb",
        deps_stamp=stamp_include_dirs(include_dirs),
        compiler_version="1.0.0",
        meta_dir=tmp_path / "meta",
    )


class TestBuildTestCacheKey:
    """What the executor feeds into the cache key for a BUILD_TEST command."""

    def _dep(self, tmp_path: Path) -> Path:
        dep = tmp_path / "dep"
        (dep / "navette").mkdir(parents=True)
        (dep / "navette" / "__init__.mojo").write_text("fn f(): pass")
        return dep

    def test_unchanged_env_gives_identical_key(self, tmp_path: Path):
        dep = self._dep(tmp_path)
        cmd = _planner_cmd(tmp_path, "-I", str(dep))
        k1 = _build_test_cache_key(cmd, _ctx(tmp_path, (str(dep),)), {"X": "1"})
        k2 = _build_test_cache_key(cmd, _ctx(tmp_path, (str(dep),)), {"X": "1"})
        assert k1 is not None
        assert k1 == k2

    def test_dependency_edit_changes_key(self, tmp_path: Path):
        dep = self._dep(tmp_path)
        cmd = _planner_cmd(tmp_path, "-I", str(dep))
        before = _build_test_cache_key(cmd, _ctx(tmp_path, (str(dep),)), None)
        (dep / "navette" / "__init__.mojo").write_text("fn f(): return")
        after = _build_test_cache_key(cmd, _ctx(tmp_path, (str(dep),)), None)
        assert before != after

    def test_include_reorder_changes_key(self, tmp_path: Path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        ab = _planner_cmd(tmp_path, "-I", str(a), "-I", str(b))
        ba = _planner_cmd(tmp_path, "-I", str(b), "-I", str(a))
        ctx = _ctx(tmp_path, (str(a), str(b)))
        assert _build_test_cache_key(ab, ctx, None) != _build_test_cache_key(ba, ctx, None)

    def test_missing_include_dir_gives_stable_key(self, tmp_path: Path):
        missing = str(tmp_path / "not-there")
        cmd = _planner_cmd(tmp_path, "-I", missing)
        k1 = _build_test_cache_key(cmd, _ctx(tmp_path, (missing,)), None)
        k2 = _build_test_cache_key(cmd, _ctx(tmp_path, (missing,)), None)
        assert k1 is not None
        assert k1 == k2

    def test_settings_env_change_changes_key(self, tmp_path: Path):
        cmd = _planner_cmd(tmp_path)
        ctx = _ctx(tmp_path, ())
        assert _build_test_cache_key(cmd, ctx, {"MODULAR_X": "1"}) != _build_test_cache_key(
            cmd, ctx, {"MODULAR_X": "2"}
        )
        assert _build_test_cache_key(cmd, ctx, None) != _build_test_cache_key(cmd, ctx, {"MODULAR_X": "1"})

    def test_mojo_path_change_changes_key(self, tmp_path: Path):
        ctx = _ctx(tmp_path, ())
        k1 = _build_test_cache_key(_planner_cmd(tmp_path, mojo="/opt/a/mojo"), ctx, None)
        k2 = _build_test_cache_key(_planner_cmd(tmp_path, mojo="/opt/b/mojo"), ctx, None)
        assert k1 != k2

    def test_define_ending_in_mojo_keys_on_real_source(self, tmp_path: Path):
        """The key must hash tests/test_a.mojo, not the file named by a -D value."""
        cmd = _planner_cmd(tmp_path, "-D", "FOO=x.mojo")
        ctx = _ctx(tmp_path, ())
        before = _build_test_cache_key(cmd, ctx, None)
        (tmp_path / "tests" / "test_a.mojo").write_text("def test_a(): return")
        assert _build_test_cache_key(cmd, ctx, None) != before

    def test_unreadable_source_is_uncacheable(self, tmp_path: Path):
        """A vanished or directory source is left to the compiler to report."""
        cmd = _planner_cmd(tmp_path)
        src = tmp_path / "tests" / "test_a.mojo"
        src.unlink()
        assert _build_test_cache_key(cmd, _ctx(tmp_path, ()), None) is None
        src.mkdir()
        assert _build_test_cache_key(cmd, _ctx(tmp_path, ()), None) is None

    def test_malformed_argv_is_uncacheable(self, tmp_path: Path):
        cmd = _planner_cmd(tmp_path)
        bad = Command(
            argv=(cmd.argv[0], "build", "-O0", *cmd.argv[2:]),
            cwd=cmd.cwd,
            env=cmd.env,
            kind=cmd.kind,
            target_id=cmd.target_id,
            timeout_s=cmd.timeout_s,
            outputs=cmd.outputs,
            depends_on=cmd.depends_on,
        )
        assert _build_test_cache_key(bad, _ctx(tmp_path, ()), None) is None


class TestRunCommandsWithCache:
    """Tests for run_commands routing BUILD_TEST through cached path."""

    def test_build_test_with_cache_context_builds_and_executes(self, tmp_path: Path):
        """BUILD_TEST commands with cache_context do compound build+execute."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"
        test_source = tmp_path / "test_hello.mojo"
        test_source.write_text("fn main(): pass")

        build_script = (
            "import stat, pathlib, sys\n"
            "idx = sys.argv.index('-o')\n"
            "p = pathlib.Path(sys.argv[idx + 1])\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\necho cached-build-test\\n')\n"
            "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
        )

        cmd = Command(
            argv=(sys.executable, "-c", build_script, str(test_source), "-o", str(binary)),
            cwd=PurePosixPath(str(tmp_path)),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
            kind=CommandKind.BUILD_TEST,
            target_id="test_hello",
            timeout_s=30,
            outputs=(str(binary),),
            depends_on=(),
        )

        ctx = CacheContext(
            project_hash="aaa",
            tests_tree_hash="bbb",
            deps_stamp="ccc",
            compiler_version="25.4.0",
            meta_dir=meta_dir,
        )

        results = run_commands((cmd,), cache_context=ctx)
        assert len(results) == 1
        assert results[0].kind == OutcomeKind.PASS
        assert "cached-build-test" in results[0].stdout

    def test_build_test_without_cache_context_runs_build_only(self, tmp_path: Path):
        """BUILD_TEST without cache_context runs via run_command (build only)."""
        binary = tmp_path / "test_hello"

        cmd = Command(
            argv=(sys.executable, "-c", "print('built-ok')"),
            cwd=PurePosixPath(str(tmp_path)),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
            kind=CommandKind.BUILD_TEST,
            target_id="test_hello",
            timeout_s=30,
            outputs=(str(binary),),
            depends_on=(),
        )

        results = run_commands((cmd,))
        assert len(results) == 1
        # Without cache_context, runs as a normal command (just the build)
        assert results[0].kind == OutcomeKind.PASS
        assert "built-ok" in results[0].stdout

    def test_build_test_with_cache_disabled_always_rebuilds(self, tmp_path: Path):
        """With enabled=False, BUILD_TEST always rebuilds (no cache hits)."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"
        test_source = tmp_path / "test_hello.mojo"
        test_source.write_text("fn main(): pass")

        build_script = (
            "import stat, pathlib, sys\n"
            "idx = sys.argv.index('-o')\n"
            "p = pathlib.Path(sys.argv[idx + 1])\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\necho no-cache-run\\n')\n"
            "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
        )

        cmd = Command(
            argv=(sys.executable, "-c", build_script, str(test_source), "-o", str(binary)),
            cwd=PurePosixPath(str(tmp_path)),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
            kind=CommandKind.BUILD_TEST,
            target_id="test_hello",
            timeout_s=30,
            outputs=(str(binary),),
            depends_on=(),
        )

        ctx = CacheContext(
            project_hash="aaa",
            tests_tree_hash="bbb",
            deps_stamp="ccc",
            compiler_version="25.4.0",
            meta_dir=meta_dir,
            enabled=False,
        )

        results = run_commands((cmd,), cache_context=ctx)
        assert len(results) == 1
        assert results[0].kind == OutcomeKind.PASS
        assert "no-cache-run" in results[0].stdout

    def test_on_start_fires_for_cached_build_test(self, tmp_path: Path):
        """on_start callback fires before the build step for cached BUILD_TEST."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"
        test_source = tmp_path / "test_hello.mojo"
        test_source.write_text("fn main(): pass")

        build_script = (
            "import stat, pathlib, sys\n"
            "idx = sys.argv.index('-o')\n"
            "p = pathlib.Path(sys.argv[idx + 1])\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\necho ok\\n')\n"
            "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
        )

        cmd = Command(
            argv=(sys.executable, "-c", build_script, str(test_source), "-o", str(binary)),
            cwd=PurePosixPath(str(tmp_path)),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
            kind=CommandKind.BUILD_TEST,
            target_id="test_hello",
            timeout_s=30,
            outputs=(str(binary),),
            depends_on=(),
        )

        ctx = CacheContext(
            project_hash="aaa",
            tests_tree_hash="bbb",
            deps_stamp="ccc",
            compiler_version="25.4.0",
            meta_dir=meta_dir,
        )

        started: list[str] = []
        run_commands(
            (cmd,),
            cache_context=ctx,
            on_start=lambda c: started.append(c.target_id),
        )
        assert "test_hello" in started

    def test_non_build_test_ignores_cache_context(self):
        """Non-BUILD_TEST commands are unaffected by cache_context."""
        cmd = _cmd(
            (sys.executable, "-c", "print('normal')"),
            kind=CommandKind.RUN_TEST,
            target_id="t.mojo",
        )
        ctx = CacheContext(
            project_hash="aaa",
            tests_tree_hash="bbb",
            deps_stamp="ccc",
            compiler_version="25.4.0",
            meta_dir=Path("/tmp/meta"),
        )
        results = run_commands((cmd,), cache_context=ctx)
        assert results[0].kind == OutcomeKind.PASS
        assert "normal" in results[0].stdout


class TestRuntimeArgs:
    """Tests for runtime_args threading through the exec layer."""

    def test_cache_context_default_runtime_args(self):
        """CacheContext.runtime_args defaults to empty tuple."""
        ctx = CacheContext(
            project_hash="abc",
            tests_tree_hash="def",
            deps_stamp="ccc",
            compiler_version="1.0.0",
            meta_dir=Path("/meta"),
        )
        assert ctx.runtime_args == ()

    def test_cache_context_with_runtime_args(self):
        """CacheContext accepts runtime_args."""
        ctx = CacheContext(
            project_hash="abc",
            tests_tree_hash="def",
            deps_stamp="ccc",
            compiler_version="1.0.0",
            meta_dir=Path("/meta"),
            runtime_args=("--filter", "test_foo"),
        )
        assert ctx.runtime_args == ("--filter", "test_foo")

    def test_runtime_args_reach_binary(self, tmp_path: Path):
        """runtime_args are appended to the binary's argv at execution time."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"

        # Build script that creates a shell script printing its arguments
        build_script = (
            "import stat, pathlib, sys\n"
            "idx = sys.argv.index('-o')\n"
            "p = pathlib.Path(sys.argv[idx + 1])\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\necho \"ARGS:$@\"\\n')\n"
            "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
        )

        cmd = _build_test_cmd(
            (sys.executable, "-c", build_script),
            str(binary),
        )
        outcome = run_cached_test(
            cmd,
            cache_key="abc123",
            meta_dir=meta_dir,
            compiler_version="mojo-test-1.0",
            runtime_args=("--filter", "test_foo"),
        )

        assert outcome.kind == OutcomeKind.PASS
        assert "--filter" in outcome.stdout
        assert "test_foo" in outcome.stdout

    def test_runtime_args_reach_binary_on_cache_hit(self, tmp_path: Path):
        """runtime_args are passed to the binary even on a cache hit."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"

        # Pre-create binary that prints its arguments
        binary.write_text('#!/bin/sh\necho "ARGS:$@"\n')
        binary.chmod(0o755)

        # Pre-create matching cache metadata
        write_cache_meta(
            meta_dir / "test_hello.json",
            cache_key="match-key",
            compiler_version="mojo-test-1.0",
        )

        cmd = _build_test_cmd(
            ("/nonexistent/should-not-run",),
            str(binary),
        )
        outcome = run_cached_test(
            cmd,
            cache_key="match-key",
            meta_dir=meta_dir,
            compiler_version="mojo-test-1.0",
            runtime_args=("-k", "test_bar"),
        )

        assert outcome.kind == OutcomeKind.PASS
        assert "-k" in outcome.stdout
        assert "test_bar" in outcome.stdout

    def test_runtime_args_threaded_through_cache_context(self, tmp_path: Path):
        """runtime_args from CacheContext reach the binary via run_commands."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"
        test_source = tmp_path / "test_hello.mojo"
        test_source.write_text("fn main(): pass")

        build_script = (
            "import stat, pathlib, sys\n"
            "idx = sys.argv.index('-o')\n"
            "p = pathlib.Path(sys.argv[idx + 1])\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\necho \"ARGS:$@\"\\n')\n"
            "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
        )

        cmd = Command(
            argv=(sys.executable, "-c", build_script, str(test_source), "-o", str(binary)),
            cwd=PurePosixPath(str(tmp_path)),
            env={"PATH": f"{sys.prefix}/bin:/usr/bin:/bin", "HOME": ""},
            kind=CommandKind.BUILD_TEST,
            target_id="test_hello",
            timeout_s=30,
            outputs=(str(binary),),
            depends_on=(),
        )

        ctx = CacheContext(
            project_hash="aaa",
            tests_tree_hash="bbb",
            deps_stamp="ccc",
            compiler_version="25.4.0",
            meta_dir=meta_dir,
            runtime_args=("-k", "test_something"),
        )

        results = run_commands((cmd,), cache_context=ctx)
        assert len(results) == 1
        assert results[0].kind == OutcomeKind.PASS
        assert "-k" in results[0].stdout
        assert "test_something" in results[0].stdout

    def test_empty_runtime_args_no_extra_argv(self, tmp_path: Path):
        """Empty runtime_args (default) adds nothing to the binary's argv."""
        binary = tmp_path / "test_hello"
        meta_dir = tmp_path / "meta"

        build_script = (
            "import stat, pathlib, sys\n"
            "idx = sys.argv.index('-o')\n"
            "p = pathlib.Path(sys.argv[idx + 1])\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\necho \"ARGS:$@\"\\n')\n"
            "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
        )

        cmd = _build_test_cmd(
            (sys.executable, "-c", build_script),
            str(binary),
        )
        outcome = run_cached_test(
            cmd,
            cache_key="abc123",
            meta_dir=meta_dir,
            compiler_version="mojo-test-1.0",
        )

        assert outcome.kind == OutcomeKind.PASS
        assert outcome.stdout.strip() == "ARGS:"


class TestTextFileBusyRetry:
    """A freshly written binary can transiently fail execve with ETXTBSY."""

    @staticmethod
    def _flaky_run(monkeypatch, failures: int | None) -> list[list[str]]:
        """Make subprocess.run raise ETXTBSY *failures* times (None: always)."""
        import errno
        import subprocess

        import mojox.exec as exec_mod

        calls: list[list[str]] = []
        real_run = subprocess.run

        def flaky(argv, **kwargs):
            calls.append(list(argv))
            if failures is None or len(calls) <= failures:
                raise OSError(errno.ETXTBSY, "Text file busy", argv[0])
            return real_run(argv, **kwargs)

        monkeypatch.setattr(exec_mod.subprocess, "run", flaky)
        monkeypatch.setattr(exec_mod.time, "sleep", lambda _s: None)
        return calls

    def test_transient_etxtbsy_is_retried(self, monkeypatch):
        calls = self._flaky_run(monkeypatch, failures=2)
        outcome = run_command(_cmd((sys.executable, "-c", "print('ok')")))
        assert outcome.kind == OutcomeKind.PASS, outcome.stderr
        assert outcome.stdout.strip() == "ok"
        assert len(calls) == 3

    def test_persistent_etxtbsy_gives_up(self, monkeypatch):
        import mojox.exec as exec_mod

        calls = self._flaky_run(monkeypatch, failures=None)
        outcome = run_command(_cmd((sys.executable, "-c", "pass")))
        assert outcome.kind == OutcomeKind.COMPILE_ERROR
        assert "Text file busy" in outcome.stderr
        assert f"after {exec_mod._ETXTBSY_ATTEMPTS} attempts" in outcome.stderr
        assert len(calls) == exec_mod._ETXTBSY_ATTEMPTS

    def test_other_oserror_is_not_retried(self, monkeypatch):
        import errno

        import mojox.exec as exec_mod

        calls: list[object] = []

        def denied(argv, **kwargs):
            calls.append(argv)
            raise OSError(errno.EACCES, "Permission denied", argv[0])

        monkeypatch.setattr(exec_mod.subprocess, "run", denied)
        outcome = run_command(_cmd((sys.executable, "-c", "pass")))
        assert outcome.kind == OutcomeKind.COMPILE_ERROR
        assert "Permission denied" in outcome.stderr
        assert len(calls) == 1


_RECORDING_BUILD = (
    "import stat, pathlib, sys, time\n"
    "idx = sys.argv.index('-o')\n"
    "p = pathlib.Path(sys.argv[idx + 1])\n"
    "with open(sys.argv[1], 'a') as log:\n"
    "    log.write(str(p) + '\\n')\n"
    "time.sleep(0.2)\n"
    "p.parent.mkdir(parents=True, exist_ok=True)\n"
    "p.write_text('#!/bin/sh\\necho built-' + sys.argv[2] + '\\n')\n"
    "p.chmod(p.stat().st_mode | stat.S_IEXEC)\n"
)
"""Fake ``mojo build``: logs its ``-o`` path to argv[1], then writes a
binary echoing ``built-<argv[2]>``. The sleep widens the race window."""


def _recording_cmd(log: Path, tag: str, binary: Path) -> Command:
    """A BUILD_TEST command running :data:`_RECORDING_BUILD`."""
    return _build_test_cmd(
        (sys.executable, "-c", _RECORDING_BUILD, str(log), tag),
        str(binary),
    )


class TestTempPathIsolation:
    """Concurrent builds in one process must never share a temp output."""

    def test_concurrent_builds_use_distinct_temp_paths(self, tmp_path: Path, monkeypatch):
        """All builders hold their temp output at once (barrier), so none may share it."""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        import mojox.exec as exec_mod

        workers = 6
        binary = tmp_path / "bin" / "test_x-deadbeef"
        meta_dir = tmp_path / "meta"
        barrier = threading.Barrier(workers, timeout=10)
        temps: list[str] = []
        lock = threading.Lock()
        real_run_command = exec_mod.run_command

        def fake_run_command(cmd, **kwargs):
            if cmd.kind != CommandKind.BUILD_TEST:
                return real_run_command(cmd, **kwargs)
            out = Path(cmd.argv[cmd.argv.index("-o") + 1])
            out.write_text(f"#!/bin/sh\necho built-{cmd.target_id}\n")
            out.chmod(0o755)
            with lock:
                temps.append(str(out))
            # Write before the barrier: no thread forks for its exec while
            # another still holds a write fd, mirroring the real compiler,
            # which writes from its own process.
            barrier.wait()
            return Outcome(cmd, OutcomeKind.PASS, 0, "", "", (), 0.0)

        monkeypatch.setattr(exec_mod, "run_command", fake_run_command)

        def build(i: int):
            return run_cached_test(
                _build_test_cmd(("fake-mojo",), str(binary), target_id=str(i)),
                cache_key=f"key-{i}",
                meta_dir=meta_dir,
                compiler_version="v",
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(build, range(workers)))

        assert all(o.kind == OutcomeKind.PASS for o in outcomes), [o.stderr for o in outcomes]
        assert len(temps) == workers
        assert len(set(temps)) == workers
        for t in temps:
            assert Path(t).parent == binary.parent
            assert not Path(t).exists()
        assert sorted(p.name for p in binary.parent.iterdir()) == [binary.name]

    def test_published_binary_is_executable_without_builder_chmod(self, tmp_path: Path):
        """Don't rely on the compiler resetting the 0600 mode mkstemp gives the temp."""
        import os

        binary = tmp_path / "bin" / "test_x-deadbeef"
        script = (
            "import sys\n"
            "p = sys.argv[sys.argv.index('-o') + 1]\n"
            "open(p, 'w').write('#!/bin/sh\\necho no-chmod\\n')\n"
        )
        cmd = _build_test_cmd((sys.executable, "-c", script), str(binary))
        outcome = run_cached_test(cmd, cache_key="k", meta_dir=tmp_path / "meta", compiler_version="v")
        assert outcome.kind == OutcomeKind.PASS, outcome.stderr
        assert outcome.stdout.strip() == "no-chmod"
        assert os.access(binary, os.X_OK)

    def test_missing_binary_message_names_checked_temp(self, tmp_path: Path):
        binary = tmp_path / "bin" / "test_x-deadbeef"
        cmd = _build_test_cmd((sys.executable, "-c", "pass"), str(binary))
        outcome = run_cached_test(cmd, cache_key="k", meta_dir=tmp_path / "meta", compiler_version="v")
        assert f"{binary.parent}/.{binary.name}." in outcome.stderr

    def test_no_cache_concurrent_builds_use_distinct_temp_paths(self, tmp_path: Path):
        from concurrent.futures import ThreadPoolExecutor

        binary = tmp_path / "bin" / "test_x-deadbeef"
        log = tmp_path / "log"

        def build(i: int):
            return run_cached_test(
                _recording_cmd(log, str(i), binary),
                cache_key=f"key-{i}",
                meta_dir=tmp_path / "meta",
                compiler_version="v",
                skip_cache_write=True,
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(build, range(4)))

        assert [o.stdout.strip() for o in outcomes] == [f"built-{i}" for i in range(4)]
        temps = log.read_text().split()
        assert len(set(temps)) == 4
        assert not any(Path(t).exists() for t in temps)
        assert not binary.exists()
        assert not (tmp_path / "meta").exists()

    def test_temp_removed_on_build_failure(self, tmp_path: Path):
        binary = tmp_path / "bin" / "test_x-deadbeef"
        script = (
            "import sys\n"
            "idx = sys.argv.index('-o')\n"
            "open(sys.argv[1], 'w').write(sys.argv[idx + 1])\n"
            "sys.exit(1)\n"
        )
        log = tmp_path / "log"
        cmd = _build_test_cmd((sys.executable, "-c", script, str(log)), str(binary))
        outcome = run_cached_test(cmd, cache_key="k", meta_dir=tmp_path / "meta", compiler_version="v")
        assert outcome.kind == OutcomeKind.COMPILE_ERROR
        assert not Path(log.read_text()).exists()
        assert list(binary.parent.iterdir()) == []

    def test_temp_removed_when_build_produces_nothing(self, tmp_path: Path):
        binary = tmp_path / "bin" / "test_x-deadbeef"
        cmd = _build_test_cmd((sys.executable, "-c", "pass"), str(binary))
        outcome = run_cached_test(cmd, cache_key="k", meta_dir=tmp_path / "meta", compiler_version="v")
        assert outcome.kind == OutcomeKind.COMPILE_ERROR
        assert "binary not found" in outcome.stderr.lower()
        assert list(binary.parent.iterdir()) == []

    def test_no_cache_temp_dir_removed_on_build_failure(self, tmp_path: Path):
        binary = tmp_path / "bin" / "test_x-deadbeef"
        script = (
            "import sys\n"
            "idx = sys.argv.index('-o')\n"
            "open(sys.argv[1], 'w').write(sys.argv[idx + 1])\n"
            "sys.exit(1)\n"
        )
        log = tmp_path / "log"
        cmd = _build_test_cmd((sys.executable, "-c", script, str(log)), str(binary))
        outcome = run_cached_test(
            cmd, cache_key="k", meta_dir=tmp_path / "meta", compiler_version="v", skip_cache_write=True
        )
        assert outcome.kind == OutcomeKind.COMPILE_ERROR
        assert not Path(log.read_text()).parent.exists()


class TestAtomicPublish:
    """A binary must never be observable next to a meta describing another build."""

    def _stale_cache(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Seed a binary + matching meta under key ``old``."""
        binary = tmp_path / "bin" / "test_x-deadbeef"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho stale\n")
        binary.chmod(0o755)
        meta_dir = tmp_path / "meta"
        write_cache_meta(meta_dir / f"{binary.name}.json", cache_key="old", compiler_version="v")
        return binary, meta_dir, meta_dir / f"{binary.name}.json"

    def test_meta_absent_while_binary_is_renamed(self, tmp_path: Path, monkeypatch):
        import os

        binary, meta_dir, meta = self._stale_cache(tmp_path)
        seen: list[bool] = []

        def spy(real):
            def wrapper(src, dst):
                if Path(dst) == binary:
                    seen.append(meta.exists())
                return real(src, dst)

            return wrapper

        monkeypatch.setattr(os, "rename", spy(os.rename))
        monkeypatch.setattr(os, "replace", spy(os.replace))

        outcome = run_cached_test(
            _recording_cmd(tmp_path / "log", "new", binary),
            cache_key="new",
            meta_dir=meta_dir,
            compiler_version="v",
        )
        assert outcome.kind == OutcomeKind.PASS
        assert seen == [False]
        assert json.loads(meta.read_text())["cache_key"] == "new"

    def test_interrupt_between_rename_and_meta_write_leaves_miss(self, tmp_path: Path, monkeypatch):
        binary, meta_dir, meta = self._stale_cache(tmp_path)

        def boom(*_args, **_kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr("mojox.exec.write_cache_meta", boom)
        with pytest.raises(KeyboardInterrupt):
            run_cached_test(
                _recording_cmd(tmp_path / "log", "new", binary),
                cache_key="new",
                meta_dir=meta_dir,
                compiler_version="v",
            )
        monkeypatch.undo()

        # The stale key must not validate the freshly renamed binary.
        assert not meta.exists()
        outcome = run_cached_test(
            _recording_cmd(tmp_path / "log2", "rebuilt", binary),
            cache_key="old",
            meta_dir=meta_dir,
            compiler_version="v",
        )
        assert outcome.stdout.strip() == "built-rebuilt"


class TestCorruptMetaIsMiss:
    """A damaged meta file falls back to a rebuild rather than crashing."""

    @pytest.mark.parametrize(
        "payload",
        [b"", b"{\"schema_version\": 1, \"cache_k", b"\xff\xfe\x00", b"null", b"[\"k\"]"],
    )
    def test_corrupt_meta_rebuilds(self, tmp_path: Path, payload: bytes):
        binary = tmp_path / "bin" / "test_x-deadbeef"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho stale\n")
        binary.chmod(0o755)
        meta = tmp_path / "meta" / f"{binary.name}.json"
        meta.parent.mkdir()
        meta.write_bytes(payload)

        outcome = run_cached_test(
            _recording_cmd(tmp_path / "log", "fresh", binary),
            cache_key="k",
            meta_dir=meta.parent,
            compiler_version="v",
        )
        assert outcome.stdout.strip() == "built-fresh"
        assert json.loads(meta.read_text())["cache_key"] == "k"


def _precompile_cmd(tmp_path: Path, *flags: str, mojo: str = "/opt/mojo/bin/mojo", env: dict | None = None) -> Command:
    """A planner-shaped COMPILE_PACKAGE command for ``src/mylib`` under *tmp_path*."""
    lib = tmp_path / "src" / "mylib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "__init__.mojo").write_text("fn lib(): pass\n")
    output = ".mojox/build/pkg/mylib.mojoc"
    return _cmd(
        (mojo, "precompile", "src/mylib", "-o", output, *flags),
        cwd=PurePosixPath(str(tmp_path)),
        env=env or {"PATH": "/usr/bin", "HOME": ""},
        kind=CommandKind.COMPILE_PACKAGE,
        target_id="src/mylib",
        outputs=(output,),
    )


class TestPrecompileCacheKey:
    """The precompile key covers every input of ``mojo precompile``."""

    def test_unchanged_inputs_give_identical_key(self, tmp_path: Path):
        cmd = _precompile_cmd(tmp_path, "-I", str(tmp_path / "dep"))
        assert _precompile_cache_key(cmd, None, "1.0.0") == _precompile_cache_key(cmd, None, "1.0.0")

    def test_lib_edit_changes_key(self, tmp_path: Path):
        cmd = _precompile_cmd(tmp_path)
        before = _precompile_cache_key(cmd, None, "1.0.0")
        (tmp_path / "src" / "mylib" / "__init__.mojo").write_text("fn lib(): return\n")
        assert _precompile_cache_key(cmd, None, "1.0.0") != before

    def test_include_dir_edit_changes_key(self, tmp_path: Path):
        dep = tmp_path / "dep"
        (dep / "navette").mkdir(parents=True)
        (dep / "navette" / "__init__.mojo").write_text("a")
        cmd = _precompile_cmd(tmp_path, "-I", str(dep))
        before = _precompile_cache_key(cmd, None, "1.0.0")
        (dep / "navette" / "__init__.mojo").write_text("ab")
        assert _precompile_cache_key(cmd, None, "1.0.0") != before

    def test_relative_include_dir_is_resolved_against_cwd(self, tmp_path: Path, monkeypatch):
        (tmp_path / "vendored").mkdir()
        cmd = _precompile_cmd(tmp_path, "-I", "vendored")
        before = _precompile_cache_key(cmd, None, "1.0.0")
        monkeypatch.chdir("/")
        (tmp_path / "vendored" / "x.mojo").write_text("")
        assert _precompile_cache_key(cmd, None, "1.0.0") != before

    def test_include_reorder_changes_key(self, tmp_path: Path):
        a, b = str(tmp_path / "a"), str(tmp_path / "b")
        first = _precompile_cache_key(_precompile_cmd(tmp_path, "-I", a, "-I", b), None, "1.0.0")
        assert _precompile_cache_key(_precompile_cmd(tmp_path, "-I", b, "-I", a), None, "1.0.0") != first

    def test_extra_flag_changes_key(self, tmp_path: Path):
        base = _precompile_cache_key(_precompile_cmd(tmp_path), None, "1.0.0")
        assert _precompile_cache_key(_precompile_cmd(tmp_path, "--Werror"), None, "1.0.0") != base

    def test_env_changes_key(self, tmp_path: Path):
        cmd = _precompile_cmd(tmp_path)
        base = _precompile_cache_key(cmd, None, "1.0.0")
        assert _precompile_cache_key(cmd, {"MODULAR_X": "1"}, "1.0.0") != base
        other = _precompile_cmd(tmp_path, env={"PATH": "/usr/bin", "HOME": "", "LANG": "fr_FR.UTF-8"})
        assert _precompile_cache_key(other, None, "1.0.0") != base

    def test_mojo_path_changes_key(self, tmp_path: Path):
        base = _precompile_cache_key(_precompile_cmd(tmp_path), None, "1.0.0")
        assert _precompile_cache_key(_precompile_cmd(tmp_path, mojo="/other/mojo"), None, "1.0.0") != base

    def test_compiler_version_changes_key(self, tmp_path: Path):
        cmd = _precompile_cmd(tmp_path)
        assert _precompile_cache_key(cmd, None, "1.0.0") != _precompile_cache_key(cmd, None, "1.0.1")

    def test_missing_lib_dir_is_uncacheable(self, tmp_path: Path):
        cmd = _precompile_cmd(tmp_path)
        (tmp_path / "src" / "mylib" / "__init__.mojo").unlink()
        (tmp_path / "src" / "mylib").rmdir()
        assert _precompile_cache_key(cmd, None, "1.0.0") is None

    def test_malformed_argv_is_uncacheable(self, tmp_path: Path):
        cmd = _cmd(("/opt/mojo", "precompile", "src/mylib"), kind=CommandKind.COMPILE_PACKAGE)
        assert _precompile_cache_key(cmd, None, "1.0.0") is None


# Fake precompiler: logs each call, writes ``pkg:<n>`` to the -o path, or
# exits 1 without writing when ``FAIL`` exists next to the log.
_FAKE_PRECOMPILE = (
    "import pathlib, sys\n"
    "log = pathlib.Path({log!r})\n"
    "if (log.parent / 'FAIL').exists():\n"
    "    sys.exit(1)\n"
    "with log.open('a') as f:\n"
    "    f.write(sys.argv[sys.argv.index('-o') + 1] + '\\n')\n"
    "n = len(log.read_text().splitlines())\n"
    "pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(f'pkg:{{n}}')\n"
)


class TestRunCachedPrecompile:
    """Hit / miss / publish flow of ``run_cached_precompile``."""

    @pytest.fixture
    def setup(self, tmp_path: Path):
        log = tmp_path / "log" / "calls"
        log.parent.mkdir()
        log.touch()
        script = tmp_path / "fake_mojo"
        script.write_text(f"#!{sys.executable}\n" + _FAKE_PRECOMPILE.format(log=str(log)))
        script.chmod(0o755)
        base = _precompile_cmd(tmp_path)
        cmd = _cmd(
            (str(script), *base.argv[1:]),
            cwd=base.cwd,
            kind=base.kind,
            target_id=base.target_id,
            outputs=base.outputs,
        )
        meta_dir = tmp_path / ".mojox" / "cache" / "meta"
        return cmd, meta_dir, log, tmp_path / base.outputs[0]

    @staticmethod
    def _run(cmd: Command, meta_dir: Path, key: str | None = "k1") -> Outcome:
        return run_cached_precompile(cmd, cache_key=key, meta_dir=meta_dir, compiler_version="1.0.0")

    def test_miss_then_hit(self, setup):
        cmd, meta_dir, log, package = setup
        first = self._run(cmd, meta_dir)
        second = self._run(cmd, meta_dir)
        assert first.kind == second.kind == OutcomeKind.PASS
        assert second.command is cmd
        assert len(log.read_text().splitlines()) == 1
        assert package.read_text() == "pkg:1"
        assert (meta_dir / "precompile-mylib.mojoc.json").is_file()

    def test_builds_into_a_temp_path_with_the_same_file_name(self, setup):
        cmd, meta_dir, log, package = setup
        self._run(cmd, meta_dir)
        built = Path(log.read_text().splitlines()[0])
        assert built != package
        assert built.name == package.name
        assert not built.exists()
        assert not any(p.name.startswith(".") for p in package.parent.iterdir())

    def test_key_change_rebuilds(self, setup):
        cmd, meta_dir, log, package = setup
        self._run(cmd, meta_dir, "k1")
        self._run(cmd, meta_dir, "k2")
        assert len(log.read_text().splitlines()) == 2
        assert package.read_text() == "pkg:2"

    def test_none_key_always_builds_and_drops_meta(self, setup):
        cmd, meta_dir, log, package = setup
        self._run(cmd, meta_dir, "k1")
        assert self._run(cmd, meta_dir, None).kind == OutcomeKind.PASS
        assert self._run(cmd, meta_dir, None).kind == OutcomeKind.PASS
        assert len(log.read_text().splitlines()) == 3
        assert not (meta_dir / "precompile-mylib.mojoc.json").exists()
        assert package.read_text() == "pkg:3"

    def test_failure_writes_no_meta_and_keeps_old_package(self, setup):
        cmd, meta_dir, log, package = setup
        self._run(cmd, meta_dir, "k1")
        (log.parent / "FAIL").touch()
        failed = self._run(cmd, meta_dir, "k2")
        assert failed.kind == OutcomeKind.FAIL
        assert package.read_text() == "pkg:1"
        assert read_cache_meta(meta_dir / "precompile-mylib.mojoc.json") == "k1"
        assert not any(p.name.startswith(".") for p in package.parent.iterdir())

    def test_first_failure_writes_no_meta(self, setup):
        cmd, meta_dir, log, _package = setup
        (log.parent / "FAIL").touch()
        assert self._run(cmd, meta_dir).kind == OutcomeKind.FAIL
        assert not (meta_dir / "precompile-mylib.mojoc.json").exists()

    def test_success_without_output_is_a_compile_error(self, tmp_path: Path):
        base = _precompile_cmd(tmp_path)
        cmd = _cmd((sys.executable, "-c", "pass", *base.argv[1:]), cwd=base.cwd, kind=base.kind, outputs=base.outputs)
        meta_dir = tmp_path / "meta"
        outcome = self._run(cmd, meta_dir)
        assert outcome.kind == OutcomeKind.COMPILE_ERROR
        assert "mylib.mojoc" in outcome.stderr
        assert not (meta_dir / "precompile-mylib.mojoc.json").exists()

    def test_meta_absent_while_package_is_replaced(self, setup, monkeypatch):
        import mojox.exec as exec_mod

        cmd, meta_dir, _log, package = setup
        self._run(cmd, meta_dir, "k1")
        meta = meta_dir / "precompile-mylib.mojoc.json"
        seen: list[bool] = []
        real_replace = exec_mod.os.replace

        def spy(src, dst):
            if Path(dst) == package:
                seen.append(meta.exists())
            real_replace(src, dst)

        monkeypatch.setattr(exec_mod.os, "replace", spy)
        self._run(cmd, meta_dir, "k2")
        assert seen == [False]
        assert read_cache_meta(meta) == "k2"

    def test_routed_through_run_commands_with_cache_context(self, setup):
        cmd, meta_dir, log, _package = setup
        test = _cmd((sys.executable, "-c", "pass"), target_id="t.mojo", depends_on=(cmd.target_id,))
        ctx = CacheContext(
            project_hash="a", tests_tree_hash="b", deps_stamp="c", compiler_version="1.0.0", meta_dir=meta_dir
        )
        for _ in range(2):
            results = run_commands((cmd, test), cache_context=ctx)
            assert [r.kind for r in results] == [OutcomeKind.PASS, OutcomeKind.PASS]
        assert len(log.read_text().splitlines()) == 1

    def test_disabled_cache_context_always_precompiles(self, setup):
        cmd, meta_dir, log, _package = setup
        ctx = CacheContext(
            project_hash="a",
            tests_tree_hash="b",
            deps_stamp="c",
            compiler_version="1.0.0",
            meta_dir=meta_dir,
            enabled=False,
        )
        run_commands((cmd,), cache_context=ctx)
        run_commands((cmd,), cache_context=ctx)
        assert len(log.read_text().splitlines()) == 2
        assert not (meta_dir / "precompile-mylib.mojoc.json").exists()
