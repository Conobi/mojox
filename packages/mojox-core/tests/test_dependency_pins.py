"""Guard the dependency pins that sibling packages must keep."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

PACKAGES = Path(__file__).resolve().parents[2]
PIN = "mojox-core>=0.7,<0.8"


@pytest.mark.parametrize("package", ["mojox", "mojox-build"])
def test_mojox_core_pinned_to_matching_minor(package: str) -> None:
    """Consumers must never resolve a mojox-core whose plan() output the package cannot execute."""
    data = tomllib.loads((PACKAGES / package / "pyproject.toml").read_text())
    deps = [d.replace(" ", "") for d in data["project"]["dependencies"] if d.startswith("mojox-core")]
    assert deps == [PIN]


def test_mojox_requires_compiler_with_precompile() -> None:
    """mojox only drives `mojo precompile`, which 0.x compilers lack; the floor must not silently vanish."""
    data = tomllib.loads((PACKAGES / "mojox" / "pyproject.toml").read_text())
    deps = [d.replace(" ", "") for d in data["project"]["dependencies"] if d.startswith("mojo-compiler")]
    assert deps == ["mojo-compiler>=1.0"]


def test_mojox_core_version_satisfies_the_pin_its_siblings_declare() -> None:
    """mojox and mojox-build pin mojox-core to 0.7.x; a release of mojox-core must land inside it."""
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    data = tomllib.loads((PACKAGES / "mojox-core" / "pyproject.toml").read_text())
    assert Version(data["project"]["version"]) in SpecifierSet(PIN.removeprefix("mojox-core"))


def test_workspace_lock_resolves_a_mojo_1_compiler() -> None:
    """The workspace lock must never fall back to a 0.x compiler, which has no `mojo precompile`."""
    lock = tomllib.loads((PACKAGES.parent / "uv.lock").read_text())
    versions = [p["version"] for p in lock["package"] if p["name"] == "mojo-compiler"]
    assert versions, "uv.lock no longer locks mojo-compiler"
    assert all(not v.startswith("0.") for v in versions), versions
