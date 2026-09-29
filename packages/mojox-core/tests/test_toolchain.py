"""Toolchain resolution from mojo-compiler distribution metadata."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest
from mojox_core.errors import ConfigError
from mojox_core.io import toolchain


class _FakeDist:
    """Stand-in for an installed mojo-compiler distribution rooted at *root*."""

    def __init__(self, root: Path, version: str) -> None:
        self._root = root
        self.metadata = {"Version": version}
        self.files = [importlib.metadata.PackagePath("bin/mojo")]
        self.entry_points: list[object] = []

    def locate_file(self, path: object) -> Path:
        return self._root / str(path)


@pytest.fixture
def install_compiler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Install a fake mojo-compiler of the given version with a real bin/mojo file."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "mojo").write_text("")

    def _install(version: str) -> Path:
        dist = _FakeDist(tmp_path, version)
        monkeypatch.setattr(importlib.metadata, "distribution", lambda name: dist)
        return tmp_path / "bin" / "mojo"

    return _install


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.0.0b2", "2.3.0"])
def test_supported_compiler_resolves_to_precompile(install_compiler, version: str) -> None:
    mojo = install_compiler(version)
    t = toolchain.resolve()
    assert (t.mojo_path, t.version, t.subcommand, t.extension) == (
        str(mojo.resolve()),
        version,
        "precompile",
        ".mojoc",
    )


@pytest.mark.parametrize("version", ["0.26.2.0", "0.25.6.0"])
def test_compiler_without_precompile_is_rejected(install_compiler, version: str) -> None:
    """Pre-1.0 compilers have no `mojo precompile`; fail at resolve, not mid-build."""
    install_compiler(version)
    with pytest.raises(ConfigError) as exc:
        toolchain.resolve()
    assert exc.value.key_path == "toolchain"
    assert version in exc.value.message
    assert "mojo-compiler>=1.0" in exc.value.message


def test_missing_compiler_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(name: str) -> None:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", _missing)
    with pytest.raises(ConfigError, match="not installed"):
        toolchain.resolve()
