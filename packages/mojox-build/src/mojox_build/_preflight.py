"""Preflight checks: validate environment + config before a build."""

from __future__ import annotations

from pathlib import Path

from mojox_core import ConfigError, Manifest, Toolchain


def check(root: Path, manifest: Manifest, toolchain: Toolchain) -> None:
    """Raise ConfigError if the build environment is invalid.

    Verifies that package directories, binary sources, native libs,
    and the readme file exist on disk. Toolchain validation is handled
    by mojox-core's ``io.toolchain.resolve()``.
    """
    _check_package_dirs(root, manifest)
    if not manifest.pre_build:
        _check_native_libs(root, manifest)
    _check_binaries(root, manifest)
    _check_readme(root, manifest)


def check_post_pre_build(root: Path, manifest: Manifest) -> None:
    """Validate artifacts that a pre-build hook is expected to produce."""
    if manifest.pre_build:
        _check_native_libs(root, manifest)


def _check_package_dirs(root: Path, manifest: Manifest) -> None:
    """Verify that declared package directories exist."""
    if manifest.packages is not None:
        missing = [p for p in manifest.packages if not (root / p).is_dir()]
        if missing:
            raise ConfigError(
                "tool.mojox.packages",
                f"references nonexistent directories: {missing} (relative to {root})",
            )
        return

    if manifest.binaries:
        return

    pkg_root = root / manifest.package_root
    if not pkg_root.is_dir():
        raise ConfigError(
            "tool.mojox.package-root",
            f"{manifest.package_root!r} not found at {pkg_root}. "
            "Either create it, or set `packages = [...]` explicitly.",
        )
    if not any(p.is_dir() for p in pkg_root.iterdir()):
        raise ConfigError(
            "tool.mojox.package-root",
            f"no package directories found under {pkg_root}. Each top-level "
            "directory becomes one compiled Mojo package in the wheel.",
        )


def _check_binaries(root: Path, manifest: Manifest) -> None:
    """Verify that declared binary source files exist."""
    missing = [b.source for b in manifest.binaries if not (root / b.source).is_file()]
    if missing:
        raise ConfigError(
            "tool.mojox.binaries",
            f"references files that do not exist: {missing} (relative to {root})",
        )


def _check_native_libs(root: Path, manifest: Manifest) -> None:
    """Verify that declared native library files exist."""
    missing = [p for p in manifest.native_libs if not (root / p).is_file()]
    if missing:
        raise ConfigError(
            "tool.mojox.native-libs",
            f"references files that do not exist: {missing}. "
            "Build them before invoking `uv build`.",
        )


def _check_readme(root: Path, manifest: Manifest) -> None:
    """Verify that the declared README file exists."""
    if manifest.readme and not (root / manifest.readme).is_file():
        raise ConfigError(
            "project.readme",
            f"{manifest.readme!r} does not exist",
        )
