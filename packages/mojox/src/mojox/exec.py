"""Execute Commands via subprocess, producing Outcomes.

Pass/fail is exit code only: 0 = pass, non-zero = fail. Timeout and
signal-death are distinct failure kinds. The environment is constructed
from Command.env, never inherited from the host process.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path

from mojox_core import Command, CommandKind
from mojox_core.plan import select_locale_env

from .cache import (
    compute_cache_key,
    compute_precompile_key,
    hash_directory_tree,
    read_cache_meta,
    stamp_include_dirs,
    write_cache_meta,
)
from .diagnostics import parse_diagnostics
from .types import Outcome, OutcomeKind


@dataclass(frozen=True)
class CacheContext:
    """Context for cache lookups passed to the executor.

    Groups the per-run inputs of the AOT binary cache key. They are
    computed once per invocation, not per built file. ``COMPILE_PACKAGE``
    commands only use ``compiler_version``, ``meta_dir`` and ``enabled``:
    their key is computed per command (see :func:`_precompile_cache_key`).

    Attributes:
        project_hash: Hash of the project's library source trees.
        tests_tree_hash: Hash of the test directory trees.
        deps_stamp: :func:`~mojox.cache.stamp_include_dirs` of the
            dependency include dirs, in ``-I`` order.
        compiler_version: Mojo compiler version string.
        meta_dir: Directory for per-target cache metadata JSON files.
        enabled: Whether cache lookups are active. When ``False`` the
            compound build-then-execute still runs, but every lookup
            is a guaranteed miss and no metadata is written.
        runtime_args: Extra command-line arguments appended to the
            compiled binary's argv at execution time. Used by bundle
            mode to pass a ``-k`` filter pattern to the test binary.
    """

    project_hash: str
    tests_tree_hash: str
    deps_stamp: str
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
) -> tuple[str, tuple[str, ...]]:
    """Split a planner BUILD_TEST argv into its source path and trailing flags.

    Relies on the fixed layout emitted by ``mojox_core.plan``:
    ``<mojo> build <source> -o <output> <flags...>``. Position, not suffix,
    identifies the source, so a flag value such as ``-D FOO=x.mojo`` can
    never be mistaken for it. Flags keep their argv order.

    Raises:
        ValueError: *argv* does not have that layout.
    """
    if len(argv) < 5 or argv[1] != "build" or argv[3] != "-o" or not argv[2].endswith((".mojo", ".\U0001f525")):
        raise ValueError(f"not a planner BUILD_TEST argv: {argv!r}")
    return argv[2], argv[5:]


def _merge_env(cmd_env: Mapping[str, str], extra_env: Mapping[str, str] | None) -> dict[str, str]:
    """Overlay *cmd_env* on the settings *extra_env*, except for the locale.

    The planner's env wins on conflicts, but its locale is only a host
    fallback. If the settings set any ``LANG``/``LC_*``, every planner
    locale var is dropped, so that a defaulted ``LC_ALL`` cannot mask a
    settings ``LANG``. The executor and both cache keys use this one merge,
    so a key always describes the env the child actually gets.
    """
    extra = dict(extra_env or {})
    if select_locale_env(extra):
        planner_locale = select_locale_env(cmd_env)
        cmd_env = {name: value for name, value in cmd_env.items() if name not in planner_locale}
    return {**extra, **cmd_env}


def _build_test_cache_key(
    cmd: Command,
    cache_context: CacheContext,
    extra_env: dict[str, str] | None,
) -> str | None:
    """Return the cache key for a BUILD_TEST command, or ``None`` if uncacheable.

    Beyond the per-run :class:`CacheContext` inputs, the key covers the
    compiler binary (``argv[0]``), the flags in argv order, and the build
    environment as :func:`_merge_env` builds it for :func:`run_command`. An argv that does not match the planner's
    layout is uncacheable rather than keyed on a guess. So is a source that
    cannot be read (missing, a directory): the build then runs and the
    compiler reports it.
    """
    try:
        source_str, flags = _extract_test_source_and_flags(cmd.argv)
    except ValueError:
        return None
    source_path = Path(source_str)
    if not source_path.is_absolute():
        source_path = Path(cmd.cwd) / source_path
    try:
        return compute_cache_key(
            test_source=source_path,
            project_hash=cache_context.project_hash,
            tests_tree_hash=cache_context.tests_tree_hash,
            deps_stamp=cache_context.deps_stamp,
            compiler_version=cache_context.compiler_version,
            mojo_path=cmd.argv[0],
            flags=flags,
            env=_merge_env(cmd.env, extra_env),
        )
    except OSError:
        return None


def _resolve_cache_for_build_test(
    cmd: Command,
    cache_context: CacheContext,
    extra_env: dict[str, str] | None,
    include_paths: tuple[str, ...],
) -> Outcome:
    """Route a BUILD_TEST command through the cached test runner.

    Keys the command with :func:`_build_test_cache_key`, then delegates to
    :func:`run_cached_test`. An uncacheable command gets a random key, so
    it always rebuilds.

    When caching is disabled (``cache_context.enabled is False``), the
    binary is built to a temporary directory and cache metadata is not
    written, preserving any existing cache state for future runs.
    """
    import uuid

    cache_key = _build_test_cache_key(cmd, cache_context, extra_env) if cache_context.enabled else None

    return run_cached_test(
        cmd,
        cache_key=cache_key or uuid.uuid4().hex,
        meta_dir=cache_context.meta_dir,
        compiler_version=cache_context.compiler_version,
        extra_env=extra_env,
        include_paths=include_paths,
        skip_cache_write=not cache_context.enabled,
        runtime_args=cache_context.runtime_args,
    )


def _precompile_cache_key(
    cmd: Command,
    extra_env: dict[str, str] | None,
    compiler_version: str,
) -> str | None:
    """Return the cache key for a COMPILE_PACKAGE command, or ``None`` if uncacheable.

    Relies on the planner layout ``<mojo> precompile <lib dir> -o <output>
    <flags...>``. The key covers the lib's importable files, a stat stamp
    of each ``-I`` dir in argv order (relative ones resolved against
    ``cmd.cwd``, as the compiler resolves them), the whole argv, the
    compiler binary and version, and the environment as :func:`_merge_env`
    builds it. Another layout, or a lib that is not a
    readable directory, is uncacheable: it always precompiles.
    """
    argv = cmd.argv
    if len(argv) < 5 or argv[3] != "-o":
        return None
    cwd = Path(cmd.cwd)
    lib_dir = cwd / argv[2]
    if not lib_dir.is_dir():
        return None
    flags = argv[5:]
    include_dirs = [str(cwd / value) for flag, value in pairwise(flags) if flag == "-I"]
    try:
        lib_hash = hash_directory_tree(lib_dir)
    except OSError:
        return None
    return compute_precompile_key(
        lib_hash=lib_hash,
        deps_stamp=stamp_include_dirs(include_dirs),
        compiler_version=compiler_version,
        mojo_path=argv[0],
        args=argv[1:],
        env=_merge_env(cmd.env, extra_env),
    )


def _resolve_cache_for_precompile(
    cmd: Command,
    cache_context: CacheContext,
    extra_env: dict[str, str] | None,
    include_paths: tuple[str, ...],
) -> Outcome:
    """Route a COMPILE_PACKAGE command through :func:`run_cached_precompile`."""
    cache_key = _precompile_cache_key(cmd, extra_env, cache_context.compiler_version) if cache_context.enabled else None
    return run_cached_precompile(
        cmd,
        cache_key=cache_key,
        meta_dir=cache_context.meta_dir,
        compiler_version=cache_context.compiler_version,
        extra_env=extra_env,
        include_paths=include_paths,
    )


def _execute(
    cmd: Command,
    extra_env: dict[str, str] | None,
    include_paths: tuple[str, ...],
    cache_context: CacheContext | None,
) -> Outcome:
    """Run *cmd*, through its cache when *cache_context* is given and its kind has one."""
    if cache_context is not None:
        if cmd.kind == CommandKind.BUILD_TEST:
            return _resolve_cache_for_build_test(cmd, cache_context, extra_env, include_paths)
        if cmd.kind == CommandKind.COMPILE_PACKAGE:
            return _resolve_cache_for_precompile(cmd, cache_context, extra_env, include_paths)
    return run_command(cmd, extra_env=extra_env, include_paths=include_paths)


_ETXTBSY_ATTEMPTS = 5


def _spawn(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` that retries a transient ``ETXTBSY`` from execve.

    Binaries are written and executed from worker threads of one process.
    A child forked by another thread inherits any write fd open at that
    moment until its own exec, and while it holds one, execve of that
    file fails with ETXTBSY. The window is brief, so retry with a short
    growing backoff, as Go's os/exec does; any other errno propagates.
    """
    for attempt in range(1, _ETXTBSY_ATTEMPTS):
        try:
            return subprocess.run(argv, **kwargs)
        except OSError as e:
            if e.errno != errno.ETXTBSY:
                raise
        time.sleep(0.01 * attempt)
    return subprocess.run(argv, **kwargs)


def run_command(
    cmd: Command,
    *,
    extra_env: dict[str, str] | None = None,
    include_paths: tuple[str, ...] = (),
) -> Outcome:
    """Run a single Command and return its Outcome.

    The environment is ``cmd.env`` merged with ``extra_env``
    (LocalSettings.env) by :func:`_merge_env`. The host environment is never
    inherited. Parent directories of ``cmd.outputs`` are created first,
    resolving relative outputs against ``cmd.cwd`` as the child will.
    A transient ETXTBSY from execve is retried (see :func:`_spawn`).

    Args:
        cmd: The command to execute.
        extra_env: Additional environment variables (LocalSettings.env).
            cmd.env wins on conflicts, except that a settings locale
            replaces the planner's (see :func:`_merge_env`).
        include_paths: Dependency include directories whose ``lib/``
            subdirectories are added to the dynamic linker search path.

    Returns:
        An Outcome describing the result.
    """
    for output in cmd.outputs:
        (Path(cmd.cwd) / output).parent.mkdir(parents=True, exist_ok=True)

    env = _merge_env(cmd.env, extra_env)

    if include_paths:
        _inject_native_lib_paths(env, include_paths)

    start = time.monotonic()
    try:
        result = _spawn(
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
        retried = f" (after {_ETXTBSY_ATTEMPTS} attempts)" if e.errno == errno.ETXTBSY else ""
        return Outcome(
            command=cmd,
            kind=OutcomeKind.COMPILE_ERROR,
            exit_code=None,
            stdout="",
            stderr=f"OS error running {cmd.argv[0]}{retried}: {e}",
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
    """Call *on_start* from the worker thread, then :func:`_execute` *cmd*."""
    if on_start is not None:
        on_start(cmd)
    return _execute(cmd, extra_env, include_paths, cache_context)


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
    through the cached test runner for compound build-then-execute, and
    ``COMPILE_PACKAGE`` commands through :func:`run_cached_precompile`.
    A precompile cache hit is a PASS, so its dependents run as usual.

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
        cache_context: Optional cache context. When ``None``, every
            command runs via the plain :func:`run_command` path, so
            ``BUILD_TEST`` builds without executing.

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
        cache_context: Optional cache context (see :func:`run_commands`).
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
    """Return a SKIPPED outcome if a dependency of *cmd* failed, else run it."""
    skip = _check_dependencies(cmd, completed)
    if skip is not None:
        return skip
    return _run_with_start(cmd, extra_env, include_paths, on_start, cache_context)


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


def _with_output(cmd: Command, output: Path) -> Command:
    """Copy *cmd* so it writes *output*: its ``-o`` value is replaced, or ``-o`` appended."""
    argv = list(cmd.argv)
    try:
        argv[argv.index("-o") + 1] = str(output)
    except (ValueError, IndexError):
        argv.extend(["-o", str(output)])
    return replace(cmd, argv=tuple(argv), outputs=(str(output),))


def _is_nonempty_file(path: Path) -> bool:
    """Whether *path* is a regular file with content (an empty one was never built)."""
    return path.is_file() and path.stat().st_size > 0


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
        build_outcome = run_command(_with_output(cmd, tmp_binary), extra_env=extra_env, include_paths=include_paths)

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
        if not _is_nonempty_file(tmp_binary):
            return Outcome(
                command=cmd,
                kind=OutcomeKind.COMPILE_ERROR,
                exit_code=0,
                stdout=build_outcome.stdout,
                stderr=(
                    f"Build succeeded but binary not found: {tmp_binary}\n"
                    + build_outcome.stderr
                ),
                diagnostics=build_outcome.diagnostics,
                elapsed_s=build_outcome.elapsed_s,
            )

        # mkstemp creates the temp as 0600; don't rely on the compiler
        # resetting the mode of the file it writes into.
        tmp_binary.chmod(tmp_binary.stat().st_mode | 0o700)

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


def run_cached_precompile(
    cmd: Command,
    *,
    cache_key: str | None,
    meta_dir: Path,
    compiler_version: str,
    extra_env: dict[str, str] | None = None,
    include_paths: tuple[str, ...] = (),
) -> Outcome:
    """Precompile a lib package unless the published one matches *cache_key*.

    The package is ``cmd.outputs[0]``, one ``.mojoc`` file. Its key lives
    in ``<meta_dir>/precompile-<file name>.json``; output names are unique
    per lib, so libs never share an entry. A hit needs the stored key to
    match and the package to be a non-empty file. It returns a PASS
    without running the compiler, so dependents run as usual.

    On a miss the package is built into a private ``mkdtemp`` dir next to
    it, under its final file name (``mojo precompile`` may care about the
    extension). Then the old meta is deleted, the package renamed into
    place, and only then the new meta written. A failed or empty build
    publishes nothing: the previous package and its key stay consistent.

    A ``None`` key (``--no-cache``, or an uncacheable command) skips the
    lookup and writes no meta. The package is still published, because
    dependents import it from its fixed path, so the old meta is dropped.
    """
    output = Path(cmd.cwd) / cmd.outputs[0]
    meta_path = meta_dir / f"precompile-{output.name}.json"

    if cache_key is not None and read_cache_meta(meta_path) == cache_key and _is_nonempty_file(output):
        return Outcome(
            command=cmd,
            kind=OutcomeKind.PASS,
            exit_code=0,
            stdout="",
            stderr="",
            diagnostics=(),
            elapsed_s=0.0,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=output.parent, prefix=f".{output.name}."))
    tmp_output = tmp_dir / output.name
    try:
        outcome = run_command(_with_output(cmd, tmp_output), extra_env=extra_env, include_paths=include_paths)
        if outcome.kind != OutcomeKind.PASS:
            return replace(outcome, command=cmd)
        if not _is_nonempty_file(tmp_output):
            return replace(
                outcome,
                command=cmd,
                kind=OutcomeKind.COMPILE_ERROR,
                stderr=f"Precompile succeeded but package not found: {output.name}\n" + outcome.stderr,
            )
        # Same ordering as run_cached_test: an interrupt after the rename
        # reads as a miss, never as the old key vouching for the new file.
        meta_path.unlink(missing_ok=True)
        os.replace(tmp_output, output)
        if cache_key is not None:
            write_cache_meta(meta_path, cache_key=cache_key, compiler_version=compiler_version)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return replace(outcome, command=cmd)
