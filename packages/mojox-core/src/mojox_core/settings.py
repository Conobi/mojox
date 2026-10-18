"""Parse local settings from TOML dicts and environment variables.

Pure — receives pre-read TOML dicts and env as arguments. The reader
that locates and reads the files lives in mojox (not mojox-core), which
is what makes "local settings never influence a published wheel" structural.
"""

from __future__ import annotations

from typing import Any

from .errors import ConfigError
from .policy import parse_count, parse_jobs
from .types import LocalSettings

_FORBIDDEN_KEYS = frozenset(
    {
        "packages",
        "package-root",
        "binaries",
        "native-libs",
        "source-include",
        "source-exclude",
        "wheel-include",
        "wheel-exclude",
        "test-roots",
        "test-parallel",
        "defines",
        "lints",
        "pre-build",
    }
)


def parse_settings(
    user: dict[str, Any] | None,
    project: dict[str, Any] | None,
    env: dict[str, str],
) -> LocalSettings:
    """Parse local settings from user and project TOML dicts plus env vars.

    Precedence: project file wins over user file; MOJOX_* env vars win over both.
    """
    merged: dict[str, Any] = {}

    # Each file is validated on its own so a bad user value is not masked
    # by a good project one.
    for layer in (user, project):
        if layer is not None:
            _validate_no_forbidden(layer)
            merged.update(_validate_values(layer))

    # Environment variable overrides
    if "MOJOX_JOBS" in env:
        merged["jobs"] = parse_jobs(env["MOJOX_JOBS"], "MOJOX_JOBS")

    if "MOJOX_COMPILE_THREADS" in env:
        merged["compile-threads"] = parse_count(env["MOJOX_COMPILE_THREADS"], "MOJOX_COMPILE_THREADS", from_text=True)

    if "MOJOX_TIMEOUT" in env:
        merged["timeout"] = parse_count(env["MOJOX_TIMEOUT"], "MOJOX_TIMEOUT", from_text=True)

    if not merged:
        return LocalSettings.EMPTY

    return LocalSettings(
        jobs=merged.get("jobs"),
        compile_threads=merged.get("compile-threads"),
        timeout_s=merged.get("timeout"),
        env={k: v for k, v in merged.get("env", {}).items()},
    )


def _validate_values(settings: dict[str, Any]) -> dict[str, Any]:
    """Return *settings* with the parallelism and timeout keys parsed and validated."""
    out = dict(settings)
    if "jobs" in out:
        out["jobs"] = parse_jobs(out["jobs"], "settings.jobs")
    if "compile-threads" in out:
        out["compile-threads"] = parse_count(out["compile-threads"], "settings.compile-threads", from_text=False)
    if "timeout" in out:
        out["timeout"] = parse_count(out["timeout"], "settings.timeout", from_text=False)
    return out


def _validate_no_forbidden(settings: dict[str, Any]) -> None:
    """Reject settings keys that belong in the manifest, not local config."""
    for key in settings:
        if key in _FORBIDDEN_KEYS:
            raise ConfigError(
                f"settings.{key}",
                f"'{key}' is a manifest key and is forbidden in local settings. "
                "It changes program semantics and must stay reproducible.",
            )
