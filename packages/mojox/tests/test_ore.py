"""Ore acceleration: probe, cache, pipeline."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from mojox_core import CommandKind
from mojox._ore import OreContext


class TestOreContext:
    """OreContext frozen dataclass construction and immutability."""

    def test_construction_with_all_fields(self, tmp_path: Path):
        """All seven fields are stored correctly."""
        seed = tmp_path / "libfoo.ore"
        ctx = OreContext(
            enabled=True,
            seed=seed,
            include_paths=("/usr/include", "/opt/mojo/include"),
            compiler_version="2025.6.1",
            mojo_path="/usr/bin/mojo",
            runtime_lib_dir=tmp_path / "lib",
            dep_versions=(("pkg_a", "1.0.0"), ("pkg_b", "2.3.1")),
        )
        assert ctx.enabled is True
        assert ctx.seed == seed
        assert ctx.include_paths == ("/usr/include", "/opt/mojo/include")
        assert ctx.compiler_version == "2025.6.1"
        assert ctx.mojo_path == "/usr/bin/mojo"
        assert ctx.runtime_lib_dir == tmp_path / "lib"
        assert ctx.dep_versions == (("pkg_a", "1.0.0"), ("pkg_b", "2.3.1"))

    def test_disabled_context_has_no_seed(self):
        """A disabled OreContext typically has seed=None."""
        ctx = OreContext(
            enabled=False,
            seed=None,
            include_paths=(),
            compiler_version="2025.6.1",
            mojo_path="/usr/bin/mojo",
            runtime_lib_dir=Path("/usr/lib/mojo"),
            dep_versions=(),
        )
        assert ctx.enabled is False
        assert ctx.seed is None

    def test_frozen_immutability(self, tmp_path: Path):
        """OreContext is frozen — attribute assignment raises."""
        ctx = OreContext(
            enabled=True,
            seed=tmp_path / "lib.ore",
            include_paths=(),
            compiler_version="2025.6.1",
            mojo_path="/usr/bin/mojo",
            runtime_lib_dir=tmp_path,
            dep_versions=(),
        )
        with pytest.raises(AttributeError):
            ctx.enabled = False  # type: ignore[misc]
        with pytest.raises(AttributeError):
            ctx.seed = None  # type: ignore[misc]

    def test_empty_include_paths_and_deps(self):
        """Empty tuples are valid for include_paths and dep_versions."""
        ctx = OreContext(
            enabled=True,
            seed=None,
            include_paths=(),
            compiler_version="nightly",
            mojo_path="/usr/bin/mojo",
            runtime_lib_dir=Path("/lib"),
            dep_versions=(),
        )
        assert ctx.include_paths == ()
        assert ctx.dep_versions == ()

    def test_equality_between_identical_contexts(self, tmp_path: Path):
        """Two OreContexts with the same field values are equal."""
        kwargs = dict(
            enabled=True,
            seed=tmp_path / "lib.ore",
            include_paths=("/inc",),
            compiler_version="2025.6.1",
            mojo_path="/usr/bin/mojo",
            runtime_lib_dir=tmp_path,
            dep_versions=(("a", "1.0"),),
        )
        assert OreContext(**kwargs) == OreContext(**kwargs)

    def test_inequality_on_compiler_version(self, tmp_path: Path):
        """Different compiler_version produces inequality."""
        base = dict(
            enabled=True,
            seed=tmp_path / "lib.ore",
            include_paths=(),
            mojo_path="/usr/bin/mojo",
            runtime_lib_dir=tmp_path,
            dep_versions=(),
        )
        a = OreContext(compiler_version="2025.6.1", **base)
        b = OreContext(compiler_version="2025.7.0", **base)
        assert a != b


class TestOreProbeResult:
    """OreProbeResult frozen dataclass for LLVM tool availability."""

    def test_all_tools_available(self):
        """When all LLVM tools are found, available=True."""
        from mojox._ore import OreProbeResult

        result = OreProbeResult(
            available=True,
            missing_tool=None,
            llvm_extract="/usr/bin/llvm-extract",
            llc="/usr/bin/llc",
            llvm_nm="/usr/bin/llvm-nm",
            clang="/usr/bin/clang",
        )
        assert result.available is True
        assert result.missing_tool is None
        assert result.llvm_extract == "/usr/bin/llvm-extract"
        assert result.llc == "/usr/bin/llc"
        assert result.llvm_nm == "/usr/bin/llvm-nm"
        assert result.clang == "/usr/bin/clang"

    def test_missing_tool_reports_first_absent(self):
        """When a tool is missing, available=False and missing_tool is set."""
        from mojox._ore import OreProbeResult

        result = OreProbeResult(
            available=False,
            missing_tool="llvm-extract",
            llvm_extract=None,
            llc="/usr/bin/llc",
            llvm_nm="/usr/bin/llvm-nm",
            clang="/usr/bin/clang",
        )
        assert result.available is False
        assert result.missing_tool == "llvm-extract"
        assert result.llvm_extract is None

    def test_frozen_immutability(self):
        """OreProbeResult is frozen — attribute assignment raises."""
        from mojox._ore import OreProbeResult

        result = OreProbeResult(
            available=True,
            missing_tool=None,
            llvm_extract="/usr/bin/llvm-extract",
            llc="/usr/bin/llc",
            llvm_nm="/usr/bin/llvm-nm",
            clang="/usr/bin/clang",
        )
        with pytest.raises(AttributeError):
            result.available = False  # type: ignore[misc]

    def test_all_tools_missing(self):
        """When no LLVM tools are found, available=False and all paths are None."""
        from mojox._ore import OreProbeResult

        result = OreProbeResult(
            available=False,
            missing_tool="llvm-extract",
            llvm_extract=None,
            llc=None,
            llvm_nm=None,
            clang=None,
        )
        assert result.available is False
        assert result.llvm_extract is None
        assert result.llc is None
        assert result.llvm_nm is None
        assert result.clang is None


class TestProbeLlvmTools:
    """probe_llvm_tools() uses shutil.which to check PATH."""

    def test_all_tools_found(self, monkeypatch: pytest.MonkeyPatch):
        """When all tools are on PATH, probe returns available=True."""
        from mojox._ore import probe_llvm_tools

        def fake_which(name: str) -> str | None:
            tools = {
                "llvm-extract": "/usr/bin/llvm-extract",
                "llc": "/usr/bin/llc",
                "llvm-nm": "/usr/bin/llvm-nm",
                "clang": "/usr/bin/clang",
            }
            return tools.get(name)

        monkeypatch.setattr(shutil, "which", fake_which)
        result = probe_llvm_tools()
        assert result.available is True
        assert result.missing_tool is None
        assert result.llvm_extract == "/usr/bin/llvm-extract"
        assert result.llc == "/usr/bin/llc"
        assert result.llvm_nm == "/usr/bin/llvm-nm"
        assert result.clang == "/usr/bin/clang"

    def test_missing_one_tool(self, monkeypatch: pytest.MonkeyPatch):
        """When one tool is missing, probe returns available=False."""
        from mojox._ore import probe_llvm_tools

        def fake_which(name: str) -> str | None:
            tools = {
                "llvm-extract": "/usr/bin/llvm-extract",
                "llc": None,
                "llvm-nm": "/usr/bin/llvm-nm",
                "clang": "/usr/bin/clang",
            }
            return tools.get(name)

        monkeypatch.setattr(shutil, "which", fake_which)
        result = probe_llvm_tools()
        assert result.available is False
        assert result.missing_tool == "llc"

    def test_missing_all_tools(self, monkeypatch: pytest.MonkeyPatch):
        """When no tools are found, the first checked tool is reported."""
        from mojox._ore import probe_llvm_tools

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        result = probe_llvm_tools()
        assert result.available is False
        assert result.missing_tool is not None
        assert result.llvm_extract is None
        assert result.llc is None
        assert result.llvm_nm is None
        assert result.clang is None

    def test_missing_tool_is_first_absent(self, monkeypatch: pytest.MonkeyPatch):
        """missing_tool reports the first tool that is absent, in probe order."""
        from mojox._ore import probe_llvm_tools

        def fake_which(name: str) -> str | None:
            # Only clang is present; the rest are missing.
            if name == "clang":
                return "/usr/bin/clang"
            return None

        monkeypatch.setattr(shutil, "which", fake_which)
        result = probe_llvm_tools()
        assert result.available is False
        # The first missing tool in probe order should be reported.
        assert result.missing_tool == "llvm-extract"

    def test_probe_returns_frozen_result(self, monkeypatch: pytest.MonkeyPatch):
        """The returned OreProbeResult is frozen."""
        from mojox._ore import probe_llvm_tools

        monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/" + _name)
        result = probe_llvm_tools()
        with pytest.raises(AttributeError):
            result.available = False  # type: ignore[misc]
