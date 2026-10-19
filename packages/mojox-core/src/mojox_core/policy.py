"""Resolve profiles, lints, and CLI overrides into the flag set for the planner.

Precedence (lowest to highest):
  built-in defaults -> manifest top-level -> manifest named profile ->
  local settings -> MOJOX_* environment -> CLI flags.

Merge semantics per key:
  - optimize, jobs, compile-threads, timeout: REPLACE (scalars)
  - defines: MAP, REPLACE BY KEY
  - flags: APPEND, higher layer last
  - include_paths: APPEND, higher layer FIRST (first-wins resolution)
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from .errors import ConfigError
from .types import LintConfig, LocalSettings, Manifest, Policy, Profile

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

_DEFAULT_JOBS = 1
_DEFAULT_COMPILE_THREADS = 1  # a user profile with no value at any manifest layer
_DEFAULT_TIMEOUT = 300

NUM_CPUS: Literal["num-cpus"] = "num-cpus"
_INT_TEXT = re.compile(r"[+-]?[0-9]+")
_JOBS_FORMS = 'a positive integer, a negative integer (CPUs to leave free), or "num-cpus"'


def _int_from_text(text: str) -> int | None:
    """Parse ASCII decimal digits only; None for anything else (incl. ``1_000``, ``4.0``, huge strings)."""
    stripped = text.strip()
    if not _INT_TEXT.fullmatch(stripped):
        return None
    try:
        return int(stripped)
    except ValueError:  # beyond the interpreter's int-from-str digit limit
        return None


def parse_jobs(value: object, key_path: str) -> int | Literal["num-cpus"]:
    """Validate a jobs spec from TOML, the environment, or the CLI.

    Accepts an int other than 0, its decimal text, or ``"num-cpus"``
    (case-sensitive, surrounding whitespace ignored). Negative values mean
    "all CPUs but k" and are resolved later by :func:`resolve_jobs`.

    Raises:
        ConfigError: At *key_path* for 0, bools, floats and anything else.
    """
    if isinstance(value, bool):  # bool is an int subclass: reject first
        raise ConfigError(key_path, f"expected {_JOBS_FORMS}, got {value!r}")
    if isinstance(value, int):
        jobs: int | None = value
    elif isinstance(value, str):
        if value.strip() == NUM_CPUS:
            return NUM_CPUS
        jobs = _int_from_text(value)
    else:
        jobs = None
    if jobs is None:
        raise ConfigError(key_path, f"expected {_JOBS_FORMS}, got {value!r}")
    if jobs == 0:
        raise ConfigError(key_path, 'jobs must not be 0; use 1 for serial or "num-cpus"')
    return jobs


def parse_count(value: object, key_path: str, *, from_text: bool) -> int:
    """Validate a count that must be an integer >= 1 (compile-threads, timeout).

    With *from_text* (environment, CLI) the value is decimal text; otherwise
    (TOML) it must be a real int, so a quoted ``"2"`` is rejected.

    Raises:
        ConfigError: At *key_path* for anything but an integer >= 1.
    """
    if from_text and isinstance(value, str):
        return parse_count_text(value, key_path)
    if not from_text and isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    raise ConfigError(key_path, f"expected an integer >= 1, got {value!r}")


def parse_count_text(text: str, key_path: str) -> int:
    """Parse decimal text (environment, CLI) naming an integer >= 1; surrounding whitespace is ignored.

    Raises:
        ConfigError: At *key_path* for anything else, including ``""``.
    """
    count = _int_from_text(text)
    if count is None or count < 1:
        raise ConfigError(key_path, f"expected an integer >= 1, got {text!r}")
    return count


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
        cli_overrides: Overrides from CLI flags (highest precedence); ``jobs``
            and ``compile_threads`` are validated here.
        settings: Machine-local settings from .mojox/config.toml + env.
        cpu_count: Host CPU count; only needed when the jobs spec is
            ``"num-cpus"`` or negative.

    Raises:
        ConfigError: Unknown *profile_name*, or an invalid CLI jobs /
            compile-threads value.
        ValueError: The jobs spec needs *cpu_count* and none was given.
    """
    check_profile_name(profile_name, manifest.profiles, f"profile.{profile_name}")
    builtin = _BUILTINS.get(profile_name)
    manifest_profile = manifest.profiles.get(profile_name)

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

    profile_threads = _resolve_scalar(
        builtin.compile_threads if builtin else None,
        manifest.compile_threads,
        manifest_profile.compile_threads if manifest_profile else None,
    )
    if not isinstance(profile_threads, int):
        profile_threads = _DEFAULT_COMPILE_THREADS
    jobs, jobs_spec, compile_threads = _resolve_parallelism(profile_threads, cli_overrides, settings, cpu_count)
    jobs_tests = 1 if not manifest.test_parallel else jobs

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
        jobs_compile=jobs,
        jobs_tests=jobs_tests,
        timeout_s=_resolve_timeout(cli_overrides, settings),
        compile_threads=compile_threads,
        jobs_spec=jobs_spec,
    )


def resolve_without_manifest(
    cli_overrides: dict[str, Any],
    settings: LocalSettings,
    *,
    cpu_count: int | None = None,
) -> Policy:
    """Resolve a policy for a lone file outside any project (``mojox run x.mojo``).

    The built-in dev profile stands in for the manifest. Jobs, compile
    threads and timeout follow the same rules as :func:`resolve`; the single
    target may use all *jobs* workers.

    Raises:
        ConfigError, ValueError: As for :func:`resolve`.
    """
    jobs, jobs_spec, compile_threads = _resolve_parallelism(
        BUILTIN_DEV.compile_threads or _DEFAULT_COMPILE_THREADS, cli_overrides, settings, cpu_count
    )
    optimize = cli_overrides.get("optimize")
    if optimize is None:
        optimize = BUILTIN_DEV.optimize or 0
    return Policy(
        optimize=optimize,
        debug_level=BUILTIN_DEV.debug_level or "line-tables",
        defines={**BUILTIN_DEV.defines, **cli_overrides.get("defines", {})},
        flags=(*BUILTIN_DEV.flags, *cli_overrides.get("flags", ())),
        include_paths=(),
        lints=LintConfig(),
        jobs=jobs,
        jobs_compile=jobs,
        jobs_tests=jobs,
        timeout_s=_resolve_timeout(cli_overrides, settings),
        compile_threads=compile_threads,
        jobs_spec=jobs_spec,
    )


def _resolve_parallelism(
    profile_compile_threads: int,
    cli_overrides: dict[str, Any],
    settings: LocalSettings,
    cpu_count: int | None,
) -> tuple[int, str, int]:
    """Return ``(jobs, jobs_spec display text, compile_threads)``.

    *profile_compile_threads* is the value already merged from the builtin
    and manifest layers; settings and CLI override it. Values are
    re-validated so hand-built settings and CLI dicts get the same checks as
    parsed ones. Explicit None checks: 0 must reach validation.
    """
    cli_jobs = cli_overrides.get("jobs")
    if cli_jobs is not None:
        spec = parse_jobs(cli_jobs, "--jobs")
    elif settings.jobs is not None:
        spec = parse_jobs(settings.jobs, "settings.jobs")
    else:
        spec = _DEFAULT_JOBS
    needs_cpus = spec == NUM_CPUS or (isinstance(spec, int) and spec < 0)
    if needs_cpus and cpu_count is None:
        raise ValueError(f"jobs spec {spec!r} needs cpu_count")
    jobs = resolve_jobs(spec, cpu_count or 1)  # cpu_count is unused for positive specs

    cli_threads = cli_overrides.get("compile_threads")
    if cli_threads is not None:
        threads = parse_count(cli_threads, "--compile-threads", from_text=False)
    elif settings.compile_threads is not None:
        threads = parse_count(settings.compile_threads, "settings.compile-threads", from_text=False)
    else:
        threads = profile_compile_threads
    return jobs, str(spec), threads


def _resolve_timeout(cli_overrides: dict[str, Any], settings: LocalSettings) -> int:
    """CLI -> settings -> default, re-validated: a 0-second limit would kill every command."""
    cli_timeout = cli_overrides.get("timeout")
    if cli_timeout is not None:
        return parse_count(cli_timeout, "--timeout", from_text=False)
    if settings.timeout_s is not None:
        return parse_count(settings.timeout_s, "settings.timeout", from_text=False)
    return _DEFAULT_TIMEOUT


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
