"""Shared fixtures for mojox-build tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_pyproject(tmp_path: Path) -> Path:
    """Create a minimal pyproject.toml using [tool.mojox] and return the project root."""
    src = tmp_path / "src" / "mylib"
    src.mkdir(parents=True)
    (src / "main.mojo").write_text("fn main():\n    print('hello')\n")

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "my-lib"\n'
        'version = "1.0.0"\n'
        'description = "A test library"\n'
        'readme = "README.md"\n'
        'license = "MIT"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = []\n"
        "\n"
        "[tool.mojox]\n"
        'package-root = "src"\n'
        "\n"
        "[build-system]\n"
        'requires = ["mojox-build"]\n'
        'build-backend = "mojox_build"\n'
    )
    (tmp_path / "README.md").write_text("# My Lib\n")
    (tmp_path / "LICENSE").write_text("MIT License\n")
    return tmp_path


@pytest.fixture
def sample_pyproject_with_license_files(sample_pyproject: Path) -> Path:
    """Extend sample_pyproject with license-files declaration."""
    pyproject = sample_pyproject / "pyproject.toml"
    content = pyproject.read_text()
    content = content.replace(
        'license = "MIT"',
        'license = "MIT"\nlicense-files = ["LICENSE*"]',
    )
    pyproject.write_text(content)
    (sample_pyproject / "LICENSE-APACHE").write_text("Apache License\n")
    return sample_pyproject
