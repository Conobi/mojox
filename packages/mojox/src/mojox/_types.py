"""Execution outcome types for the mojox CLI layer.

These types represent the result of running a Command via subprocess.
They live in the mojox package (not mojox-core) because they are
exec-layer concerns, not planner concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mojox_core import Command, Diagnostic


class OutcomeKind(Enum):
    """Classification of a command execution result."""

    PASS = "pass"
    FAIL = "fail"
    COMPILE_ERROR = "compile-error"
    TIMEOUT = "timeout"
    CRASH = "crash"


@dataclass(frozen=True)
class Outcome:
    """The result of executing a single Command."""

    command: Command
    kind: OutcomeKind
    exit_code: int | None
    stdout: str
    stderr: str
    diagnostics: tuple[Diagnostic, ...]
    elapsed_s: float
