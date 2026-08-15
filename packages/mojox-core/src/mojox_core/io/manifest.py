"""Read pyproject.toml from disk — the effectful half of manifest parsing."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .._errors import ConfigError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]


def read(path: Path) -> dict[str, Any]:
    """Read and parse a pyproject.toml file.

    Returns the parsed dict. Raises ConfigError if the file is missing or
    contains invalid TOML.
    """
    if not path.is_file():
        raise ConfigError("pyproject.toml", f"{path} not found")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        raise ConfigError("pyproject.toml", f"failed to parse: {e}") from e
