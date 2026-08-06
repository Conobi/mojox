"""Ore acceleration: LLVM bitcode splitting for 7x dev-build speedup.

Ore pre-compiles the library portion of a program's LLVM output into a
.ore file (a standard relocatable object). Subsequent targets link their
user-specific code against it, skipping the expensive LLVM codegen for
the library. The .ore is cached per compiler version and dependency tree.

This module lives in mojox (not mojox-core) because it is an exec-layer
optimization that shells out to LLVM tools.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

# Tools checked by probe_llvm_tools(), in probe order.
_LLVM_TOOLS: tuple[str, ...] = ("llvm-extract", "llc", "llvm-nm", "clang")


@dataclass(frozen=True)
class OreContext:
    """Configuration snapshot for ore acceleration on a single build.

    All fields needed to decide whether a cached .ore file is still valid
    and to drive the LLVM bitcode splitting pipeline.

    Attributes:
        enabled: Whether ore acceleration is active for this invocation.
        seed: Path to an existing .ore file to reuse, or None.
        include_paths: Extra include directories passed to the compiler.
        compiler_version: Version string of the active Mojo compiler.
        mojo_path: Absolute path to the ``mojo`` binary.
        runtime_lib_dir: Directory containing the Mojo runtime library.
        dep_versions: Sorted (name, version) pairs for all dependencies.
    """

    enabled: bool
    seed: Path | None
    include_paths: tuple[str, ...]
    compiler_version: str
    mojo_path: str
    runtime_lib_dir: Path
    dep_versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class OreProbeResult:
    """Result of probing the host for required LLVM tools.

    Attributes:
        available: True when every required tool was found on PATH.
        missing_tool: Name of the first tool that was not found, or None.
        llvm_extract: Resolved path to ``llvm-extract``, or None.
        llc: Resolved path to ``llc``, or None.
        llvm_nm: Resolved path to ``llvm-nm``, or None.
        clang: Resolved path to ``clang``, or None.
    """

    available: bool
    missing_tool: str | None
    llvm_extract: str | None
    llc: str | None
    llvm_nm: str | None
    clang: str | None


def probe_llvm_tools() -> OreProbeResult:
    """Check PATH for the LLVM tools required by ore acceleration.

    Uses :func:`shutil.which` to locate each tool. The probe order is
    deterministic: ``llvm-extract``, ``llc``, ``llvm-nm``, ``clang``.
    If any tool is missing, ``available`` is False and ``missing_tool``
    names the first absent tool.

    Returns:
        An OreProbeResult describing tool availability.
    """
    paths: dict[str, str | None] = {}
    first_missing: str | None = None

    for tool in _LLVM_TOOLS:
        resolved = shutil.which(tool)
        paths[tool] = resolved
        if resolved is None and first_missing is None:
            first_missing = tool

    return OreProbeResult(
        available=first_missing is None,
        missing_tool=first_missing,
        llvm_extract=paths["llvm-extract"],
        llc=paths["llc"],
        llvm_nm=paths["llvm-nm"],
        clang=paths["clang"],
    )
