"""Execute Commands via subprocess, producing Outcomes.

Pass/fail is exit code only: 0 = pass, non-zero = fail. Timeout and
signal-death are distinct failure kinds. The environment is constructed
from Command.env, never inherited from the host process.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from mojox_core import Command, CommandKind

from .cache import compute_cache_key, read_cache_meta, write_cache_meta
from .diagnostics import parse_diagnostics
from .types import Outcome, OutcomeKind


@dataclass(frozen=True)
class CacheContext:
    """Context for binary cache lookups passed to the executor.

    Groups the precomputed hashes and metadata directory needed to
    evaluate cache keys for AOT-compiled test binaries.

    Attributes:
        project_hash: Hash of the project's library source trees.
        tests_tree_hash: Hash of the test directory trees.
        compiler_version: Mojo compiler version string.
        meta_dir: Directory for per-target cache metadata JSON files.
        enabled: Whether cache lookups are active. When ``False`` the
            compound build-then-execute still runs, but every lookup
            is a guaranteed miss.
        runtime_args: Extra command-line arguments appended to the
            compiled binary's argv at execution time. Used by bundle
            mode to pass a ``-k`` filter pattern to the test binary.
    """

    project_hash: str
    tests_tree_hash: str
    compiler_version: str
    meta_dir: Path
    enabled: bool = True
    runtime_args: tuple[str, ...] = ()


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


def _extract_test_source_and_flags(
    argv: tuple[str, ...],
) -> tuple[str | None, tuple[str, ...]]:
    """Extract the ``.mojo`` source path and compiler flags from a BUILD_TEST argv.

    Parses the command argv to separate the test source file from
    compiler flags, skipping the mojo binary (``argv[0]``), the
    ``"build"`` subcommand (``argv[1]``), ``-o``, and the output path
    that follows ``-o``.

    Args:
        argv: The full argv tuple from a ``BUILD_TEST`` :class:`Command`.

    Returns:
        A ``(source_path, flags)`` tuple.  ``source_path`` is ``None``
        when no ``.mojo`` file was found in *argv*.
    """
    source: str | None = None
    flags: list[str] = []
    skip_next = False

    for i, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if i == 0:  # mojo binary path
            continue
        if i == 1 and arg == "build":
            continue
        if arg == "-o":
            skip_next = True
            continue
        if arg.endswith(".mojo"):
            source = arg
            continue
        flags.append(arg)

    return source, tuple(flags)


def _resolve_cache_for_build_test(
    cmd: Command,
    cache_context: CacheContext,
    extra_env: dict[str, str] | None,
    include_paths: tuple[str, ...],
) -> Outcome:
    """Route a BUILD_TEST command through the cached test runner.

    Computes the cache key from the command's argv and the shared
    :class:`CacheContext`, then delegates to :func:`run_cached_test`.

    When caching is disabled (``cache_context.enabled is False``), the
    binary is built to a temporary directory and cache metadata is not
    written, preserving any existing cache state for future runs.

    Args:
        cmd: A ``BUILD_TEST`` :class:`Command`.
        cache_context: Shared cache context from the CLI layer.
        extra_env: Additional environment variables.
        include_paths: Dependency include directories.

    Returns:
        An :class:`Outcome` from the compound build+execute operation.
    """
    import uuid

    if cache_context.enabled:
        source_str, flags = _extract_test_source_and_flags(cmd.argv)
        if source_str is not None:
            source_path = Path(source_str)
            if not source_path.is_absolute():
                source_path = Path(cmd.cwd) / source_path
            cache_key = compute_cache_key(
                test_source=source_path,
                project_hash=cache_context.project_hash,
                tests_tree_hash=cache_context.tests_tree_hash,
                compiler_version=cache_context.compiler_version,
                flags=flags,
            )
        else:
            cache_key = uuid.uuid4().hex
    else:
        cache_key = uuid.uuid4().hex

    return run_cached_test(
        cmd,
        cache_key=cache_key,
        meta_dir=cache_context.meta_dir,
        compiler_version=cache_context.compiler_version,
        extra_env=extra_env,
        include_paths=include_paths,
        skip_cache_write=not cache_context.enabled,
        runtime_args=cache_context.runtime_args,
    )


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
    cache_context: CacheContext | None = None,
) -> Outcome:
    """Run a command, calling on_start from the worker thread first.

    When *cache_context* is provided and the command is a ``BUILD_TEST``,
    the execution is routed through the cached test runner instead of
    the plain :func:`run_command` path.

    Args:
        cmd: The command to execute.
        extra_env: Additional environment variables to merge.
        include_paths: Dependency include directories.
        on_start: Optional callback invoked before execution.
        cache_context: Optional cache context for AOT test binaries.

    Returns:
        An Outcome describing the result.
    """
    if on_start is not None:
        on_start(cmd)
    if cache_context is not None and cmd.kind == CommandKind.BUILD_TEST:
        return _resolve_cache_for_build_test(cmd, cache_context, extra_env, include_paths)
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
    cache_context: CacheContext | None = None,
) -> tuple[Outcome, ...]:
    """Run a sequence of Commands with concurrency and dependency ordering.

    Commands whose ``depends_on`` references have not all completed
    successfully are skipped with a SKIPPED outcome. Independent commands
    run concurrently up to ``max_workers``.

    When ``fail_fast`` is True, the first non-PASS outcome cancels
    queued commands and skips remaining phases.

    When *cache_context* is provided, ``BUILD_TEST`` commands are routed
    through the cached test runner for compound build-then-execute with
    optional cache lookups.

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
        cache_context: Optional cache context for AOT test binary
            caching. When ``None``, ``BUILD_TEST`` commands run via the
            plain :func:`run_command` path (build only, no execution).

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
            cache_context,
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
                cache_context,
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
    cache_context: CacheContext | None = None,
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
        cache_context: Optional cache context for AOT test binary caching.
    """

    def _record(idx: int, outcome: Outcome) -> None:
        results[idx] = outcome
        completed[outcome.command.target_id] = outcome
        if on_complete is not None:
            on_complete(outcome)

    if len(phase) == 1:
        idx, cmd = phase[0]
        outcome = _run_or_skip(cmd, completed, extra_env, include_paths, on_start, cache_context)
        _record(idx, outcome)
        return fail_fast and outcome.kind != OutcomeKind.PASS

    workers = min(max_workers, len(phase))
    triggered = False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx: dict[Future[Outcome], tuple[int, Command]] = {}
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
                cache_context,
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
    cache_context: CacheContext | None = None,
) -> Outcome:
    """Run a command or skip it if dependencies failed.

    When *cache_context* is provided and the command is a ``BUILD_TEST``,
    execution is routed through the cached test runner.

    Args:
        cmd: The command to execute.
        completed: Mapping of target_id to Outcome for finished commands.
        extra_env: Additional env vars merged into the command.
        include_paths: Dependency include directories whose ``lib/``
            subdirectories are added to the dynamic linker search path.
        on_start: Optional callback invoked before execution begins.
        cache_context: Optional cache context for AOT test binary caching.
    """
    skip = _check_dependencies(cmd, completed)
    if skip is not None:
        return skip
    if on_start is not None:
        on_start(cmd)
    if cache_context is not None and cmd.kind == CommandKind.BUILD_TEST:
        return _resolve_cache_for_build_test(cmd, cache_context, extra_env, include_paths)
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


def _remaining_timeout(total: int | None, elapsed: float) -> int | None:
    """Compute remaining timeout budget after the build step.

    When ``total`` is ``None`` (no timeout), returns ``None``.
    Otherwise returns ``max(1, total - int(elapsed))``, guaranteeing
    at least one second for the execution step.

    Args:
        total: The original timeout budget in seconds, or ``None``.
        elapsed: Wall-clock seconds consumed by the build step.

    Returns:
        Remaining seconds (at least 1), or ``None`` when unlimited.
    """
    if total is None:
        return None
    return max(1, total - int(elapsed))


def _execute_binary(
    cmd: Command,
    binary_path: str,
    *,
    extra_env: dict[str, str] | None = None,
    include_paths: tuple[str, ...] = (),
    remaining_timeout: int | None = None,
    runtime_args: tuple[str, ...] = (),
) -> Outcome:
    """Execute a compiled test binary by constructing a new Command for it.

    Builds a fresh :class:`Command` pointing at the binary and delegates
    to :func:`run_command`.  The new command inherits *cmd*'s working
    directory, environment, and target metadata, but uses the binary as
    ``argv[0]`` and the caller-supplied ``remaining_timeout``.

    Args:
        cmd: The original ``BUILD_TEST`` command (used for cwd, env,
            target_id, etc.).
        binary_path: Absolute or relative path to the compiled binary.
        extra_env: Additional environment variables forwarded to
            :func:`run_command`.
        include_paths: Dependency include directories whose ``lib/``
            subdirectories are added to the dynamic linker search path.
        remaining_timeout: Timeout budget for execution, or ``None``
            for unlimited.
        runtime_args: Extra arguments appended to the binary's argv.
            Used by bundle mode to pass ``-k`` filter patterns.

    Returns:
        An :class:`Outcome` from running the binary.
    """
    exec_cmd = Command(
        argv=(binary_path, *runtime_args),
        cwd=cmd.cwd,
        env=cmd.env,
        kind=CommandKind.RUN_TEST,
        target_id=cmd.target_id,
        timeout_s=remaining_timeout,
        outputs=(),
        depends_on=(),
    )
    return run_command(exec_cmd, extra_env=extra_env, include_paths=include_paths)


def run_cached_test(
    cmd: Command,
    *,
    cache_key: str,
    meta_dir: Path,
    compiler_version: str,
    extra_env: dict[str, str] | None = None,
    include_paths: tuple[str, ...] = (),
    skip_cache_write: bool = False,
    runtime_args: tuple[str, ...] = (),
) -> Outcome:
    """Build and execute a test binary with cache support.

    Implements the compound build-then-execute workflow for AOT test
    binaries.  On a cache hit the build step is skipped entirely; on a
    miss the binary is compiled first, metadata is written, and then the
    binary is executed.

    The *cmd* must be a ``BUILD_TEST`` command whose ``outputs[0]``
    gives the expected binary path.  ``timeout_s`` is a shared budget:
    the build step may consume part of it, and the remainder (minimum
    1 s) is given to the execution step.

    On a miss the binary is built to a unique ``mkstemp`` sibling, the
    old meta is deleted, the binary is renamed into place, and only then
    is the new meta written; a missing or unreadable meta is a miss.
    Temp files are removed on every failure path.

    When *skip_cache_write* is True (``--no-cache``), the binary is
    built into a private ``mkdtemp`` directory removed after execution.
    No cache metadata is written, preserving existing cache state.

    Args:
        cmd: A ``BUILD_TEST`` :class:`Command` produced by the planner.
        cache_key: Precomputed composite cache key for this test.
        meta_dir: Directory where ``<target_name>.json`` metadata files
            are stored.
        compiler_version: Mojo compiler version string (written into
            cache metadata on a miss).
        extra_env: Additional environment variables forwarded to both
            the build and execution steps.
        include_paths: Dependency include directories whose ``lib/``
            subdirectories are added to the dynamic linker search path.
        skip_cache_write: If True, build to a temp path, skip metadata
            writes, and clean up the binary after execution.
        runtime_args: Extra arguments appended to the binary's argv
            at execution time. Used by bundle mode to pass ``-k``
            filter patterns to the compiled test binary.

    Returns:
        An :class:`Outcome` for the test execution (or a
        ``COMPILE_ERROR`` outcome if the build fails).
    """
    binary_path = Path(cmd.cwd) / cmd.outputs[0]
    meta_path = meta_dir / f"{binary_path.name}.json"

    # --- cache hit path (skipped when cache writes are disabled) ---
    if not skip_cache_write:
        stored_key = read_cache_meta(meta_path)
        if stored_key == cache_key and binary_path.is_file():
            return _execute_binary(
                cmd,
                str(binary_path),
                extra_env=extra_env,
                include_paths=include_paths,
                remaining_timeout=cmd.timeout_s,
                runtime_args=runtime_args,
            )

    # --- cache miss: build to a unique temp path ---
    # Worker threads share a pid, so temp names must come from mkstemp /
    # mkdtemp. The cache temp lives next to the binary so the publishing
    # rename stays on one filesystem and is atomic.
    tmp_dir: Path | None = None
    if skip_cache_write:
        tmp_dir = Path(tempfile.mkdtemp(prefix="mojox_nocache_"))
        tmp_binary = tmp_dir / binary_path.name
    else:
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=binary_path.parent, prefix=f".{binary_path.name}.", suffix=".tmp")
        os.close(fd)
        tmp_binary = Path(tmp_name)

    published = False
    try:
        build_argv = list(cmd.argv)
        try:
            o_idx = build_argv.index("-o")
            build_argv[o_idx + 1] = str(tmp_binary)
        except (ValueError, IndexError):
            build_argv.extend(["-o", str(tmp_binary)])

        build_cmd = Command(
            argv=tuple(build_argv),
            cwd=cmd.cwd,
            env=cmd.env,
            kind=cmd.kind,
            target_id=cmd.target_id,
            timeout_s=cmd.timeout_s,
            outputs=(str(tmp_binary),),
            depends_on=cmd.depends_on,
        )
        build_outcome = run_command(build_cmd, extra_env=extra_env, include_paths=include_paths)

        if build_outcome.kind != OutcomeKind.PASS:
            return Outcome(
                command=cmd,
                kind=OutcomeKind.COMPILE_ERROR,
                exit_code=build_outcome.exit_code,
                stdout=build_outcome.stdout,
                stderr=build_outcome.stderr,
                diagnostics=build_outcome.diagnostics,
                elapsed_s=build_outcome.elapsed_s,
            )

        # mkstemp pre-creates an empty file, so "empty" also means "not built".
        if not tmp_binary.is_file() or tmp_binary.stat().st_size == 0:
            return Outcome(
                command=cmd,
                kind=OutcomeKind.COMPILE_ERROR,
                exit_code=0,
                stdout=build_outcome.stdout,
                stderr=(
                    f"Build succeeded but binary not found: {binary_path}\n"
                    + build_outcome.stderr
                ),
                diagnostics=build_outcome.diagnostics,
                elapsed_s=build_outcome.elapsed_s,
            )

        if skip_cache_write:
            exec_binary = tmp_binary
        else:
            # Drop the old key before the binary changes: an interrupt
            # between rename and meta write then reads as a miss, never
            # as the old key vouching for the new binary.
            meta_path.unlink(missing_ok=True)
            os.replace(tmp_binary, binary_path)
            published = True
            write_cache_meta(
                meta_path,
                cache_key=cache_key,
                compiler_version=compiler_version,
            )
            exec_binary = binary_path

        # --- execute ---
        timeout_left = _remaining_timeout(cmd.timeout_s, build_outcome.elapsed_s)
        exec_outcome = _execute_binary(
            cmd,
            str(exec_binary),
            extra_env=extra_env,
            include_paths=include_paths,
            remaining_timeout=timeout_left,
            runtime_args=runtime_args,
        )
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        elif not published:
            tmp_binary.unlink(missing_ok=True)

    # combine: prepend build warnings to execution stderr
    combined_stderr = exec_outcome.stderr
    if build_outcome.stderr:
        combined_stderr = build_outcome.stderr + combined_stderr

    total_elapsed = build_outcome.elapsed_s + exec_outcome.elapsed_s

    return Outcome(
        command=cmd,
        kind=exec_outcome.kind,
        exit_code=exec_outcome.exit_code,
        stdout=exec_outcome.stdout,
        stderr=combined_stderr,
        diagnostics=build_outcome.diagnostics + exec_outcome.diagnostics,
        elapsed_s=total_elapsed,
    )
