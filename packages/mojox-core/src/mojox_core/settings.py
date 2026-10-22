"""Parse local settings files against the option registry.

Pure — receives already-read TOML tables. The reader that locates and reads
the files lives in mojox (not mojox-core), which is what makes "local settings
never influence a published wheel" structural. ``MOJOX_*`` variables are not
read here: :func:`mojox_core.policy.resolve` reads them as their own layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ConfigError
from .options import OPTIONS, Layer, Option, origin_under, parse_table
from .types import LocalSettings, Value


def parse_settings(
    files: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    options: Sequence[Option] = OPTIONS,
) -> LocalSettings:
    """Parse settings files, lowest precedence first (user, then project), into one source each.

    Each file is validated on its own, so a bad user value is not masked by a
    good project one. A key a settings file cannot set, an unknown key and an
    invalid value are errors naming ``<file path>:<key>``.
    """
    sources: list[tuple[str, Mapping[str, Value]]] = []
    for path, table in files:
        unknown: list[ConfigError] = []
        values = parse_table(table, Layer.SETTINGS, origin_under(path, ":"), unknown=unknown, options=options)
        if unknown:
            raise unknown[0]
        sources.append((path, values))
    return LocalSettings(sources=tuple(sources)) if sources else LocalSettings.EMPTY
