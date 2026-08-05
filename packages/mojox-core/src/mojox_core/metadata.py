"""Serialize the build plan and environment into a JSON-serialisable dict.

Pure -- performs no probing. Reader-layer facts arrive as fields of ResolvedEnv.
Deterministic, fixed key order, byte-identical for equal inputs.
"""

from __future__ import annotations

from ._types import (
    Command,
    Diagnostic,
    Policy,
    ResolvedEnv,
    TargetGraph,
    TargetKind,
    Toolchain,
)

METADATA_VERSION = 1


def serialize(
    graph: TargetGraph,
    env: ResolvedEnv,
    policy: Policy,
    commands: tuple[Command, ...],
    toolchain: Toolchain,
    diagnostics: tuple[Diagnostic, ...],
) -> dict:
    """Serialize the complete build plan into a JSON-serialisable dict.

    Args:
        graph: The discovered target graph.
        env: The resolved build environment (include sequence, mojo binary).
        policy: The fully resolved flag set.
        commands: The mojo invocations the planner produced.
        toolchain: The resolved Mojo toolchain identity.
        diagnostics: Any compiler or mojox diagnostics to include.

    Returns:
        A plain dict that is directly JSON-serialisable via ``json.dumps``.
        Key order is fixed and deterministic for equal inputs.
    """
    has_source_generating_hook = False

    return {
        "metadata_version": METADATA_VERSION,
        "toolchain": {
            "mojo_path": toolchain.mojo_path,
            "version": toolchain.version,
            "subcommand": toolchain.subcommand,
            "extension": toolchain.extension,
        },
        "environment": {
            "include_sequence": [
                {
                    "name": e.name,
                    "include_dir": e.include_dir,
                    "kind": e.kind.value,
                    "packages": list(e.packages),
                    "provenance": e.provenance,
                }
                for e in env.include_sequence
            ],
            "mojo_path": env.mojo_path,
            "mojo_version": env.mojo_version,
            "path_mojo": env.path_mojo,
            "lock_version": env.lock_version,
        },
        "policy": {
            "optimize": policy.optimize,
            "debug_level": policy.debug_level,
            "defines": dict(sorted(policy.defines.items())),
            "flags": list(policy.flags),
            "jobs": policy.jobs,
            "jobs_compile": policy.jobs_compile,
            "jobs_tests": policy.jobs_tests,
            "timeout_s": policy.timeout_s,
        },
        "targets": [
            {
                "kind": t.kind.value,
                "path": t.path,
                "target_id": t.target_id,
            }
            for t in graph.targets
        ],
        "commands": [
            {
                "argv": list(c.argv),
                "cwd": str(c.cwd),
                "env": dict(c.env),
                "kind": c.kind.value,
                "target_id": c.target_id,
                "timeout_s": c.timeout_s,
                "outputs": list(c.outputs),
                "depends_on": list(c.depends_on),
            }
            for c in commands
        ],
        "diagnostics": [
            {
                "kind": d.kind,
                "message": d.message,
                "file": d.file,
                "line": d.line,
                "column": d.column,
            }
            for d in diagnostics
        ],
        "plan_complete": not has_source_generating_hook,
        "unstable": [
            "/commands/*/env",
            "/environment/include_sequence/*/native_lib_dirs",
        ],
    }
