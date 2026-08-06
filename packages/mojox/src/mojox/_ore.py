"""Ore acceleration: LLVM bitcode splitting for 7x dev-build speedup.

Ore pre-compiles the library portion of a program's LLVM output into a
.ore file (a standard relocatable object). Subsequent targets link their
user-specific code against it, skipping the expensive LLVM codegen for
the library. The .ore is cached per compiler version and dependency tree.

This module lives in mojox (not mojox-core) because it is an exec-layer
optimization that shells out to LLVM tools.
"""

from __future__ import annotations

import hashlib
import os
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
        seed: Path to the ore-seed .mojo source file, or None for first-target-as-seed.
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


def compute_cache_key(
    compiler_version: str,
    dep_versions: tuple[tuple[str, str], ...],
    seed_path: Path | None = None,
) -> str:
    """Compute a 24-character hex cache key from build inputs.

    The key is the first 24 hex characters of a SHA-256 digest built from:

    1. ``"compiler:{version}\\n"``
    2. Sorted ``"dep:{name}=={ver}\\n"`` lines for each dependency
    3. The seed file's raw bytes if *seed_path* is a regular file,
       otherwise the literal ``"seed:implicit\\n"``

    No file I/O is performed for dependency versions; only version strings
    are hashed. The *seed_path* file is read only when it exists and is a
    regular file.

    Args:
        compiler_version: The Mojo compiler version string.
        dep_versions: Sorted (name, version) pairs for all dependencies.
        seed_path: Optional path to the ore-seed source file.

    Returns:
        A 24-character lowercase hex digest string.
    """
    h = hashlib.sha256()
    h.update(f"compiler:{compiler_version}\n".encode())

    for name, ver in sorted(dep_versions):
        h.update(f"dep:{name}=={ver}\n".encode())

    if seed_path is not None and seed_path.is_file():
        h.update(seed_path.read_bytes())
    else:
        h.update(b"seed:implicit\n")

    return h.hexdigest()[:24]


class OreCache:
    """Manages cached .ore files in a directory, keyed by content hash.

    Each cached artifact is stored at ``<cache_dir>/<key>/lib.ore``.
    Writes are atomic: the source file is first copied to a temporary name
    inside the key directory, then renamed into place via :func:`os.rename`.

    Attributes:
        cache_dir: Root directory for all cached .ore artifacts.
    """

    def __init__(self, cache_dir: Path) -> None:
        """Initialise the cache at the given directory.

        Args:
            cache_dir: Root directory for cached .ore files. Created lazily
                on the first :meth:`put` call.
        """
        self.cache_dir = cache_dir

    def get(self, key: str) -> Path | None:
        """Look up a cached .ore file by key.

        Args:
            key: The cache key (typically a 24-char hex digest).

        Returns:
            The path to ``lib.ore`` if it exists, or None on a cache miss.
        """
        candidate = self.cache_dir / key / "lib.ore"
        if candidate.is_file():
            return candidate
        return None

    def put(self, key: str, source: Path) -> Path:
        """Atomically store a .ore file in the cache.

        The *source* file is copied to a temporary name inside the key
        directory, then atomically renamed to ``lib.ore``.  This ensures
        concurrent readers never see a partially-written file.

        Args:
            key: The cache key to store under.
            source: Path to the .ore file to cache.

        Returns:
            The final path to the cached ``lib.ore`` file.
        """
        key_dir = self.cache_dir / key
        key_dir.mkdir(parents=True, exist_ok=True)

        tmp_name = f".lib.ore.{os.getpid()}.tmp"
        tmp_path = key_dir / tmp_name
        final_path = key_dir / "lib.ore"

        shutil.copy2(source, tmp_path)
        os.rename(tmp_path, final_path)

        return final_path
