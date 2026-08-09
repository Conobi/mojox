"""Exec runner: Command → Outcome via subprocess."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

from mojox._exec import run_command, run_commands
from mojox._types import OutcomeKind
from mojox_core import Command, CommandKind


def _cmd(argv: tuple[str, ...], **overrides) -> Command:
    """Build a test command with sensible defaults."""
    defaults = dict(
        argv=argv,
        cwd=PurePosixPath("."),
        env={"PATH": "/usr/bin", "HOME": ""},
        kind=CommandKind.RUN_TEST,
        target_id="test::t.mojo",
        timeout_s=30,
        outputs=(),
        depends_on=(),
    )
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
        cmd1 = _cmd((sys.executable, "-c", "print('one')"), target_id="test::one")
        cmd2 = _cmd((sys.executable, "-c", "print('two')"), target_id="test::two")
        results = run_commands((cmd1, cmd2), max_workers=1)
        assert len(results) == 2
        assert results[0].kind == OutcomeKind.PASS
        assert results[1].kind == OutcomeKind.PASS

    def test_depends_on_ordering(self):
        """Commands with depends_on wait for dependencies to complete."""
        precompile = _cmd(
            (sys.executable, "-c", "import time; time.sleep(0.1); print('precompiled')"),
            kind=CommandKind.COMPILE_PACKAGE,
            target_id="lib::mylib",
        )
        test = _cmd(
            (sys.executable, "-c", "print('tested')"),
            target_id="test::t.mojo",
            depends_on=("lib::mylib",),
        )
        results = run_commands((precompile, test), max_workers=2)
        assert len(results) == 2
        assert results[0].command.target_id == "lib::mylib"
        assert results[1].command.target_id == "test::t.mojo"
        assert results[0].kind == OutcomeKind.PASS
        assert results[1].kind == OutcomeKind.PASS

    def test_failure_in_dependency_skips_dependents(self):
        """When a dependency fails, its dependents are skipped."""
        precompile = _cmd(
            (sys.executable, "-c", "import sys; sys.exit(1)"),
            kind=CommandKind.COMPILE_PACKAGE,
            target_id="lib::mylib",
        )
        test = _cmd(
            (sys.executable, "-c", "print('should not run')"),
            target_id="test::t.mojo",
            depends_on=("lib::mylib",),
        )
        results = run_commands((precompile, test), max_workers=2)
        assert results[0].kind == OutcomeKind.FAIL
        assert results[1].kind == OutcomeKind.FAIL
        assert "dependency" in results[1].stderr.lower()

    def test_concurrent_independent_commands(self):
        """Independent commands run concurrently."""
        cmds = tuple(
            _cmd(
                (sys.executable, "-c", f"print({i})"),
                target_id=f"test::t{i}.mojo",
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
