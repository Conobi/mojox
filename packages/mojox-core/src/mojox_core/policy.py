"""Resolve the layered configuration into the flag set the planner consumes.

Precedence (lowest to highest): built-in profile values -> [tool.mojox] ->
the named profile -> local settings -> MOJOX_* environment -> CLI flags. How
each option merges is declared once in :mod:`mojox_core.options`; this module
turns the layers into fold sources and projects the fold into :class:`Policy`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .errors import ConfigError
from .options import NUM_CPUS, OPTIONS, Resolution, by_key, parse_count, parse_count_text, parse_jobs, resolve_values
from .types import LocalSettings, Manifest, Policy, Profile, Value

__all__ = [
    "BUILTIN_DEV",
    "BUILTIN_RELEASE",
    "check_profile_name",
    "parse_count",
    "parse_count_text",
    "parse_jobs",
    "resolve",
    "resolve_jobs",
    "resolve_without_manifest",
]

BUILTIN_DEV = Profile(
    optimize=0,
    debug_level="line-tables",
    defines={"ASSERT": "all"},
    flags=(),
    compile_threads=1,
)

BUILTIN_RELEASE = Profile(
    optimize=3,
    debug_level="none",
    defines={"ASSERT": "safe"},
    flags=(),
    compile_threads=8,
)

_BUILTINS: dict[str, Profile] = {
    "dev": BUILTIN_DEV,
    "release": BUILTIN_RELEASE,
}

# cli_overrides keys (argparse dests) -> option keys. optimize and debug_level
# have no flag; tests still pass them.
_LEGACY_CLI_KEYS = {
    "jobs": "jobs",
    "compile_threads": "compile-threads",
    "timeout": "timeout",
    "defines": "defines",
    "flags": "flags",
    "optimize": "optimize",
    "debug_level": "debug-level",
}


def resolve_jobs(spec: int | str, cpu_count: int) -> int:
    """Turn a parsed jobs spec into a worker count (always >= 1).

    Positive specs are used as-is, not capped at *cpu_count*.
    """
    if spec == NUM_CPUS:
        return max(1, cpu_count)
    if not isinstance(spec, int):
        raise TypeError(f"unparsed jobs spec {spec!r}; call parse_jobs first")
    if spec < 0:
        return max(1, cpu_count + spec)
    return spec


def check_profile_name(name: str, declared: Mapping[str, Profile], key_path: str) -> None:
    """Reject *name* unless it is a built-in or a declared profile.

    Raises:
        ConfigError: At *key_path*, listing every available profile.
    """
    if name in _BUILTINS or name in declared:
        return
    available = sorted({*_BUILTINS, *declared})
    raise ConfigError(key_path, f"unknown profile {name!r}. Available: {', '.join(available)}")


def resolve(
    manifest: Manifest,
    profile_name: str,
    cli_overrides: dict[str, Any],
    settings: LocalSettings,
    *,
    cpu_count: int | None = None,
) -> Policy:
    """Resolve the full flag set from all precedence layers.

    Args:
        cli_overrides: Overrides from CLI flags (highest precedence), re-validated here.
        settings: Machine-local settings from .mojox/config.toml + env.
        cpu_count: Host CPU count; only needed when the jobs spec is
            ``"num-cpus"`` or negative.

    Raises:
        ConfigError: Unknown *profile_name*, or an invalid settings or CLI value.
        ValueError: The jobs spec needs *cpu_count* and none was given.
    """
    check_profile_name(profile_name, manifest.profiles, f"profile.{profile_name}")
    sources = _legacy_sources(manifest, profile_name, cli_overrides, settings)
    return _project(resolve_values(sources, profile_name), cpu_count, tests_follow_jobs=manifest.test_parallel)


def resolve_without_manifest(
    cli_overrides: dict[str, Any],
    settings: LocalSettings,
    *,
    cpu_count: int | None = None,
) -> Policy:
    """Resolve a policy for a lone file outside any project (``mojox run x.mojo``).

    The built-in dev profile stands in for the manifest; the single target
    may use all *jobs* workers.

    Raises:
        ConfigError, ValueError: As for :func:`resolve`.
    """
    sources = _legacy_sources(None, "dev", cli_overrides, settings)
    return _project(resolve_values(sources, "dev"), cpu_count, tests_follow_jobs=True)


def _project(resolution: Resolution, cpu_count: int | None, *, tests_follow_jobs: bool) -> Policy:
    """Turn folded option values into the planner's typed :class:`Policy`.

    Raises:
        ValueError: The jobs spec needs *cpu_count* and none was given.
    """
    values = resolution.values
    spec = values["jobs"]
    needs_cpus = spec == NUM_CPUS or (isinstance(spec, int) and spec < 0)
    if needs_cpus and cpu_count is None:
        raise ValueError(f"jobs spec {spec!r} needs cpu_count")
    jobs = resolve_jobs(spec, cpu_count or 1)  # cpu_count is unused for positive specs
    return Policy(
        optimize=values["optimize"],
        debug_level=values["debug-level"],
        defines=dict(values["defines"]),
        flags=tuple(values["flags"]),
        include_paths=(),
        lints=values["lints"],
        jobs=jobs,
        jobs_compile=jobs,
        jobs_tests=jobs if tests_follow_jobs else 1,
        timeout_s=values["timeout"],
        compile_threads=values["compile-threads"],
        jobs_spec=str(spec),
    )


def _legacy_sources(
    manifest: Manifest | None,
    profile_name: str,
    cli_overrides: Mapping[str, Any],
    settings: LocalSettings,
) -> list[Mapping[str, Value]]:
    """Order the layers as fold sources: manifest, named profile, settings (with MOJOX_*), CLI."""
    sources: list[Mapping[str, Value]] = []
    if manifest is not None:
        sources.append(_typed_layer(manifest, "tool.mojox"))
        profile = manifest.profiles.get(profile_name)
        if profile is not None:
            sources.append(_typed_layer(profile, f"tool.mojox.profile.{profile_name}"))
    sources.append(_legacy_settings(settings))
    sources.append(_legacy_cli(cli_overrides))
    return sources


def _typed_layer(table: Manifest | Profile, prefix: str) -> dict[str, Value]:
    """Wrap a manifest's or profile's typed option fields as values; unset fields are skipped."""
    fields: dict[str, Any] = {
        "optimize": table.optimize,
        "debug-level": table.debug_level,
        "defines": table.defines or None,
        "flags": table.flags or None,
        "compile-threads": table.compile_threads,
    }
    if isinstance(table, Manifest):
        fields["lints"] = table.lints
    return {key: Value(value, f"{prefix}.{key}") for key, value in fields.items() if value is not None}


def _legacy_settings(settings: LocalSettings) -> dict[str, Value]:
    """Settings fields (files and MOJOX_* already merged), re-validated as the old resolver did."""
    raw = {"jobs": settings.jobs, "compile-threads": settings.compile_threads, "timeout": settings.timeout_s}
    return _checked(raw, lambda key: f"settings.{key}")


def _legacy_cli(cli_overrides: Mapping[str, Any]) -> dict[str, Value]:
    """CLI overrides keyed by argparse dest, re-validated and named by their flag."""
    raw = {_LEGACY_CLI_KEYS[name]: value for name, value in cli_overrides.items() if name in _LEGACY_CLI_KEYS}
    return _checked(raw, lambda key: f"--{key}")


def _checked(raw: Mapping[str, Any], origin_of: Callable[[str], str]) -> dict[str, Value]:
    """Parse each set value with its option's TOML parser, so hand-built inputs get the usual checks."""
    table = by_key(OPTIONS)
    return {
        key: Value(table[key].parse(value, origin_of(key)), origin_of(key))
        for key, value in raw.items()
        if value is not None
    }
