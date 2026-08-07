"""Execute Commands via subprocess, producing Outcomes.

Pass/fail is exit code only: 0 = pass, non-zero = fail. Timeout and
signal-death are distinct failure kinds. The environment is constructed
from Command.env, never inherited from the host process.
"""

from __future__ import annotations

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from mojox_core import Command

from ._diagnostics import parse_diagnostics
from ._ore import OreContext
from ._types import Outcome, OutcomeKind


def run_command(
    cmd: Command,
    *,
    extra_env: dict[str, str] | None = None,
    ore_context: OreContext | None = None,
) -> Outcome:
    """Run a single Command and return its Outcome.

    The environment is constructed from ``cmd.env`` merged with
    ``extra_env`` (LocalSettings.env). The host environment is never
    inherited.

    When *ore_context* is enabled and the command kind is ore-eligible,
    the ore pipeline is attempted first. If it succeeds, the ore-
    accelerated Outcome is returned. Otherwise execution falls through
    to the standard subprocess path.

    Args:
        cmd: The command to execute.
        extra_env: Additional environment variables to merge (from
            LocalSettings.env). These are added under cmd.env, with
            cmd.env values taking precedence for conflicts.
        ore_context: Optional ore acceleration context. When provided
            and enabled, eligible commands are dispatched through the
            ore pipeline before falling back to the standard path.

    Returns:
        An Outcome describing the result.
    """
    if ore_context is not None and ore_context.enabled:
        from ._ore import is_ore_eligible, _try_ore_run

        if is_ore_eligible(cmd.kind):
            result = _try_ore_run(cmd, ore_context, extra_env=extra_env)
            if result is not None:
                return result
    elif ore_context is not None and not ore_context.enabled:
        import sys
        print("ore: disabled (release profile or --no-ore)", file=sys.stderr)
    # Fall through to standard subprocess path
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
    except OSError as e:
        elapsed = time.monotonic() - start
        return Outcome(
            command=cmd,
            kind=OutcomeKind.COMPILE_ERROR,
            exit_code=None,
            stdout="",
            stderr=f"OS error running {cmd.argv[0]}: {e}",
            diagnostics=(),
            elapsed_s=elapsed,
        )

    elapsed = time.monotonic() - start

    if result.returncode < 0:
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


def run_commands(
    commands: tuple[Command, ...],
    *,
    max_workers: int = 1,
    extra_env: dict[str, str] | None = None,
    ore_context: OreContext | None = None,
) -> tuple[Outcome, ...]:
    """Run a sequence of Commands with concurrency and dependency ordering.

    Commands whose ``depends_on`` references have not all completed
    successfully are skipped with a FAIL outcome. Independent commands
    run concurrently up to ``max_workers``.

    The current two-phase implementation supports commands with at most
    one level of dependencies (e.g., precompile -> test). Deeper
    dependency chains would require topological-order execution.

    Args:
        commands: The commands to execute, in planner order.
        max_workers: Maximum concurrent subprocess invocations.
        extra_env: Additional env vars merged into each command
            (from LocalSettings.env).
        ore_context: Optional ore acceleration context forwarded to
            each :func:`run_command` invocation.

    Returns:
        A tuple of Outcomes in the same order as the input commands.
    """
    if not commands:
        return ()

    completed: dict[str, Outcome] = {}
    results: list[Outcome | None] = [None] * len(commands)

    no_deps: list[tuple[int, Command]] = []
    has_deps: list[tuple[int, Command]] = []

    for i, cmd in enumerate(commands):
        if cmd.depends_on:
            has_deps.append((i, cmd))
        else:
            no_deps.append((i, cmd))

    if no_deps:
        _run_phase(no_deps, completed, results, max_workers, extra_env, ore_context)
    if has_deps:
        _run_phase(has_deps, completed, results, max_workers, extra_env, ore_context)

    assert all(r is not None for r in results), "unfilled result slots"
    return tuple(results)  # type: ignore[arg-type]


def _run_phase(
    phase: list[tuple[int, Command]],
    completed: dict[str, Outcome],
    results: list[Outcome | None],
    max_workers: int,
    extra_env: dict[str, str] | None,
    ore_context: OreContext | None = None,
) -> None:
    """Run a batch of commands concurrently, checking deps before submission.

    Args:
        phase: List of (index, Command) pairs to execute.
        completed: Mapping of target_id to Outcome for finished commands.
        results: Mutable list of results to populate by index.
        max_workers: Maximum concurrent subprocess invocations.
        extra_env: Additional env vars merged into each command.
        ore_context: Optional ore acceleration context forwarded to
            each :func:`run_command` invocation.
    """
    if len(phase) == 1:
        idx, cmd = phase[0]
        outcome = _run_or_skip(cmd, completed, extra_env, ore_context)
        results[idx] = outcome
        completed[cmd.target_id] = outcome
        return

    workers = min(max_workers, len(phase))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx: dict = {}
        for idx, cmd in phase:
            skip_outcome = _check_dependencies(cmd, completed)
            if skip_outcome is not None:
                results[idx] = skip_outcome
                completed[cmd.target_id] = skip_outcome
                continue
            future = pool.submit(
                run_command, cmd, extra_env=extra_env, ore_context=ore_context,
            )
            future_to_idx[future] = (idx, cmd)

        for future in as_completed(future_to_idx):
            idx, cmd = future_to_idx[future]
            outcome = future.result()
            results[idx] = outcome
            completed[cmd.target_id] = outcome


def _run_or_skip(
    cmd: Command,
    completed: dict[str, Outcome],
    extra_env: dict[str, str] | None,
    ore_context: OreContext | None = None,
) -> Outcome:
    """Run a command or skip it if dependencies failed.

    Args:
        cmd: The command to execute.
        completed: Mapping of target_id to Outcome for finished commands.
        extra_env: Additional env vars merged into the command.
        ore_context: Optional ore acceleration context forwarded to
            :func:`run_command`.
    """
    skip = _check_dependencies(cmd, completed)
    if skip is not None:
        return skip
    return run_command(cmd, extra_env=extra_env, ore_context=ore_context)


def _check_dependencies(
    cmd: Command,
    completed: dict[str, Outcome],
) -> Outcome | None:
    """Check if all dependencies completed successfully.

    Returns a skip Outcome if any dependency failed, None otherwise.
    """
    for dep_id in cmd.depends_on:
        dep_outcome = completed.get(dep_id)
        if dep_outcome is None or dep_outcome.kind != OutcomeKind.PASS:
            return Outcome(
                command=cmd,
                kind=OutcomeKind.FAIL,
                exit_code=None,
                stdout="",
                stderr=f"Dependency {dep_id!r} failed; skipping {cmd.target_id!r}.",
                diagnostics=(),
                elapsed_s=0.0,
            )
    return None
