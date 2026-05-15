"""Preflight checks: validate environment + config before a build."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ._config import BackendConfig, BuildConfigError, ProjectMetadata


def check(root: Path, project: ProjectMetadata, backend: BackendConfig) -> None:
    """Raise BuildConfigError with a clean message if anything is off."""
    _check_mojo_binary()
    _check_package_dirs(root, backend)
    # native-libs may be produced by a pre-build hook, so the existence check
    # is deferred to check_post_pre_build() (run after _run_pre_build).
    if not backend.pre_build:
        _check_native_libs(root, backend)
    _check_readme(root, project)


def check_post_pre_build(root: Path, backend: BackendConfig) -> None:
    """Validate artifacts that the pre-build hook is expected to produce."""
    if backend.pre_build:
        _check_native_libs(root, backend)


def _check_mojo_binary() -> None:
    if not shutil.which("mojo"):
        raise BuildConfigError(
            "cannot find `mojo` on PATH. Add a `mojo-compiler` requirement to "
            "[build-system].requires so uv installs it in the build environment."
        )
    result = subprocess.run(
        ["mojo", "--version"], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise BuildConfigError(
            f"`mojo --version` failed (exit {result.returncode}). stderr:\n"
            f"  {result.stderr.strip()}"
        )


def _check_package_dirs(root: Path, backend: BackendConfig) -> None:
    if backend.packages is not None:
        missing = [p for p in backend.packages if not (root / p).is_dir()]
        if missing:
            raise BuildConfigError(
                f"[tool.mojox-build].packages references nonexistent directories: "
                f"{missing} (relative to {root})."
            )
        return

    pkg_root = root / backend.package_root
    if not pkg_root.is_dir():
        raise BuildConfigError(
            f"[tool.mojox-build].package-root = {backend.package_root!r} not found "
            f"at {pkg_root}. Either create it, or set `packages = [...]` explicitly."
        )
    if not any(p.is_dir() for p in pkg_root.iterdir()):
        raise BuildConfigError(
            f"no package directories found under {pkg_root}. Each top-level "
            f"directory becomes one .mojopkg in the wheel."
        )


def _check_native_libs(root: Path, backend: BackendConfig) -> None:
    missing = [p for p in backend.native_libs if not (root / p).is_file()]
    if missing:
        raise BuildConfigError(
            f"[tool.mojox-build].native-libs references files that do not exist: "
            f"{missing}. Build them before invoking `uv build`."
        )


def _check_readme(root: Path, project: ProjectMetadata) -> None:
    if project.readme and not (root / project.readme).is_file():
        raise BuildConfigError(
            f"[project].readme = {project.readme!r} does not exist."
        )
