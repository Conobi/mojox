"""Exec runner: Command → Outcome via subprocess."""

from __future__ import annotations

import sys
from pathlib import PurePosixPath

import pytest

from mojox._exec import run_command
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
