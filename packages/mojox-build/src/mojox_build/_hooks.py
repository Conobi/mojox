"""PEP 517 + PEP 660 hook implementations.

The hooks live here so `__init__.py` can stay a clean re-export surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ._build import GENERATOR_VERSION, build_sdist, build_wheel, host_platform_tag
from ._config import BuildConfigError, load, normalize_name
from ._metadata import render_metadata, render_wheel_file
from ._preflight import check as _preflight


def _verbose_from(config_settings: dict | None) -> bool:
    if not config_settings:
        return False
    v = config_settings.get("verbose")
    if isinstance(v, bool):
        return v
    return str(v).lower() in {"1", "true", "yes", "on"}


def _run(action, *args, **kwargs):
    """Run a hook, converting BuildConfigError into a clean fatal message."""
    try:
        return action(*args, **kwargs)
    except BuildConfigError as e:
        print(f"\nmojox-build: {e}\n", file=sys.stderr)
        raise SystemExit(1) from e


# ============================================================
# PEP 517 — wheels
# ============================================================


def hook_build_wheel(
    wheel_directory: str,
    config_settings: dict | None = None,
    metadata_directory: str | None = None,
) -> str:
    del metadata_directory

    def _do() -> str:
        root = Path.cwd()
        project, backend = load(root / "pyproject.toml")
        _preflight(root, project, backend)
        return build_wheel(
            root,
            project,
            backend,
            wheel_directory=Path(wheel_directory),
            verbose=_verbose_from(config_settings),
        )

    return _run(_do)


def hook_get_requires_for_build_wheel(config_settings: dict | None = None) -> list[str]:
    del config_settings
    return []


def hook_prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict | None = None,
) -> str:
    del config_settings

    def _do() -> str:
        root = Path.cwd()
        project, _ = load(root / "pyproject.toml")
        name = normalize_name(project.name)
        dist_info_name = f"{name}-{project.version}.dist-info"
        dist_info = Path(metadata_directory) / dist_info_name
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(render_metadata(project, root, []))
        (dist_info / "WHEEL").write_text(
            render_wheel_file(
                tag=f"py3-none-{host_platform_tag()}",
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
    config_settings: dict | None = None,
) -> str:
    del config_settings

    def _do() -> str:
        root = Path.cwd()
        project, backend = load(root / "pyproject.toml")
        # Preflight is skipped for sdist (no compilation happens).
        return build_sdist(
            root, project, backend, sdist_directory=Path(sdist_directory)
        )

    return _run(_do)


def hook_get_requires_for_build_sdist(config_settings: dict | None = None) -> list[str]:
    del config_settings
    return []


# ============================================================
# PEP 660 — editable installs
# ============================================================
# Mojo compiles to .mojopkg artifacts; there is no source-import mode. So
# "editable" effectively means "build a normal wheel". Consumers who want
# rebuild-on-change semantics should add a uv cache-keys entry like:
#
#     [tool.uv]
#     cache-keys = [{ file = "pyproject.toml" }, { file = "**/*.mojo" }]


def hook_build_editable(
    wheel_directory: str,
    config_settings: dict | None = None,
    metadata_directory: str | None = None,
) -> str:
    return hook_build_wheel(wheel_directory, config_settings, metadata_directory)


def hook_get_requires_for_build_editable(config_settings: dict | None = None) -> list[str]:
    del config_settings
    return []


def hook_prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict | None = None,
) -> str:
    return hook_prepare_metadata_for_build_wheel(metadata_directory, config_settings)
