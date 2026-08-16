"""PEP 517 + PEP 660 hook implementations.

The hooks live here so ``__init__.py`` can stay a clean re-export surface.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from mojox_core import (
    ConfigError,
    LocalSettings,
    Manifest,
    parse_manifest,
    resolve,
)
from mojox_core.io.manifest import read as read_manifest
from mojox_core.io.toolchain import resolve as resolve_toolchain

from .build import (
    GENERATOR_VERSION,
    _normalize_name,
    _resolve_package_dirs,
    build_editable_wheel,
    build_sdist,
    build_wheel,
    host_platform_tag,
)
from .metadata import render_metadata, render_wheel_file
from .preflight import check as _preflight


def _verbose_from(config_settings: dict[str, object] | None) -> bool:
    """Extract the verbose flag from PEP 517 config_settings."""
    if not config_settings:
        return False
    v = config_settings.get("verbose")
    if isinstance(v, bool):
        return v
    return str(v).lower() in {"1", "true", "yes", "on"}


def _load(root: Path) -> Manifest:
    """Load and parse pyproject.toml into a Manifest."""
    raw = read_manifest(root / "pyproject.toml")
    return parse_manifest(raw)


_T = TypeVar("_T")


def _run(action: Callable[..., _T], *args: object, **kwargs: object) -> _T:
    """Run a hook, converting ConfigError into a clean fatal message."""
    try:
        return action(*args, **kwargs)
    except ConfigError as e:
        print(f"\nmojox-build: {e}\n", file=sys.stderr)
        raise SystemExit(1) from e


# ============================================================
# PEP 517 — wheels
# ============================================================


def hook_build_wheel(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build a wheel containing compiled Mojo packages."""
    del metadata_directory

    def _do() -> str:
        root = Path.cwd()
        manifest = _load(root)
        toolchain = resolve_toolchain()
        policy = resolve(manifest, manifest.build_profile, {}, LocalSettings.EMPTY)
        _preflight(root, manifest, toolchain)
        return build_wheel(
            root,
            manifest,
            policy,
            toolchain,
            wheel_directory=Path(wheel_directory),
            verbose=_verbose_from(config_settings),
        )

    return _run(_do)


def hook_get_requires_for_build_wheel(config_settings: dict[str, object] | None = None) -> list[str]:
    """Return additional requirements for building a wheel."""
    del config_settings
    return []


def hook_prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    """Prepare wheel metadata without building the wheel."""
    del config_settings

    def _do() -> str:
        root = Path.cwd()
        manifest = _load(root)
        toolchain = resolve_toolchain()

        has_native = bool(manifest.native_libs) or bool(manifest.binaries)
        tag = f"py3-none-{host_platform_tag()}" if has_native else "py3-none-any"

        has_compiled = bool(_resolve_package_dirs(root, manifest))
        compiler_version = toolchain.version if has_compiled else None

        name = _normalize_name(manifest.name)
        dist_info_name = f"{name}-{manifest.version}.dist-info"
        dist_info = Path(metadata_directory) / dist_info_name
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(render_metadata(manifest, root, [], compiler_version=compiler_version))
        (dist_info / "WHEEL").write_text(
            render_wheel_file(
                tag=tag,
                root_is_purelib=False,
                generator_version=GENERATOR_VERSION,
            )
        )
        return dist_info_name

    return _run(_do)


# ============================================================
# PEP 517 — sdists
# ============================================================


def hook_build_sdist(
    sdist_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    """Build a source distribution."""
    del config_settings

    def _do() -> str:
        root = Path.cwd()
        manifest = _load(root)
        return build_sdist(root, manifest, sdist_directory=Path(sdist_directory))

    return _run(_do)


def hook_get_requires_for_build_sdist(config_settings: dict[str, object] | None = None) -> list[str]:
    """Return additional requirements for building an sdist."""
    del config_settings
    return []


# ============================================================
# PEP 660 — editable installs
# ============================================================


def hook_build_editable(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build an editable wheel that symlinks source dirs at runtime."""
    del metadata_directory

    def _do() -> str:
        root = Path.cwd()
        manifest = _load(root)
        toolchain = resolve_toolchain()
        policy = resolve(manifest, manifest.build_profile, {}, LocalSettings.EMPTY)
        _preflight(root, manifest, toolchain)
        return build_editable_wheel(
            root,
            manifest,
            policy,
            toolchain,
            wheel_directory=Path(wheel_directory),
            verbose=_verbose_from(config_settings),
        )

    return _run(_do)


def hook_get_requires_for_build_editable(config_settings: dict[str, object] | None = None) -> list[str]:
    """Return additional requirements for building an editable wheel."""
    del config_settings
    return []


def hook_prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    """Prepare editable-wheel metadata (delegates to the wheel variant)."""
    return hook_prepare_metadata_for_build_wheel(metadata_directory, config_settings)
