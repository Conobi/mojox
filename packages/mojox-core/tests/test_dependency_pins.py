"""Guard the mojox-core version pin in sibling packages."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

PACKAGES = Path(__file__).resolve().parents[2]
PIN = "mojox-core>=0.6,<0.7"


@pytest.mark.parametrize("package", ["mojox", "mojox-build"])
def test_mojox_core_pinned_to_matching_minor(package: str) -> None:
    """Consumers must never resolve a mojox-core whose plan() output the package cannot execute."""
    data = tomllib.loads((PACKAGES / package / "pyproject.toml").read_text())
    deps = [d.replace(" ", "") for d in data["project"]["dependencies"] if d.startswith("mojox-core")]
    assert deps == [PIN]
