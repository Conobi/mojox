"""Resolve the layered configuration into the flag set the planner consumes.

Precedence (lowest to highest): built-in profile values -> [tool.mojox] ->
the named profile -> local settings -> MOJOX_* environment -> CLI flags. How
each option merges is declared once in :mod:`mojox_core.options`; this module
turns the layers into fold sources and projects the fold into :class:`Policy`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ConfigError
from .options import (
    BUILTIN_PROFILES,
    NUM_CPUS,
    OPTIONS,
    Option,
    Resolution,
    cli_source,
    env_source,
    parse_count,
    parse_count_text,
    parse_jobs,
    resolve_values,
)
from .types import LocalSettings, Manifest, Policy, Profile, Value

__all__ = [
    "check_profile_name",
    "collect_sources",
    "parse_count",
    "parse_count_text",
    "parse_jobs",
    "resolve",
    "resolve_jobs",
    "resolve_without_manifest",
]


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
    if name in BUILTIN_PROFILES or name in declared:
        return
    available = sorted({*BUILTIN_PROFILES, *declared})
    raise ConfigError(key_path, f"unknown profile {name!r}. Available: {', '.join(available)}")


def resolve(
    manifest: Manifest,
    profile_name: str,
    cli_overrides: Mapping[str, Any],
    settings: LocalSettings,
    env: Mapping[str, str],
    *,
    cpu_count: int | None = None,
    options: Sequence[Option] = OPTIONS,
) -> Policy:
    """Resolve the full flag set from all precedence layers.

    Args:
        cli_overrides: CLI values keyed by option key and shaped like TOML
            values (a tuple for ``flags``, a dict for ``defines``); see
            :func:`mojox_core.options.cli_source`.
        env: The process environment; only the ``MOJOX_*`` names of options
            that allow ENV are read. Pass ``{}`` where the environment must not
            count (the build backend).
        cpu_count: Host CPU count; only needed when the jobs spec is
            ``"num-cpus"`` or negative.

    Raises:
        ConfigError: Unknown *profile_name*, or an invalid ``MOJOX_*`` or CLI value.
        ValueError: The jobs spec needs *cpu_count* and none was given, or a
            *cli_overrides* key is not a command-line option.
    """
    check_profile_name(profile_name, manifest.profiles, f"profile.{profile_name}")
    sources = collect_sources(manifest, profile_name, cli_overrides, settings, env, options=options)
    resolution = resolve_values(sources, profile_name, options=options)
    return _project(resolution, cpu_count, tests_follow_jobs=resolution.values["test-parallel"])


def resolve_without_manifest(
    cli_overrides: Mapping[str, Any],
    settings: LocalSettings,
    env: Mapping[str, str],
    *,
    cpu_count: int | None = None,
    options: Sequence[Option] = OPTIONS,
) -> Policy:
    """Resolve a policy for a lone file outside any project (``mojox run x.mojo``).

    The built-in dev profile stands in for the manifest; the single target
    may use all *jobs* workers.

    Raises:
        ConfigError, ValueError: As for :func:`resolve`.
    """
    sources = collect_sources(None, "dev", cli_overrides, settings, env, options=options)
    return _project(resolve_values(sources, "dev", options=options), cpu_count, tests_follow_jobs=True)


def collect_sources(
    manifest: Manifest | None,
    profile_name: str,
    cli_overrides: Mapping[str, Any],
    settings: LocalSettings,
    env: Mapping[str, str],
    *,
    options: Sequence[Option] = OPTIONS,
) -> tuple[Mapping[str, Value], ...]:
    """Order every layer as fold sources: manifest, named profile, settings, ``MOJOX_*``, CLI.

    Raises:
        ConfigError: An invalid ``MOJOX_*`` or CLI value, named by its origin.
        ValueError: A *cli_overrides* key is not a command-line option.
    """
    sources: list[Mapping[str, Value]] = []
    if manifest is not None:
        sources.append(manifest.values)
        profile = manifest.profiles.get(profile_name)
        if profile is not None:
            sources.append(profile.values)
    sources.extend(values for _path, values in settings.sources)
    sources.append(env_source(env, options=options))
    sources.append(cli_source(cli_overrides, options=options))
    return tuple(sources)


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
        env=dict(values["env"]),
        origins=dict(resolution.origins),
    )
