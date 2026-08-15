"""Render exec results for human consumption.

Human output goes to stderr. JSON metadata goes to stdout.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import IO

from mojox_core import Command, CommandKind, Diagnostic

from ._types import Outcome, OutcomeKind, OutputMode

# -- ANSI helpers -----------------------------------------------------------

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_RED_BOLD = "\033[1;31m"
_RESET = "\033[0m"


def _use_color(stream: IO[str]) -> bool:
    """Return True when *stream* is a TTY and color is not suppressed."""
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def _c(stream: IO[str], code: str, text: str) -> str:
    """Wrap *text* in an ANSI escape if *stream* supports color."""
    if _use_color(stream):
        return f"{code}{text}{_RESET}"
    return text


# -- Progress ---------------------------------------------------------------

_OUTCOME_LABELS: dict[OutcomeKind, tuple[str, str]] = {
    OutcomeKind.PASS: (_GREEN, "PASS"),
    OutcomeKind.FAIL: (_RED, "FAIL"),
    OutcomeKind.TIMEOUT: (_RED_BOLD, "TIMEOUT"),
    OutcomeKind.CRASH: (_RED_BOLD, "CRASH"),
    OutcomeKind.COMPILE_ERROR: (_RED_BOLD, "COMPILE ERROR"),
    OutcomeKind.SKIPPED: (_DIM, "SKIP"),
}


def render_starting(
    count: int,
    *,
    stream: IO[str] | None = None,
) -> None:
    """Render the 'Starting N targets' header line."""
    out = stream or sys.stderr
    label = "target" if count == 1 else "targets"
    out.write(f"{_c(out, _BOLD, 'Starting')} {count} {label}\n")


def _write_output_block(out: IO[str], outcome: Outcome) -> None:
    """Write full stdout + stderr block for an outcome."""
    if outcome.stdout:
        for line in outcome.stdout.splitlines():
            out.write(f"  {_c(out, _DIM, line)}\n")
    if outcome.stderr:
        for line in outcome.stderr.splitlines():
            out.write(f"  {_c(out, _DIM, line)}\n")


def render_outcome(
    outcome: Outcome,
    *,
    stream: IO[str] | None = None,
    success_output: OutputMode = OutputMode.NEVER,
    failure_output: OutputMode = OutputMode.IMMEDIATE,
) -> None:
    """Render a single outcome as it completes (nextest style).

    Output visibility is controlled per outcome category:

    - *success_output* governs PASS outcomes.
    - *failure_output* governs FAIL / TIMEOUT / CRASH / COMPILE_ERROR.
    - SKIPPED outcomes never display output.
    """
    out = stream or sys.stderr
    code, text = _OUTCOME_LABELS[outcome.kind]
    label = _c(out, code, text)
    timing = _c(out, _DIM, f"[{outcome.elapsed_s:.1f}s]")
    out.write(f"{label} {timing} {outcome.command.target_id}\n")

    if outcome.kind == OutcomeKind.SKIPPED:
        return

    if outcome.kind == OutcomeKind.PASS:
        if success_output == OutputMode.IMMEDIATE:
            _write_output_block(out, outcome)
    else:
        if failure_output == OutputMode.IMMEDIATE:
            _write_output_block(out, outcome)
            if outcome.kind == OutcomeKind.TIMEOUT:
                out.write(f"  {_c(out, _DIM, f'(timed out after {outcome.elapsed_s:.1f}s)')}\n")


def make_progress_callback(
    stream: IO[str] | None = None,
    success_output: OutputMode = OutputMode.NEVER,
    failure_output: OutputMode = OutputMode.IMMEDIATE,
) -> Callable[[Outcome], None]:
    """Return a callback suitable for ``run_commands(on_complete=...)``."""
    out = stream or sys.stderr

    def _on_complete(outcome: Outcome) -> None:
        render_outcome(
            outcome,
            stream=out,
            success_output=success_output,
            failure_output=failure_output,
        )

    return _on_complete


def render_final_output(
    outcomes: tuple[Outcome, ...],
    *,
    stream: IO[str] | None = None,
    success_output: OutputMode = OutputMode.NEVER,
    failure_output: OutputMode = OutputMode.IMMEDIATE,
) -> None:
    """Render deferred output for outcomes whose mode is FINAL.

    Only outcomes matching ``OutputMode.FINAL`` for their category
    are rendered here. SKIPPED outcomes are always excluded.
    """
    out = stream or sys.stderr

    failures = [
        o
        for o in outcomes
        if o.kind not in (OutcomeKind.PASS, OutcomeKind.SKIPPED) and failure_output == OutputMode.FINAL
    ]
    successes = [o for o in outcomes if o.kind == OutcomeKind.PASS and success_output == OutputMode.FINAL]

    for o in failures:
        code, text = _OUTCOME_LABELS[o.kind]
        out.write(f"\n{_c(out, code, text)} {o.command.target_id}\n")
        _write_output_block(out, o)

    for o in successes:
        code, text = _OUTCOME_LABELS[o.kind]
        out.write(f"\n{_c(out, code, text)} {o.command.target_id}\n")
        _write_output_block(out, o)


# -- Summary ----------------------------------------------------------------


def render_summary(
    outcomes: tuple[Outcome, ...],
    *,
    stream: IO[str] | None = None,
) -> None:
    """Render the final summary counts line."""
    out = stream or sys.stderr

    if not outcomes:
        out.write("No targets to run.\n")
        return

    passed = sum(1 for o in outcomes if o.kind == OutcomeKind.PASS)
    failed = sum(1 for o in outcomes if o.kind == OutcomeKind.FAIL)
    timed_out = sum(1 for o in outcomes if o.kind == OutcomeKind.TIMEOUT)
    crashed = sum(1 for o in outcomes if o.kind == OutcomeKind.CRASH)
    compile_errors = sum(1 for o in outcomes if o.kind == OutcomeKind.COMPILE_ERROR)
    skipped = sum(1 for o in outcomes if o.kind == OutcomeKind.SKIPPED)
    total_time = sum(o.elapsed_s for o in outcomes)

    parts: list[str] = []
    if passed:
        parts.append(_c(out, _GREEN, f"{passed} passed"))
    if failed:
        parts.append(_c(out, _RED, f"{failed} failed"))
    if timed_out:
        parts.append(_c(out, _RED_BOLD, f"{timed_out} timed out"))
    if crashed:
        parts.append(_c(out, _RED_BOLD, f"{crashed} crashed"))
    if compile_errors:
        parts.append(_c(out, _RED_BOLD, f"{compile_errors} compile error(s)"))
    if skipped:
        parts.append(_c(out, _DIM, f"{skipped} skipped"))

    summary = ", ".join(parts) if parts else "0 targets"
    out.write(f"\n{_c(out, _BOLD, summary)} in {total_time:.2f}s\n")


# -- Dry-run ----------------------------------------------------------------


def render_dry_run(
    commands: tuple[Command, ...],
    *,
    stream: IO[str] | None = None,
    compact: bool = True,
) -> None:
    """Render planned commands.

    When *compact* is True (default), consecutive commands with identical
    flags are grouped. When False, every command is rendered individually.
    """
    out = stream or sys.stderr

    if not compact:
        for cmd in commands:
            _render_single_command(cmd, out)
        return

    groups = _group_commands(commands)
    for group in groups:
        if len(group) == 1:
            _render_single_command(group[0], out)
        else:
            _render_grouped_commands(group, out)


def _command_group_key(cmd: Command) -> tuple[CommandKind, tuple[str, ...], tuple[str, ...], str]:
    """Key for grouping commands with identical flags."""
    argv_without_source = tuple(a for a in cmd.argv if not a.endswith(".mojo") and not a.endswith(".mojoc"))
    return (cmd.kind, argv_without_source, cmd.depends_on, str(cmd.cwd))


def _group_commands(commands: tuple[Command, ...]) -> list[list[Command]]:
    """Group consecutive commands that share the same flags."""
    if not commands:
        return []
    groups: list[list[Command]] = []
    current: list[Command] = [commands[0]]
    current_key = _command_group_key(commands[0])

    for cmd in commands[1:]:
        key = _command_group_key(cmd)
        if key == current_key:
            current.append(cmd)
        else:
            groups.append(current)
            current = [cmd]
            current_key = key
    groups.append(current)
    return groups


def _shorten_argv(cmd: Command) -> str:
    """Shorten argv for display: relativize paths against cwd."""
    cwd = str(cmd.cwd)
    parts: list[str] = []
    for a in cmd.argv:
        if a.startswith(cwd + "/"):
            parts.append(os.path.relpath(a, cwd))
        elif "/" in a and not a.startswith("-"):
            try:
                rel = os.path.relpath(a, cwd)
                if not rel.startswith("../../"):
                    parts.append(rel)
                else:
                    parts.append(a)
            except ValueError:
                parts.append(a)
        else:
            parts.append(a)
    return " ".join(parts)


def _render_single_command(cmd: Command, out: IO[str]) -> None:
    """Render a single command (non-grouped)."""
    kind_tag = _c(out, _CYAN, f"[{cmd.kind.value}]")
    target = _c(out, _BOLD, cmd.target_id)
    out.write(f"{kind_tag} {target}\n")
    out.write(f"  {_c(out, _DIM, f'cwd: {cmd.cwd}')}\n")
    out.write(f"  {_shorten_argv(cmd)}\n")
    if cmd.depends_on:
        deps = ", ".join(cmd.depends_on)
        out.write(f"  {_c(out, _DIM, f'depends_on: {deps}')}\n")
    out.write("\n")


def _render_grouped_commands(group: list[Command], out: IO[str]) -> None:
    """Render a group of commands that share the same flags."""
    first = group[0]
    kind_tag = _c(out, _CYAN, f"[{first.kind.value}]")
    count = _c(out, _BOLD, f"{len(group)} targets")

    deps_str = ""
    if first.depends_on:
        deps = ", ".join(first.depends_on)
        deps_str = f" {_c(out, _DIM, f'(depends_on: {deps})')}"

    out.write(f"{kind_tag} {count}{deps_str}\n")

    template_parts: list[str] = []
    for a in first.argv:
        if a.endswith((".mojo", ".mojoc")):
            template_parts.append("<source>")
        else:
            cwd = str(first.cwd)
            if a.startswith(cwd + "/"):
                template_parts.append(os.path.relpath(a, cwd))
            else:
                try:
                    rel = os.path.relpath(a, cwd)
                    if not rel.startswith("../../"):
                        template_parts.append(rel)
                    else:
                        template_parts.append(a)
                except ValueError:
                    template_parts.append(a)
    out.write(f"  {' '.join(template_parts)}\n")

    max_shown = 5
    out.write(f"  {_c(out, _DIM, 'targets:')}\n")
    for cmd in group[:max_shown]:
        out.write(f"    {_c(out, _DIM, cmd.target_id)}\n")
    remaining = len(group) - max_shown
    if remaining > 0:
        out.write(f"    {_c(out, _DIM, f'... ({remaining} more)')}\n")
    out.write("\n")


# -- Diagnostics ------------------------------------------------------------


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
