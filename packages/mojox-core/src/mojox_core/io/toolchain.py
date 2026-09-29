"""Resolve the Mojo toolchain from installed distributions.

Uses importlib.metadata to find the mojo-compiler distribution, its
console script entry point, and version. Never invokes mojo --version.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

from ..errors import ConfigError
from ..types import Toolchain


def resolve() -> Toolchain:
    """Resolve the installed Mojo toolchain.

    Never runs a subprocess. Only Mojo 1.0+ (including its pre-releases)
    is supported: the toolchain surface is always ``mojo precompile`` and
    ``.mojoc``, neither of which exists in 0.x compilers.

    Raises:
        ConfigError: If ``mojo-compiler`` is not installed, is older than
            1.0, or its ``mojo`` binary is missing from the file list.
    """
    try:
        dist = importlib.metadata.distribution("mojo-compiler")
    except importlib.metadata.PackageNotFoundError:
        raise ConfigError(
            "toolchain",
            "mojo-compiler is not installed. Install it with: uv add mojo-compiler",
        )

    version = dist.metadata["Version"]
    _require_precompile(version)

    return Toolchain(
        mojo_path=_find_console_script(dist),
        version=version,
        subcommand="precompile",
        extension=".mojoc",
    )


def _require_precompile(version: str) -> None:
    """Reject 0.x compilers before any command fails with ``no such command 'precompile'``.

    Compares the release major only, so 1.0 pre-releases pass. An
    unparseable version is let through rather than blocking a build.
    """
    try:
        major = Version(version).major
    except InvalidVersion:
        return
    if major < 1:
        raise ConfigError(
            "toolchain",
            f"mojo-compiler {version} is too old: mojox needs Mojo 1.0 or newer "
            "(`mojo precompile`). Upgrade it with: uv add 'mojo-compiler>=1.0'",
        )


def _find_console_script(dist: importlib.metadata.Distribution) -> str:
    """Find the mojo console script path from the distribution.

    Searches the distribution's recorded file list for a file named ``mojo``
    (either at the top of a scripts directory or under ``bin/``). Falls back
    to inspecting entry_points if the file list doesn't yield a match.

    Args:
        dist: The ``mojo-compiler`` distribution object.

    Returns:
        The absolute path to the ``mojo`` binary as a string.

    Raises:
        ConfigError: If the binary cannot be located.
    """
    # Look in the scripts directory
    if dist.files:
        for f in dist.files:
            parts = str(f).split("/")
            if len(parts) >= 1 and parts[-1] == "mojo":
                located = dist.locate_file(f)
                if located and Path(str(located)).exists():
                    return str(Path(str(located)).resolve())

    # Fallback: try the entry_points
    eps = dist.entry_points
    for ep in eps:
        if ep.name == "mojo":
            # entry_points don't directly give us a path; use the scripts dir
            break

    # Last resort: look in the same directory as the package
    pkg_files = dist.files or []
    for f in pkg_files:
        if str(f).endswith("/bin/mojo") or str(f) == "bin/mojo":
            located = dist.locate_file(f)
            if located:
                return str(Path(str(located)).resolve())

    raise ConfigError(
        "toolchain",
        "mojo-compiler is installed but the `mojo` binary was not found in its file list",
    )
