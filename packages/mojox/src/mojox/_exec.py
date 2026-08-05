"""Execute Commands via subprocess, producing Outcomes.

Pass/fail is exit code only: 0 = pass, non-zero = fail. Timeout and
signal-death are distinct failure kinds. The environment is constructed
from Command.env, never inherited from the host process.
"""

from __future__ import annotations

import subprocess
import time

from mojox_core import Command

from ._diagnostics import parse_diagnostics
from ._types import Outcome, OutcomeKind


def run_command(
    cmd: Command,
    *,
    extra_env: dict[str, str] | None = None,
) -> Outcome:
    """Run a single Command and return its Outcome.

    The environment is constructed from ``cmd.env`` merged with
    ``extra_env`` (LocalSettings.env). The host environment is never
    inherited.

    Args:
        cmd: The command to execute.
        extra_env: Additional environment variables to merge (from
            LocalSettings.env). These are added under cmd.env, with
            cmd.env values taking precedence for conflicts.

    Returns:
        An Outcome describing the result.
    """
    env = dict(cmd.env)
    if extra_env:
        merged = dict(extra_env)
        merged.update(env)
        env = merged

    start = time.monotonic()
    try:
        result = subprocess.run(
            list(cmd.argv),
            cwd=str(cmd.cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=cmd.timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = time.monotonic() - start
        stdout = e.stdout or ""
        stderr = e.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return Outcome(
            command=cmd,
            kind=OutcomeKind.TIMEOUT,
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            diagnostics=parse_diagnostics(stderr),
            elapsed_s=elapsed,
        )
    except FileNotFoundError:
        elapsed = time.monotonic() - start
        return Outcome(
            command=cmd,
            kind=OutcomeKind.COMPILE_ERROR,
            exit_code=None,
            stdout="",
            stderr=f"command not found: {cmd.argv[0]}",
            diagnostics=(),
            elapsed_s=elapsed,
        )

    elapsed = time.monotonic() - start

    if result.returncode is None or result.returncode < 0:
        kind = OutcomeKind.CRASH
        exit_code = result.returncode
    elif result.returncode == 0:
        kind = OutcomeKind.PASS
        exit_code = 0
    else:
        kind = OutcomeKind.FAIL
        exit_code = result.returncode

    return Outcome(
        command=cmd,
        kind=kind,
        exit_code=exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        diagnostics=parse_diagnostics(result.stderr),
        elapsed_s=elapsed,
    )
