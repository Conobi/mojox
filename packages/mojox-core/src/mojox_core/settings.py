"""Parse local settings from TOML dicts and environment variables.

Pure — receives pre-read TOML dicts and env as arguments. The reader
that locates and reads the files lives in mojox (not mojox-core), which
is what makes "local settings never influence a published wheel" structural.
"""

from __future__ import annotations

from ._errors import ConfigError
from ._types import LocalSettings

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
    user: dict | None,
    project: dict | None,
    env: dict[str, str],
) -> LocalSettings:
    """Parse local settings from user and project TOML dicts plus env vars.

    Precedence: project file wins over user file; MOJOX_* env vars win over both.
    """
    merged: dict = {}

    if user is not None:
        _validate_no_forbidden(user)
        merged.update(user)

    if project is not None:
        _validate_no_forbidden(project)
        merged.update(project)

    # Environment variable overrides
    if "MOJOX_JOBS" in env:
        try:
            merged["jobs"] = int(env["MOJOX_JOBS"])
        except ValueError:
            raise ConfigError(
                "MOJOX_JOBS",
                f"expected an integer, got {env['MOJOX_JOBS']!r}",
            )

    if "MOJOX_TIMEOUT" in env:
        try:
            merged["timeout"] = int(env["MOJOX_TIMEOUT"])
        except ValueError:
            raise ConfigError(
                "MOJOX_TIMEOUT",
                f"expected an integer, got {env['MOJOX_TIMEOUT']!r}",
            )

    if not merged:
        return LocalSettings.EMPTY

    return LocalSettings(
        jobs=merged.get("jobs"),
        timeout_s=merged.get("timeout"),
        env={k: v for k, v in merged.get("env", {}).items()},
    )


def _validate_no_forbidden(settings: dict) -> None:
    """Reject settings keys that belong in the manifest, not local config."""
    for key in settings:
        if key in _FORBIDDEN_KEYS:
            raise ConfigError(
                f"settings.{key}",
                f"'{key}' is a manifest key and is forbidden in local settings. "
                "It changes program semantics and must stay reproducible.",
            )
