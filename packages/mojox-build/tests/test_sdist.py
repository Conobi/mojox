"""Sdist assembly: README/license inclusion, symlink safety."""

from __future__ import annotations

from mojox_build.build import _sdist_files
from mojox_core import parse_manifest
from mojox_core.io.manifest import read as read_manifest


class TestSdistInclusion:
    def test_readme_always_included(self, sample_pyproject):
        """README is included even when source-include is restrictive."""
        # Restrict source-include to src/** inside the existing [tool.mojox] table.
        pyproject = sample_pyproject / "pyproject.toml"
        content = pyproject.read_text()
        content = content.replace(
            'package-root = "src"',
            'package-root = "src"\nsource-include = ["src/**"]',
        )
        pyproject.write_text(content)

        raw = read_manifest(pyproject)
        manifest = parse_manifest(raw)
        files = _sdist_files(sample_pyproject, manifest)
        filenames = {p.name for p in files}
        assert "README.md" in filenames

    def test_license_files_always_included(self, sample_pyproject_with_license_files):
        """License files are included regardless of source-include."""
        root = sample_pyproject_with_license_files
        pyproject = root / "pyproject.toml"
        content = pyproject.read_text()
        content = content.replace(
            'package-root = "src"',
            'package-root = "src"\nsource-include = ["src/**"]',
        )
        pyproject.write_text(content)

        raw = read_manifest(pyproject)
        manifest = parse_manifest(raw)
        files = _sdist_files(root, manifest)
        filenames = {p.name for p in files}
        assert "LICENSE" in filenames or any("LICENSE" in n for n in filenames)

    def test_pyproject_always_included(self, sample_pyproject):
        """pyproject.toml is always included."""
        pyproject = sample_pyproject / "pyproject.toml"
        content = pyproject.read_text()
        content = content.replace(
            'package-root = "src"',
            'package-root = "src"\nsource-include = ["src/**"]',
        )
        pyproject.write_text(content)

        raw = read_manifest(pyproject)
        manifest = parse_manifest(raw)
        files = _sdist_files(sample_pyproject, manifest)
        filenames = {p.name for p in files}
        assert "pyproject.toml" in filenames

    def test_symlinks_excluded(self, sample_pyproject):
        """Symlinked files are not included in the sdist."""
        real_file = sample_pyproject / "real.txt"
        real_file.write_text("content")
        link = sample_pyproject / "link.txt"
        link.symlink_to(real_file)

        raw = read_manifest(sample_pyproject / "pyproject.toml")
        manifest = parse_manifest(raw)
        files = _sdist_files(sample_pyproject, manifest)
        filenames = {p.name for p in files}
        assert "real.txt" in filenames
        assert "link.txt" not in filenames


class TestSdistExclusion:
    def test_source_exclude_respected(self, sample_pyproject):
        """source-exclude removes matching files."""
        junk = sample_pyproject / "junk.log"
        junk.write_text("noise")

        pyproject = sample_pyproject / "pyproject.toml"
        content = pyproject.read_text()
        content = content.replace(
            'package-root = "src"',
            'package-root = "src"\nsource-exclude = ["*.log"]',
        )
        pyproject.write_text(content)

        raw = read_manifest(pyproject)
        manifest = parse_manifest(raw)
        files = _sdist_files(sample_pyproject, manifest)
        filenames = {p.name for p in files}
        assert "junk.log" not in filenames
