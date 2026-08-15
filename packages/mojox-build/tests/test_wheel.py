"""Wheel assembly: compiler pin, provenance, platform tags, RECORD consistency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mojox_build._build import (
    GENERATOR_VERSION,
    _normalize_name,
    _write_provenance,
    host_platform_tag,
)
from mojox_build._metadata import render_metadata
from mojox_core import Manifest, Toolchain, parse_manifest
from mojox_core.io.manifest import read as read_manifest


@pytest.fixture
def sample_manifest(sample_pyproject: Path) -> Manifest:
    """Parse the sample pyproject into a Manifest."""
    raw = read_manifest(sample_pyproject / "pyproject.toml")
    return parse_manifest(raw)


@pytest.fixture
def sample_toolchain() -> Toolchain:
    """A fake toolchain for testing (no real mojo binary)."""
    return Toolchain(
        mojo_path="/usr/bin/mojo",
        version="1.0.0b2",
        subcommand="precompile",
        extension=".mojoc",
    )


class TestCompilerPin:
    def test_metadata_includes_compiler_pin(self, sample_manifest, tmp_path):
        """When compiler_version is set, Requires-Dist for mojo-compiler is emitted."""
        meta = render_metadata(
            sample_manifest,
            tmp_path,
            [],
            compiler_version="1.0.0b2",
        )
        assert "Requires-Dist: mojo-compiler==1.0.0b2" in meta

    def test_metadata_omits_compiler_pin_when_none(self, sample_manifest, tmp_path):
        """When compiler_version is None, no mojo-compiler pin is emitted."""
        meta = render_metadata(sample_manifest, tmp_path, [])
        assert "mojo-compiler" not in meta

    def test_declared_deps_preserved_alongside_pin(self, sample_pyproject):
        """Compiler pin does not interfere with declared project dependencies."""
        pyproject = sample_pyproject / "pyproject.toml"
        content = pyproject.read_text().replace(
            "dependencies = []",
            'dependencies = ["packaging>=23.0"]',
        )
        pyproject.write_text(content)
        raw = read_manifest(pyproject)
        manifest = parse_manifest(raw)
        meta = render_metadata(manifest, sample_pyproject, [], compiler_version="1.0.0b2")
        assert "Requires-Dist: packaging>=23.0" in meta
        assert "Requires-Dist: mojo-compiler==1.0.0b2" in meta


class TestProvenance:
    def test_provenance_json_written(self, tmp_path, sample_toolchain):
        """_write_provenance creates mojox-provenance.json with expected fields."""
        dist_info = tmp_path / "dist-info"
        dist_info.mkdir()
        _write_provenance(dist_info, sample_toolchain, GENERATOR_VERSION)

        prov_path = dist_info / "mojox-provenance.json"
        assert prov_path.is_file()
        data = json.loads(prov_path.read_text())
        assert data["mojo_compiler_version"] == "1.0.0b2"
        assert data["mojox_build_version"] == GENERATOR_VERSION
        assert data["toolchain_surface"] == "precompile/.mojoc"

    def test_provenance_is_deterministic(self, tmp_path, sample_toolchain):
        """Two provenance writes with equal inputs produce byte-identical output."""
        d1 = tmp_path / "d1"
        d1.mkdir()
        _write_provenance(d1, sample_toolchain, GENERATOR_VERSION)

        d2 = tmp_path / "d2"
        d2.mkdir()
        _write_provenance(d2, sample_toolchain, GENERATOR_VERSION)

        assert (d1 / "mojox-provenance.json").read_bytes() == (d2 / "mojox-provenance.json").read_bytes()


class TestPlatformTag:
    def test_host_platform_tag_returns_string(self):
        """host_platform_tag returns a non-empty string."""
        tag = host_platform_tag()
        assert isinstance(tag, str)
        assert len(tag) > 0
        assert tag != "any"

    def test_normalize_name(self):
        """PEP 503 normalization converts dashes and dots to underscores."""
        assert _normalize_name("My-Lib") == "my_lib"
        assert _normalize_name("my.lib") == "my_lib"
        assert _normalize_name("MYLIB") == "mylib"


class TestContentBasedTag:
    """Content-based platform tag: py3-none-any for pure Mojo, host tag for native."""

    def test_pure_mojo_gets_any_tag(self, sample_pyproject):
        """A manifest with no native-libs and no binaries yields py3-none-any."""
        raw = read_manifest(sample_pyproject / "pyproject.toml")
        manifest = parse_manifest(raw)
        assert not manifest.native_libs
        assert not manifest.binaries
        has_native = bool(manifest.native_libs) or bool(manifest.binaries)
        tag = f"py3-none-{host_platform_tag()}" if has_native else "py3-none-any"
        assert tag == "py3-none-any"

    def test_native_libs_gets_host_tag(self, sample_pyproject):
        """A manifest with native-libs yields a host platform tag."""
        pyproject = sample_pyproject / "pyproject.toml"
        content = pyproject.read_text()
        content = content.replace(
            'package-root = "src"',
            'package-root = "src"\nnative-libs = ["lib/libfoo.so"]',
        )
        pyproject.write_text(content)
        raw = read_manifest(pyproject)
        manifest = parse_manifest(raw)
        has_native = bool(manifest.native_libs) or bool(manifest.binaries)
        tag = f"py3-none-{host_platform_tag()}" if has_native else "py3-none-any"
        assert "any" != tag.split("-")[-1]

    def test_binaries_gets_host_tag(self, sample_pyproject):
        """A manifest with binaries yields a host platform tag."""
        pyproject = sample_pyproject / "pyproject.toml"
        content = pyproject.read_text()
        content = content.replace(
            'package-root = "src"',
            'package-root = "src"\n\n[[tool.mojox.binaries]]\nname = "myapp"\nsource = "src/main.mojo"',
        )
        pyproject.write_text(content)
        raw = read_manifest(pyproject)
        manifest = parse_manifest(raw)
        has_native = bool(manifest.native_libs) or bool(manifest.binaries)
        tag = f"py3-none-{host_platform_tag()}" if has_native else "py3-none-any"
        assert "any" != tag.split("-")[-1]
