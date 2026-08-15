"""Execute Commands via subprocess, producing Outcomes.

Pass/fail is exit code only: 0 = pass, non-zero = fail. Timeout and
signal-death are distinct failure kinds. The environment is constructed
from Command.env, never inherited from the host process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path

from mojox_core import Command

from ._diagnostics import parse_diagnostics
from ._types import Outcome, OutcomeKind


def _inject_native_lib_paths(
    env: dict[str, str],
    include_paths: tuple[str, ...],
) -> None:
    """Add native lib dirs from include paths to the library search path.

    Dependencies may ship native shared libraries (e.g. librustls_mojo.so)
    in a ``lib/`` subdirectory of their include path. The dynamic linker
    needs to find them at runtime.

    Modifies *env* in place, appending to ``LD_LIBRARY_PATH`` (Linux)
    or ``DYLD_LIBRARY_PATH`` (macOS).
    """
    lib_dirs: list[str] = []
    for inc in include_paths:
        lib_dir = Path(inc) / "lib"
        if lib_dir.is_dir():
            lib_dirs.append(str(lib_dir))

    if not lib_dirs:
        return

    env_key = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    existing = env.get(env_key, "")
    parts = [existing] if existing else []
    parts.extend(lib_dirs)
    env[env_key] = os.pathsep.join(parts)


def run_command(
    cmd: Command,
    *,
    extra_env: dict[str, str] | None = None,
    include_paths: tuple[str, ...] = (),
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
        include_paths: Dependency include directories whose ``lib/``
            subdirectories are added to the dynamic linker search path.

    Returns:
        An Outcome describing the result.
    """
    for output in cmd.outputs:
        Path(output).parent.mkdir(parents=True, exist_ok=True)

    env = dict(cmd.env)
    if extra_env:
        merged = dict(extra_env)
        merged.update(env)
        env = merged

    if include_paths:
        _inject_native_lib_paths(env, include_paths)

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


def _run_with_start(
    cmd: Command,
    extra_env: dict[str, str] | None,
    include_paths: tuple[str, ...],
    on_start: Callable[[Command], None] | None,
) -> Outcome:
    """Run a command, calling on_start from the worker thread first.

    Args:
        cmd: The command to execute.
        extra_env: Additional environment variables to merge.
        include_paths: Dependency include directories.
        on_start: Optional callback invoked before execution.

    Returns:
        An Outcome describing the result.
    """
    if on_start is not None:
        on_start(cmd)
    return run_command(cmd, extra_env=extra_env, include_paths=include_paths)


def run_commands(
    commands: tuple[Command, ...],
    *,
    max_workers: int = 1,
    extra_env: dict[str, str] | None = None,
    include_paths: tuple[str, ...] = (),
    on_start: Callable[[Command], None] | None = None,
    on_complete: Callable[[Outcome], None] | None = None,
    fail_fast: bool = False,
) -> tuple[Outcome, ...]:
    """Run a sequence of Commands with concurrency and dependency ordering.

    Commands whose ``depends_on`` references have not all completed
    successfully are skipped with a SKIPPED outcome. Independent commands
    run concurrently up to ``max_workers``.

    When ``fail_fast`` is True, the first non-PASS outcome cancels
    queued commands and skips remaining phases.

    The current two-phase implementation supports commands with at most
    one level of dependencies (e.g., precompile -> test). Deeper
    dependency chains would require topological-order execution.

    Args:
        commands: The commands to execute, in planner order.
        max_workers: Maximum concurrent subprocess invocations.
        extra_env: Additional env vars merged into each command
            (from LocalSettings.env).
        include_paths: Dependency include directories whose ``lib/``
            subdirectories are added to the dynamic linker search path.
        on_start: Optional callback invoked with each Command just before
            it begins execution. Called from the worker thread; must be
            thread-safe.
        on_complete: Optional callback invoked with each Outcome as it
            completes. Called from the executor thread; must be thread-safe.
        fail_fast: If True, cancel remaining commands after the first
            non-PASS outcome.

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

    triggered = False
    if no_deps:
        triggered = _run_phase(
            no_deps,
            completed,
            results,
            max_workers,
            extra_env,
            include_paths,
            on_start,
            on_complete,
            fail_fast,
        )

    if has_deps:
        if triggered:
            for idx, cmd in has_deps:
                outcome = Outcome(
                    command=cmd,
                    kind=OutcomeKind.SKIPPED,
                    exit_code=None,
                    stdout="",
                    stderr="Cancelled by --fail-fast",
                    diagnostics=(),
                    elapsed_s=0.0,
                )
                results[idx] = outcome
                completed[cmd.target_id] = outcome
                if on_complete is not None:
                    on_complete(outcome)
        else:
            _run_phase(
                has_deps,
                completed,
                results,
                max_workers,
                extra_env,
                include_paths,
                on_start,
                on_complete,
                fail_fast,
            )

    assert all(r is not None for r in results), "unfilled result slots"
    return tuple(results)  # type: ignore[arg-type]


def _run_phase(
    phase: list[tuple[int, Command]],
    completed: dict[str, Outcome],
    results: list[Outcome | None],
    max_workers: int,
    extra_env: dict[str, str] | None,
    include_paths: tuple[str, ...] = (),
    on_start: Callable[[Command], None] | None = None,
    on_complete: Callable[[Outcome], None] | None = None,
    fail_fast: bool = False,
) -> bool:
    """Run a batch of commands concurrently, checking deps before submission.

    Returns True if fail-fast was triggered during this phase.

    Args:
        phase: List of (index, Command) pairs to execute.
        completed: Mapping of target_id to Outcome for finished commands.
        results: Mutable list of results to populate by index.
        max_workers: Maximum concurrent subprocess invocations.
        extra_env: Additional env vars merged into each command.
        include_paths: Dependency include directories whose ``lib/``
            subdirectories are added to the dynamic linker search path.
        on_start: Optional callback invoked with each Command before
            execution begins.
        on_complete: Optional callback invoked with each Outcome.
        fail_fast: If True, cancel remaining futures after first non-PASS.
    """

    def _record(idx: int, outcome: Outcome) -> None:
        results[idx] = outcome
        completed[outcome.command.target_id] = outcome
        if on_complete is not None:
            on_complete(outcome)

    if len(phase) == 1:
        idx, cmd = phase[0]
        outcome = _run_or_skip(cmd, completed, extra_env, include_paths, on_start)
        _record(idx, outcome)
        return fail_fast and outcome.kind != OutcomeKind.PASS

    workers = min(max_workers, len(phase))
    triggered = False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx: dict = {}
        for idx, cmd in phase:
            skip_outcome = _check_dependencies(cmd, completed)
            if skip_outcome is not None:
                _record(idx, skip_outcome)
                continue
            future = pool.submit(
                _run_with_start,
                cmd,
                extra_env,
                include_paths,
                on_start,
            )
            future_to_idx[future] = (idx, cmd)

        try:
            for future in as_completed(future_to_idx):
                idx, cmd = future_to_idx[future]
                try:
                    outcome = future.result()
                except CancelledError:
                    outcome = Outcome(
                        command=cmd,
                        kind=OutcomeKind.SKIPPED,
                        exit_code=None,
                        stdout="",
                        stderr="Cancelled by --fail-fast",
                        diagnostics=(),
                        elapsed_s=0.0,
                    )
                _record(idx, outcome)
                if fail_fast and not triggered and outcome.kind != OutcomeKind.PASS:
                    triggered = True
                    for f in future_to_idx:
                        f.cancel()
        except KeyboardInterrupt:
            for f in future_to_idx:
                f.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise

    return triggered


def _run_or_skip(
    cmd: Command,
    completed: dict[str, Outcome],
    extra_env: dict[str, str] | None,
    include_paths: tuple[str, ...] = (),
    on_start: Callable[[Command], None] | None = None,
) -> Outcome:
    """Run a command or skip it if dependencies failed.

    Args:
        cmd: The command to execute.
        completed: Mapping of target_id to Outcome for finished commands.
        extra_env: Additional env vars merged into the command.
        include_paths: Dependency include directories whose ``lib/``
            subdirectories are added to the dynamic linker search path.
        on_start: Optional callback invoked before execution begins.
    """
    skip = _check_dependencies(cmd, completed)
    if skip is not None:
        return skip
    if on_start is not None:
        on_start(cmd)
    return run_command(cmd, extra_env=extra_env, include_paths=include_paths)


def _check_dependencies(
    cmd: Command,
    completed: dict[str, Outcome],
) -> Outcome | None:
    """Check if all dependencies completed successfully.

    Returns a SKIPPED Outcome if any dependency failed or is missing,
    None otherwise.
    """
    for dep_id in cmd.depends_on:
        dep_outcome = completed.get(dep_id)
        if dep_outcome is None or dep_outcome.kind != OutcomeKind.PASS:
            return Outcome(
                command=cmd,
                kind=OutcomeKind.SKIPPED,
                exit_code=None,
                stdout="",
                stderr=f"Dependency {dep_id!r} failed; skipping {cmd.target_id!r}.",
                diagnostics=(),
                elapsed_s=0.0,
            )
    return None
