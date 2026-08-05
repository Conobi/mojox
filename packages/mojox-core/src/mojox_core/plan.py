"""Pure build planner: data in, Command tuples out.

This module performs no I/O, imports no subprocess, reads no environment
variable, and accesses no clock. Every question of the form "does mojox
pass the right flag in situation X" is a pure unit test over data structures.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from ._types import (
    Command,
    CommandKind,
    HostFacts,
    LintConfig,
    Policy,
    ResolvedEnv,
    Target,
    TargetGraph,
    TargetKind,
    Toolchain,
)

_PRECOMPILE_THRESHOLD = 2


def _append_lint_flags(argv: list[str], lints: LintConfig) -> None:
    """Append compiler lint flags to an argv list.

    Only flags whose corresponding LintConfig field is True are emitted.
    These flags are stripped from lib targets (same scope as -O, -D).
    """
    if lints.warnings_as_errors:
        argv.append("--Werror")
    if lints.missing_doc_strings:
        argv.append("--diagnose-missing-doc-strings")
    if lints.unstable_apis:
        argv.append("--warn-on-unstable-apis")


def plan(
    graph: TargetGraph,
    env: ResolvedEnv,
    policy: Policy,
    toolchain: Toolchain,
    host: HostFacts,
) -> tuple[Command, ...]:
    """Produce the sequence of mojo commands to execute.

    Returns an ordered tuple of Command objects. Precompile commands appear
    before any dependent targets (tests, examples, binaries). Thread counts
    are divided by applied concurrency. Flags that ``mojo precompile`` rejects
    (``-O``, ``-D``, ``--num-threads``) are stripped from lib targets.
    """
    commands: list[Command] = []
    precompile_output_dir = PurePosixPath(str(host.manifest_dir)) / ".mojox" / "build" / "pkg"

    lib_targets = [t for t in graph.targets if t.kind == TargetKind.LIB]
    dependent_targets = [
        t for t in graph.targets
        if t.kind in (TargetKind.TEST, TargetKind.EXAMPLE, TargetKind.BIN)
    ]

    should_precompile = len(lib_targets) > 0 and len(dependent_targets) >= _PRECOMPILE_THRESHOLD

    # Precompile lib targets
    precompiled_ids: list[str] = []
    if should_precompile:
        for lib in lib_targets:
            cmd = _build_precompile_command(lib, toolchain, env, host, precompile_output_dir)
            commands.append(cmd)
            precompiled_ids.append(lib.target_id)

    # Build include paths for dependent targets
    include_sequence = _build_include_sequence(
        env, policy, precompile_output_dir if should_precompile else None,
    )

    # Test targets
    for target in graph.targets:
        if target.kind == TargetKind.TEST:
            cmd = _build_run_test_command(
                target, toolchain, policy, include_sequence, host, precompiled_ids,
            )
            commands.append(cmd)
        elif target.kind == TargetKind.EXAMPLE:
            cmd = _build_check_example_command(
                target, toolchain, policy, include_sequence, host, precompiled_ids,
            )
            commands.append(cmd)
        elif target.kind == TargetKind.BIN:
            cmd = _build_compile_binary_command(
                target, toolchain, policy, include_sequence, host, precompiled_ids,
            )
            commands.append(cmd)

    return tuple(commands)


def _build_precompile_command(
    target: Target,
    toolchain: Toolchain,
    env: ResolvedEnv,
    host: HostFacts,
    output_dir: PurePosixPath,
) -> Command:
    """Build a precompile command.

    Strips ``-O``, ``-D``, and ``--num-threads`` because
    ``mojo precompile`` rejects them.
    """
    output = output_dir / f"{PurePosixPath(target.path).name}{toolchain.extension}"
    argv: list[str] = [
        toolchain.mojo_path,
        toolchain.subcommand,
        target.path,
        "-o",
        str(output),
    ]

    for entry in env.include_sequence:
        argv.extend(["-I", entry.include_dir])

    return Command(
        argv=tuple(argv),
        cwd=host.manifest_dir,
        env=_construct_env(toolchain),
        kind=CommandKind.COMPILE_PACKAGE,
        target_id=target.target_id,
        timeout_s=None,
        outputs=(str(output),),
        depends_on=(),
    )


def _build_run_test_command(
    target: Target,
    toolchain: Toolchain,
    policy: Policy,
    include_sequence: tuple[str, ...],
    host: HostFacts,
    precompiled_ids: list[str],
) -> Command:
    """Build a run-test command with optimization, defines, and thread count."""
    argv: list[str] = [toolchain.mojo_path, "run", target.path]

    if policy.optimize is not None:
        argv.append(f"-O{policy.optimize}")

    if policy.debug_level and policy.debug_level != "none":
        argv.extend(["--debug-level", policy.debug_level])

    for path in include_sequence:
        argv.extend(["-I", path])

    for key, value in sorted(policy.defines.items()):
        argv.extend(["-D", f"{key}={value}"])

    num_threads = max(1, host.cpu_count // max(1, policy.jobs_tests))
    argv.extend(["--num-threads", str(num_threads)])

    _append_lint_flags(argv, policy.lints)
    argv.extend(policy.flags)

    return Command(
        argv=tuple(argv),
        cwd=host.manifest_dir,
        env=_construct_env(toolchain),
        kind=CommandKind.RUN_TEST,
        target_id=target.target_id,
        timeout_s=policy.timeout_s,
        outputs=(),
        depends_on=tuple(precompiled_ids),
    )


def _build_check_example_command(
    target: Target,
    toolchain: Toolchain,
    policy: Policy,
    include_sequence: tuple[str, ...],
    host: HostFacts,
    precompiled_ids: list[str],
) -> Command:
    """Build a check-example command with optimization, defines, and thread count."""
    argv: list[str] = [toolchain.mojo_path, "build", target.path]

    if policy.optimize is not None:
        argv.append(f"-O{policy.optimize}")

    if policy.debug_level and policy.debug_level != "none":
        argv.extend(["--debug-level", policy.debug_level])

    for path in include_sequence:
        argv.extend(["-I", path])

    for key, value in sorted(policy.defines.items()):
        argv.extend(["-D", f"{key}={value}"])

    num_threads = max(1, host.cpu_count // max(1, policy.jobs_compile))
    argv.extend(["--num-threads", str(num_threads)])

    _append_lint_flags(argv, policy.lints)
    argv.extend(policy.flags)

    return Command(
        argv=tuple(argv),
        cwd=host.manifest_dir,
        env=_construct_env(toolchain),
        kind=CommandKind.CHECK_EXAMPLE,
        target_id=target.target_id,
        timeout_s=policy.timeout_s,
        outputs=(),
        depends_on=tuple(precompiled_ids),
    )


def _build_compile_binary_command(
    target: Target,
    toolchain: Toolchain,
    policy: Policy,
    include_sequence: tuple[str, ...],
    host: HostFacts,
    precompiled_ids: list[str],
) -> Command:
    """Build a compile-binary command."""
    output_name = target.target_id.removeprefix("bin::")
    argv: list[str] = [toolchain.mojo_path, "build", target.path, "-o", output_name]

    if policy.optimize is not None:
        argv.append(f"-O{policy.optimize}")

    if policy.debug_level and policy.debug_level != "none":
        argv.extend(["--debug-level", policy.debug_level])

    for path in include_sequence:
        argv.extend(["-I", path])

    for key, value in sorted(policy.defines.items()):
        argv.extend(["-D", f"{key}={value}"])

    num_threads = max(1, host.cpu_count // max(1, policy.jobs_compile))
    argv.extend(["--num-threads", str(num_threads)])

    _append_lint_flags(argv, policy.lints)
    argv.extend(policy.flags)

    return Command(
        argv=tuple(argv),
        cwd=host.manifest_dir,
        env=_construct_env(toolchain),
        kind=CommandKind.COMPILE_BINARY,
        target_id=target.target_id,
        timeout_s=None,
        outputs=(output_name,),
        depends_on=tuple(precompiled_ids),
    )


def _build_include_sequence(
    env: ResolvedEnv,
    policy: Policy,
    precompile_output_dir: PurePosixPath | None,
) -> tuple[str, ...]:
    """Build the deduplicated include path sequence.

    Order: precompile output (if any) -> policy include paths (higher layer
    first) -> environment include sequence.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(path: str) -> None:
        if path not in seen:
            seen.add(path)
            result.append(path)

    if precompile_output_dir is not None:
        _add(str(precompile_output_dir))

    for p in policy.include_paths:
        _add(p)

    for entry in env.include_sequence:
        _add(entry.include_dir)

    return tuple(result)


def _construct_env(toolchain: Toolchain) -> dict[str, str]:
    """Construct a minimal, safe environment for mojo invocations.

    The environment is explicitly constructed rather than inherited from the
    host process, ensuring reproducibility and preventing accidental leakage
    of ambient environment variables.
    """
    return {
        "PATH": str(PurePosixPath(toolchain.mojo_path).parent),
        "HOME": "",
    }
