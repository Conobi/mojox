"""Guard against the generator version drifting from pyproject.toml."""

from __future__ import annotations

import sys
from pathlib import Path

import mojox_build
from mojox_build import build

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _pyproject_version() -> str:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_generator_version_matches_pyproject() -> None:
    assert build.GENERATOR_VERSION == _pyproject_version()


def test_package_version_matches_pyproject() -> None:
    assert mojox_build.__version__ == _pyproject_version()
