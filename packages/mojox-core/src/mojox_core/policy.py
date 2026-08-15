"""Resolve profiles, lints, and CLI overrides into the flag set for the planner.

Precedence (lowest to highest):
  built-in defaults -> manifest top-level -> manifest named profile ->
  local settings -> MOJOX_* environment -> CLI flags.

Merge semantics per key:
  - optimize, jobs, timeout: REPLACE (scalars)
  - defines: MAP, REPLACE BY KEY
  - flags: APPEND, higher layer last
  - include_paths: APPEND, higher layer FIRST (first-wins resolution)
"""

from __future__ import annotations

from typing import Any

from ._errors import ConfigError
from ._types import LocalSettings, Manifest, Policy, Profile

BUILTIN_DEV = Profile(
    optimize=0,
    debug_level="line-tables",
    defines={"ASSERT": "all"},
    flags=(),
)

BUILTIN_RELEASE = Profile(
    optimize=3,
    debug_level="none",
    defines={"ASSERT": "safe"},
    flags=(),
)

_BUILTINS: dict[str, Profile] = {
    "dev": BUILTIN_DEV,
    "release": BUILTIN_RELEASE,
}

_DEFAULT_JOBS = 1
_DEFAULT_TIMEOUT = 300


def resolve(
    manifest: Manifest,
    profile_name: str,
    cli_overrides: dict[str, Any],
    settings: LocalSettings,
) -> Policy:
    """Resolve the full flag set from all precedence layers.

    Args:
        manifest: The parsed project manifest.
        profile_name: Which profile to activate (e.g. "dev", "release").
        cli_overrides: Overrides from CLI flags (highest precedence).
        settings: Machine-local settings from .mojox/config.toml + env.

    Returns:
        A fully resolved Policy ready for the planner.

    Raises:
        ConfigError: If *profile_name* is not a built-in or manifest profile.
    """
    builtin = _BUILTINS.get(profile_name)
    manifest_profile = manifest.profiles.get(profile_name)

    if builtin is None and manifest_profile is None:
        available = sorted(set(list(_BUILTINS.keys()) + list(manifest.profiles.keys())))
        raise ConfigError(
            f"profile.{profile_name}",
            f"unknown profile {profile_name!r}. Available: {', '.join(available)}",
        )

    # Precedence: builtin -> manifest top-level -> manifest profile -> settings -> CLI
    optimize = _resolve_scalar(
        builtin.optimize if builtin else None,
        manifest.optimize,
        manifest_profile.optimize if manifest_profile else None,
        None,  # settings don't carry optimize
        cli_overrides.get("optimize"),
    )
    if not isinstance(optimize, int):
        optimize = 0

    debug_level = _resolve_scalar(
        builtin.debug_level if builtin else None,
        manifest.debug_level,
        manifest_profile.debug_level if manifest_profile else None,
        None,  # settings don't carry debug_level
        cli_overrides.get("debug_level"),
    )
    if not isinstance(debug_level, str):
        debug_level = "none"

    # Defines: map, replace by key.  Builtin -> manifest -> profile -> CLI
    defines: dict[str, str] = {}
    if builtin:
        defines.update(builtin.defines)
    defines.update(manifest.defines)
    if manifest_profile:
        defines.update(manifest_profile.defines)
    if "defines" in cli_overrides:
        defines.update(cli_overrides["defines"])

    # Flags: append, higher layer last
    flags: list[str] = []
    if builtin:
        flags.extend(builtin.flags)
    flags.extend(manifest.flags)
    if manifest_profile:
        flags.extend(manifest_profile.flags)
    if "flags" in cli_overrides:
        flags.extend(cli_overrides["flags"])

    # Jobs — explicit None checks to avoid treating 0 as falsy
    jobs_raw = cli_overrides.get("jobs")
    if jobs_raw is None:
        jobs_raw = settings.jobs
    if jobs_raw is None:
        jobs_raw = _DEFAULT_JOBS
    jobs = int(jobs_raw)
    jobs_compile = jobs
    jobs_tests = 1 if not manifest.test_parallel else jobs

    # Timeout — explicit None checks to avoid treating 0 as falsy
    timeout_raw = cli_overrides.get("timeout")
    if timeout_raw is None:
        timeout_raw = settings.timeout_s
    if timeout_raw is None:
        timeout_raw = _DEFAULT_TIMEOUT
    timeout_s = int(timeout_raw)

    # Lints
    lints = manifest.lints

    return Policy(
        optimize=optimize,
        debug_level=debug_level,
        defines=defines,
        flags=tuple(flags),
        include_paths=(),
        lints=lints,
        jobs=jobs,
        jobs_compile=jobs_compile,
        jobs_tests=jobs_tests,
        timeout_s=timeout_s,
    )


def _resolve_scalar(*layers: object) -> object:
    """Return the last non-None value from the precedence layers.

    Each positional argument is a layer from lowest to highest precedence.
    The last non-None value wins.
    """
    result = None
    for val in layers:
        if val is not None:
            result = val
    return result
