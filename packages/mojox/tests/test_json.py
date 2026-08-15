"""JSON serialization: pure functions producing NDJSON events."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from mojox._json import (
    serialize_command_completed,
    serialize_command_started,
    serialize_suite_finished,
    serialize_suite_started,
)
from mojox._types import Outcome, OutcomeKind
from mojox_core import Command, CommandKind, Diagnostic


def _cmd(target_id: str = "tests/test_a.mojo", kind: CommandKind = CommandKind.RUN_TEST) -> Command:
    return Command(
        argv=("/usr/bin/mojo", "run", "t.mojo"),
        cwd=PurePosixPath("/workspace"),
        env={"PATH": "/usr/bin"},
        kind=kind,
        target_id=target_id,
        timeout_s=300,
        outputs=(),
        depends_on=(),
    )


def _outcome(
    target_id: str = "tests/test_a.mojo",
    kind: OutcomeKind = OutcomeKind.PASS,
    cmd_kind: CommandKind = CommandKind.RUN_TEST,
    **kw,
) -> Outcome:
    cmd = _cmd(target_id, cmd_kind)
    return Outcome(
        command=cmd,
        kind=kind,
        exit_code=kw.get("exit_code", 0 if kind == OutcomeKind.PASS else 1),
        stdout=kw.get("stdout", ""),
        stderr=kw.get("stderr", ""),
        diagnostics=kw.get("diagnostics", ()),
        elapsed_s=kw.get("elapsed_s", 1.0),
    )


class TestSerializeSuiteStarted:
    def test_structure(self):
        event = serialize_suite_started(5)
        assert event["type"] == "suite"
        assert event["event"] == "started"
        assert event["test_count"] == 5
        assert event["schema_version"] == 1

    def test_is_valid_json(self):
        event = serialize_suite_started(0)
        line = json.dumps(event, separators=(",", ":"))
        assert "\n" not in line


class TestSerializeCommandStarted:
    def test_structure(self):
        cmd = _cmd("tests/test_a.mojo", CommandKind.RUN_TEST)
        event = serialize_command_started(cmd)
        assert event["type"] == "command"
        assert event["event"] == "started"
        assert event["name"] == "tests/test_a.mojo"
        assert event["kind"] == "run-test"


class TestSerializeCommandCompleted:
    def test_pass_event(self):
        outcome = _outcome(kind=OutcomeKind.PASS)
        event = serialize_command_completed(outcome)
        assert event["type"] == "command"
        assert event["event"] == "ok"
        assert event["name"] == "tests/test_a.mojo"
        assert event["kind"] == "run-test"
        assert event["elapsed_s"] == 1.0
        assert event["exit_code"] == 0
        assert event["diagnostics"] == []

    def test_fail_event(self):
        outcome = _outcome(kind=OutcomeKind.FAIL, stderr="assertion failed")
        event = serialize_command_completed(outcome)
        assert event["event"] == "failed"
        assert event["stderr"] == "assertion failed"

    def test_skipped_event(self):
        outcome = _outcome(kind=OutcomeKind.SKIPPED, exit_code=None, elapsed_s=0.0)
        event = serialize_command_completed(outcome)
        assert event["event"] == "skipped"
        assert event["exit_code"] is None

    def test_diagnostics_serialized(self):
        diag = Diagnostic(kind="error", message="type mismatch", file="test.mojo", line=5, column=10)
        outcome = _outcome(diagnostics=(diag,))
        event = serialize_command_completed(outcome)
        assert len(event["diagnostics"]) == 1
        assert event["diagnostics"][0]["kind"] == "error"
        assert event["diagnostics"][0]["file"] == "test.mojo"
        assert event["diagnostics"][0]["line"] == 5

    def test_diagnostics_omit_none_fields(self):
        diag = Diagnostic(kind="error", message="bad")
        outcome = _outcome(diagnostics=(diag,))
        event = serialize_command_completed(outcome)
        d = event["diagnostics"][0]
        assert "file" not in d
        assert "line" not in d
        assert "column" not in d

    def test_stdout_with_newlines_is_valid_ndjson(self):
        outcome = _outcome(stdout="line1\nline2\nline3")
        event = serialize_command_completed(outcome)
        line = json.dumps(event, separators=(",", ":"))
        assert "\n" not in line

    def test_all_outcome_kinds_mapped(self):
        expected = {
            OutcomeKind.PASS: "ok",
            OutcomeKind.FAIL: "failed",
            OutcomeKind.TIMEOUT: "timeout",
            OutcomeKind.CRASH: "crash",
            OutcomeKind.COMPILE_ERROR: "compile-error",
            OutcomeKind.SKIPPED: "skipped",
        }
        for kind, event_name in expected.items():
            outcome = _outcome(kind=kind, exit_code=None if kind in (OutcomeKind.SKIPPED, OutcomeKind.TIMEOUT) else 1)
            event = serialize_command_completed(outcome)
            assert event["event"] == event_name, f"{kind} should map to {event_name}"


class TestSerializeSuiteFinished:
    def test_all_pass(self):
        outcomes = (
            _outcome(kind=OutcomeKind.PASS, cmd_kind=CommandKind.COMPILE_PACKAGE, target_id="lib"),
            _outcome(kind=OutcomeKind.PASS, target_id="t1"),
            _outcome(kind=OutcomeKind.PASS, target_id="t2"),
        )
        event = serialize_suite_finished(outcomes, elapsed_s=5.0)
        assert event["type"] == "suite"
        assert event["event"] == "ok"
        assert event["passed"] == 2
        assert event["compile_passed"] == 1
        assert event["elapsed_s"] == 5.0

    def test_mixed_results(self):
        outcomes = (
            _outcome(kind=OutcomeKind.FAIL, cmd_kind=CommandKind.COMPILE_PACKAGE, target_id="lib"),
            _outcome(kind=OutcomeKind.PASS, target_id="t1"),
            _outcome(kind=OutcomeKind.FAIL, target_id="t2"),
            _outcome(kind=OutcomeKind.SKIPPED, target_id="t3"),
        )
        event = serialize_suite_finished(outcomes, elapsed_s=10.0)
        assert event["event"] == "failed"
        assert event["passed"] == 1
        assert event["failed"] == 1
        assert event["skipped"] == 1
        assert event["compile_passed"] == 0
        assert event["compile_failed"] == 1

    def test_test_count_invariant(self):
        outcomes = (
            _outcome(kind=OutcomeKind.PASS, target_id="t1"),
            _outcome(kind=OutcomeKind.FAIL, target_id="t2"),
            _outcome(kind=OutcomeKind.TIMEOUT, target_id="t3"),
            _outcome(kind=OutcomeKind.CRASH, target_id="t4"),
            _outcome(kind=OutcomeKind.SKIPPED, target_id="t5"),
            _outcome(kind=OutcomeKind.COMPILE_ERROR, target_id="t6"),
        )
        event = serialize_suite_finished(outcomes, elapsed_s=1.0)
        total = event["passed"] + event["failed"] + event["timed_out"] + event["crashed"] + event["skipped"]
        assert total == 6

    def test_compile_error_test_counted_as_failed(self):
        outcomes = (_outcome(kind=OutcomeKind.COMPILE_ERROR, target_id="t1"),)
        event = serialize_suite_finished(outcomes, elapsed_s=1.0)
        assert event["failed"] == 1

    def test_skipped_compile_not_counted_as_failed(self):
        outcomes = (
            _outcome(kind=OutcomeKind.PASS, cmd_kind=CommandKind.COMPILE_PACKAGE, target_id="lib1"),
            _outcome(kind=OutcomeKind.SKIPPED, cmd_kind=CommandKind.COMPILE_PACKAGE, target_id="lib2"),
        )
        event = serialize_suite_finished(outcomes, elapsed_s=1.0)
        assert event["compile_passed"] == 1
        assert event["compile_failed"] == 0
