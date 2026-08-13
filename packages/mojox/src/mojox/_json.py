"""NDJSON event serialization for CI pipeline output.

Pure serializer functions (no I/O) and a thread-safe writer.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import IO

from mojox_core import Command, CommandKind, Diagnostic

from ._types import Outcome, OutcomeKind

_SCHEMA_VERSION = 1

_EVENT_MAP: dict[OutcomeKind, str] = {
    OutcomeKind.PASS: "ok",
    OutcomeKind.FAIL: "failed",
    OutcomeKind.TIMEOUT: "timeout",
    OutcomeKind.CRASH: "crash",
    OutcomeKind.COMPILE_ERROR: "compile-error",
    OutcomeKind.SKIPPED: "skipped",
}

_COMPILE_KINDS = frozenset({
    CommandKind.COMPILE_PACKAGE,
    CommandKind.COMPILE_BINARY,
    CommandKind.CHECK_EXAMPLE,
})


def serialize_suite_started(test_count: int) -> dict:
    """Build the suite:started event dict."""
    return {
        "type": "suite",
        "event": "started",
        "test_count": test_count,
        "schema_version": _SCHEMA_VERSION,
    }


def serialize_command_started(cmd: Command) -> dict:
    """Build a command:started event dict."""
    return {
        "type": "command",
        "event": "started",
        "name": cmd.target_id,
        "kind": cmd.kind.value,
    }


def _serialize_diagnostic(d: Diagnostic) -> dict:
    """Convert a Diagnostic to a JSON-ready dict, omitting None fields."""
    result: dict = {"kind": d.kind, "message": d.message}
    if d.file is not None:
        result["file"] = d.file
    if d.line is not None:
        result["line"] = d.line
    if d.column is not None:
        result["column"] = d.column
    if d.source_text is not None:
        result["source_text"] = d.source_text
    return result


def serialize_command_completed(outcome: Outcome) -> dict:
    """Build a command:completed event dict."""
    return {
        "type": "command",
        "event": _EVENT_MAP[outcome.kind],
        "name": outcome.command.target_id,
        "kind": outcome.command.kind.value,
        "elapsed_s": outcome.elapsed_s,
        "exit_code": outcome.exit_code,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "diagnostics": [_serialize_diagnostic(d) for d in outcome.diagnostics],
    }


def serialize_suite_finished(
    outcomes: tuple[Outcome, ...],
    *,
    elapsed_s: float,
) -> dict:
    """Build the suite:finished event dict with split test/compile counts."""
    test_outcomes = [o for o in outcomes if o.command.kind == CommandKind.RUN_TEST]
    compile_outcomes = [o for o in outcomes if o.command.kind in _COMPILE_KINDS]

    passed = sum(1 for o in test_outcomes if o.kind == OutcomeKind.PASS)
    failed = sum(1 for o in test_outcomes if o.kind in (OutcomeKind.FAIL, OutcomeKind.COMPILE_ERROR))
    timed_out = sum(1 for o in test_outcomes if o.kind == OutcomeKind.TIMEOUT)
    crashed = sum(1 for o in test_outcomes if o.kind == OutcomeKind.CRASH)
    skipped = sum(1 for o in test_outcomes if o.kind == OutcomeKind.SKIPPED)
    compile_passed = sum(1 for o in compile_outcomes if o.kind == OutcomeKind.PASS)
    compile_failed = sum(1 for o in compile_outcomes if o.kind not in (OutcomeKind.PASS, OutcomeKind.SKIPPED))

    all_ok = all(
        o.kind in (OutcomeKind.PASS, OutcomeKind.SKIPPED) for o in outcomes
    )

    return {
        "type": "suite",
        "event": "ok" if all_ok else "failed",
        "passed": passed,
        "failed": failed,
        "timed_out": timed_out,
        "crashed": crashed,
        "skipped": skipped,
        "compile_passed": compile_passed,
        "compile_failed": compile_failed,
        "elapsed_s": elapsed_s,
    }


class JsonEventWriter:
    """Thread-safe NDJSON writer."""

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def write_event(self, event: dict) -> None:
        """Serialize and write a single event as one NDJSON line."""
        line = json.dumps(event, separators=(",", ":")) + "\n"
        with self._lock:
            self._stream.write(line)
            self._stream.flush()


def make_json_callbacks(
    writer: JsonEventWriter,
) -> tuple[Callable[[Command], None], Callable[[Outcome], None]]:
    """Build thread-safe on_start and on_complete callbacks sharing *writer*."""

    def on_start(cmd: Command) -> None:
        """Write a command:started event."""
        writer.write_event(serialize_command_started(cmd))

    def on_complete(outcome: Outcome) -> None:
        """Write a command:completed event."""
        writer.write_event(serialize_command_completed(outcome))

    return on_start, on_complete
