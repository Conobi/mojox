"""Render exec results for human consumption.

Human output goes to stderr. JSON metadata goes to stdout.
"""

from __future__ import annotations

import sys
from typing import IO

from mojox_core import Command, Diagnostic

from ._types import Outcome, OutcomeKind


def render_summary(
    outcomes: tuple[Outcome, ...],
    *,
    stream: IO[str] | None = None,
) -> None:
    """Render a summary of execution outcomes.

    Shows pass/fail counts, timing, and any diagnostics from
    failed targets.
    """
    out = stream or sys.stderr

    if not outcomes:
        out.write("No targets to run.\n")
        return

    passed = sum(1 for o in outcomes if o.kind == OutcomeKind.PASS)
    failed = sum(1 for o in outcomes if o.kind == OutcomeKind.FAIL)
    timed_out = sum(1 for o in outcomes if o.kind == OutcomeKind.TIMEOUT)
    crashed = sum(1 for o in outcomes if o.kind == OutcomeKind.CRASH)
    compile_errors = sum(1 for o in outcomes if o.kind == OutcomeKind.COMPILE_ERROR)
    total_time = sum(o.elapsed_s for o in outcomes)

    for o in outcomes:
        if o.kind != OutcomeKind.PASS:
            _render_failure(o, out)

    parts: list[str] = []
    if passed:
        parts.append(f"{passed} passed")
    if failed:
        parts.append(f"{failed} failed")
    if timed_out:
        parts.append(f"{timed_out} timed out")
    if crashed:
        parts.append(f"{crashed} crashed")
    if compile_errors:
        parts.append(f"{compile_errors} compile error(s)")

    summary = ", ".join(parts) if parts else "0 targets"
    out.write(f"\n{summary} in {total_time:.2f}s\n")


def _render_failure(outcome: Outcome, out: IO[str]) -> None:
    """Render details for a single failed target."""
    label = _kind_label(outcome.kind)
    out.write(f"\n{label}: {outcome.command.target_id}\n")

    if outcome.stderr:
        for line in outcome.stderr.splitlines()[:20]:
            out.write(f"  {line}\n")

    if outcome.kind == OutcomeKind.TIMEOUT:
        out.write(f"  (timed out after {outcome.elapsed_s:.1f}s)\n")


def _kind_label(kind: OutcomeKind) -> str:
    """Human-readable label for an outcome kind."""
    return {
        OutcomeKind.FAIL: "FAIL",
        OutcomeKind.TIMEOUT: "TIMEOUT",
        OutcomeKind.CRASH: "CRASH",
        OutcomeKind.COMPILE_ERROR: "COMPILE ERROR",
        OutcomeKind.PASS: "PASS",
    }[kind]


def render_dry_run(
    commands: tuple[Command, ...],
    *,
    stream: IO[str] | None = None,
) -> None:
    """Render the planned commands without executing them.

    Shows each command's argv, cwd, and key flags.
    """
    out = stream or sys.stderr

    for cmd in commands:
        argv_str = " ".join(cmd.argv)
        out.write(f"[{cmd.kind.value}] {cmd.target_id}\n")
        out.write(f"  cwd: {cmd.cwd}\n")
        out.write(f"  {argv_str}\n")
        if cmd.depends_on:
            out.write(f"  depends_on: {', '.join(cmd.depends_on)}\n")
        out.write("\n")


def render_diagnostics(
    diagnostics: tuple[Diagnostic, ...],
    *,
    stream: IO[str] | None = None,
) -> None:
    """Render diagnostics to stderr."""
    out = stream or sys.stderr
    for d in diagnostics:
        prefix = f"{d.kind}: " if d.kind != "note" else ""
        location = ""
        if d.file:
            location = f"{d.file}"
            if d.line is not None:
                location += f":{d.line}"
                if d.column is not None:
                    location += f":{d.column}"
            location += ": "
        out.write(f"{location}{prefix}{d.message}\n")
