"""Preflight checks: validate project layout before building."""

from __future__ import annotations

from pathlib import Path

import pytest
from mojox_build._preflight import check
from mojox_core import ConfigError, Manifest, Toolchain, parse_manifest
from mojox_core.io.manifest import read as read_manifest


@pytest.fixture
def manifest(sample_pyproject: Path) -> Manifest:
    """Parse the sample pyproject into a Manifest."""
    raw = read_manifest(sample_pyproject / "pyproject.toml")
    return parse_manifest(raw)


@pytest.fixture
def toolchain() -> Toolchain:
    """A fake toolchain for testing."""
    return Toolchain(
        mojo_path="/usr/bin/mojo",
        version="1.0.0b2",
        subcommand="precompile",
        extension=".mojoc",
    )


class TestPreflightPackageDirs:
    def test_passes_with_valid_package_root(self, sample_pyproject, manifest, toolchain):
        """No error when package-root directory exists with subdirectories."""
        check(sample_pyproject, manifest, toolchain)

    def test_fails_on_missing_package_root(self, sample_pyproject, toolchain):
        """Raises ConfigError when package-root directory doesn't exist."""
        import shutil

        shutil.rmtree(sample_pyproject / "src")

        raw = read_manifest(sample_pyproject / "pyproject.toml")
        manifest = parse_manifest(raw)

        with pytest.raises(ConfigError, match="package-root"):
            check(sample_pyproject, manifest, toolchain)


class TestPreflightBinaries:
    def test_fails_on_missing_binary_source(self, sample_pyproject, toolchain):
        """Raises ConfigError when a declared binary source doesn't exist."""
        pyproject = sample_pyproject / "pyproject.toml"
        content = pyproject.read_text()
        content = content.replace(
            'package-root = "src"',
            'package-root = "src"\n\n[[tool.mojox.binaries]]\nname = "myapp"\nsource = "nonexistent.mojo"',
        )
        pyproject.write_text(content)

        raw = read_manifest(pyproject)
        manifest = parse_manifest(raw)

        with pytest.raises(ConfigError, match="binaries"):
            check(sample_pyproject, manifest, toolchain)


class TestPreflightReadme:
    def test_fails_on_missing_readme(self, sample_pyproject, toolchain):
        """Raises ConfigError when the declared readme doesn't exist."""
        (sample_pyproject / "README.md").unlink()

        raw = read_manifest(sample_pyproject / "pyproject.toml")
        manifest = parse_manifest(raw)

        with pytest.raises(ConfigError, match="readme"):
            check(sample_pyproject, manifest, toolchain)

    def test_passes_with_no_readme_declared(self, sample_pyproject, toolchain):
        """No error when no readme is declared in the manifest."""
        pyproject = sample_pyproject / "pyproject.toml"
        content = pyproject.read_text()
        content = content.replace('readme = "README.md"\n', "")
        pyproject.write_text(content)

        raw = read_manifest(pyproject)
        manifest = parse_manifest(raw)
        check(sample_pyproject, manifest, toolchain)
