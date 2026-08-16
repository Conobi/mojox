"""Outcome types: frozen dataclasses for exec results."""

from __future__ import annotations

from pathlib import PurePosixPath

from mojox.types import Outcome, OutcomeKind, OutputFormat, OutputMode
from mojox_core import Command, CommandKind


class TestOutcomeKind:
    def test_values(self):
        assert OutcomeKind.PASS.value == "pass"
        assert OutcomeKind.FAIL.value == "fail"
        assert OutcomeKind.COMPILE_ERROR.value == "compile-error"
        assert OutcomeKind.TIMEOUT.value == "timeout"
        assert OutcomeKind.CRASH.value == "crash"


class TestOutcome:
    def test_outcome_is_frozen(self):
        cmd = Command(
            argv=("/usr/bin/mojo", "run", "test.mojo"),
            cwd=PurePosixPath("/workspace/project"),
            env={"PATH": "/usr/bin"},
            kind=CommandKind.RUN_TEST,
            target_id="test.mojo",
            timeout_s=300,
            outputs=(),
            depends_on=(),
        )
        outcome = Outcome(
            command=cmd,
            kind=OutcomeKind.PASS,
            exit_code=0,
            stdout="",
            stderr="",
            diagnostics=(),
            elapsed_s=1.5,
        )
        assert outcome.kind == OutcomeKind.PASS
        assert outcome.exit_code == 0
        assert outcome.elapsed_s == 1.5

        import pytest

        with pytest.raises(AttributeError):
            outcome.kind = OutcomeKind.FAIL  # type: ignore[misc]

    def test_crash_has_none_exit_code(self):
        cmd = Command(
            argv=("/usr/bin/mojo", "run", "test.mojo"),
            cwd=PurePosixPath("/workspace/project"),
            env={"PATH": "/usr/bin"},
            kind=CommandKind.RUN_TEST,
            target_id="test.mojo",
            timeout_s=300,
            outputs=(),
            depends_on=(),
        )
        outcome = Outcome(
            command=cmd,
            kind=OutcomeKind.CRASH,
            exit_code=None,
            stdout="",
            stderr="Segmentation fault",
            diagnostics=(),
            elapsed_s=0.3,
        )
        assert outcome.exit_code is None
        assert outcome.kind == OutcomeKind.CRASH


class TestOutcomeKindSkipped:
    def test_skipped_value(self):
        assert OutcomeKind.SKIPPED.value == "skipped"

    def test_skipped_is_distinct(self):
        kinds = [k.value for k in OutcomeKind]
        assert len(kinds) == len(set(kinds))


class TestOutputMode:
    def test_values(self):
        assert OutputMode.IMMEDIATE.value == "immediate"
        assert OutputMode.FINAL.value == "final"
        assert OutputMode.NEVER.value == "never"

    def test_all_members(self):
        assert len(OutputMode) == 3


class TestOutputFormat:
    def test_values(self):
        assert OutputFormat.HUMAN.value == "human"
        assert OutputFormat.JSON.value == "json"

    def test_all_members(self):
        assert len(OutputFormat) == 2
