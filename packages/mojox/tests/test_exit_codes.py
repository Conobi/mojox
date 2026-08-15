"""Exit code determination: pure function from outcomes to int."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest
from mojox._cli import determine_exit_code
from mojox._types import Outcome, OutcomeKind
from mojox_core import Command, CommandKind


def _outcome(kind: OutcomeKind, cmd_kind: CommandKind = CommandKind.RUN_TEST) -> Outcome:
    """Build a minimal Outcome for exit code testing."""
    cmd = Command(
        argv=("/usr/bin/mojo", "run", "t.mojo"),
        cwd=PurePosixPath("/workspace"),
        env={"PATH": "/usr/bin"},
        kind=cmd_kind,
        target_id="t.mojo",
        timeout_s=300,
        outputs=(),
        depends_on=(),
    )
    return Outcome(
        command=cmd,
        kind=kind,
        exit_code=0 if kind == OutcomeKind.PASS else 1,
        stdout="",
        stderr="",
        diagnostics=(),
        elapsed_s=1.0,
    )


class TestDetermineExitCode:
    """Exhaustive exit code matrix."""

    def test_all_pass_exits_0(self):
        outcomes = (_outcome(OutcomeKind.PASS),)
        assert determine_exit_code(outcomes) == 0

    def test_empty_outcomes_exits_0(self):
        assert determine_exit_code(()) == 0

    @pytest.mark.parametrize(
        "kind",
        [
            OutcomeKind.FAIL,
            OutcomeKind.TIMEOUT,
            OutcomeKind.CRASH,
            OutcomeKind.COMPILE_ERROR,
        ],
    )
    def test_test_failure_exits_1(self, kind):
        outcomes = (_outcome(kind, CommandKind.RUN_TEST),)
        assert determine_exit_code(outcomes) == 1

    @pytest.mark.parametrize(
        "cmd_kind",
        [
            CommandKind.COMPILE_PACKAGE,
            CommandKind.COMPILE_BINARY,
            CommandKind.CHECK_EXAMPLE,
        ],
    )
    def test_compile_failure_exits_2(self, cmd_kind):
        outcomes = (_outcome(OutcomeKind.FAIL, cmd_kind),)
        assert determine_exit_code(outcomes) == 2

    def test_test_failure_takes_precedence_over_compile_failure(self):
        outcomes = (
            _outcome(OutcomeKind.FAIL, CommandKind.COMPILE_PACKAGE),
            _outcome(OutcomeKind.FAIL, CommandKind.RUN_TEST),
        )
        assert determine_exit_code(outcomes) == 1

    def test_skipped_does_not_count_as_failure(self):
        outcomes = (
            _outcome(OutcomeKind.PASS, CommandKind.RUN_TEST),
            _outcome(OutcomeKind.SKIPPED, CommandKind.RUN_TEST),
        )
        assert determine_exit_code(outcomes) == 0

    def test_skipped_with_compile_failure_exits_2(self):
        outcomes = (
            _outcome(OutcomeKind.FAIL, CommandKind.COMPILE_PACKAGE),
            _outcome(OutcomeKind.SKIPPED, CommandKind.RUN_TEST),
        )
        assert determine_exit_code(outcomes) == 2

    def test_all_skipped_exits_0(self):
        outcomes = (
            _outcome(OutcomeKind.SKIPPED, CommandKind.RUN_TEST),
            _outcome(OutcomeKind.SKIPPED, CommandKind.RUN_TEST),
        )
        assert determine_exit_code(outcomes) == 0

    def test_pass_compile_pass_test_exits_0(self):
        outcomes = (
            _outcome(OutcomeKind.PASS, CommandKind.COMPILE_PACKAGE),
            _outcome(OutcomeKind.PASS, CommandKind.RUN_TEST),
        )
        assert determine_exit_code(outcomes) == 0
