"""Build the resolved environment from distribution data.

Pure transformer -- receives pre-read distribution info and returns ResolvedEnv.
The IO reader that enumerates distributions lives in mojox_core.io.environment.
"""

from __future__ import annotations

from ._errors import ConfigError
from ._types import Diagnostic, DistEntry, DistKind, ResolvedEnv

_MAX_KNOWN_LOCK_VERSION = 1


def build_env(
    dists: list[dict],
    lock_data: dict | None,
    mojo_path: str,
    mojo_version: str,
    *,
    path_mojo: str | None = None,
) -> ResolvedEnv:
    """Build a ResolvedEnv from distribution data and optional lockfile.

    Args:
        dists: List of distribution dicts, each containing 'name',
            'include_dir', 'kind', 'packages', 'provenance', and
            'native_lib_dirs' keys.
        lock_data: Parsed lockfile data (with 'version' key), or None.
        mojo_path: Path to the Mojo compiler in the virtual environment.
        mojo_version: Version string of the Mojo compiler.
        path_mojo: Path to Mojo found on PATH, if different from mojo_path.

    Returns:
        A fully resolved ResolvedEnv instance.

    Raises:
        ConfigError: If a package appears as both source and precompiled
            in the same include directory.
    """
    diagnostics: list[Diagnostic] = []
    entries: list[DistEntry] = []

    _check_source_shadows_precompiled(dists)

    for d in dists:
        entries.append(
            DistEntry(
                name=d["name"],
                include_dir=d["include_dir"],
                kind=d["kind"],
                packages=tuple(d["packages"]),
                provenance=d.get("provenance", "unknown"),
                native_lib_dirs=tuple(d.get("native_lib_dirs", ())),
            )
        )

    lock_version = _extract_lock_version(lock_data, diagnostics)

    if path_mojo is not None and path_mojo != mojo_path:
        diagnostics.append(
            Diagnostic(
                kind="warning",
                message=f"PATH mojo ({path_mojo}) differs from environment mojo "
                f"({mojo_path}). The environment mojo will be used.",
            )
        )

    return ResolvedEnv(
        include_sequence=tuple(entries),
        mojo_path=mojo_path,
        mojo_version=mojo_version,
        path_mojo=path_mojo,
        lock_version=lock_version,
        diagnostics=tuple(diagnostics),
    )


def _check_source_shadows_precompiled(dists: list[dict]) -> None:
    """Raise ConfigError if any package is both source and precompiled in the same dir.

    Args:
        dists: List of distribution dicts to validate.

    Raises:
        ConfigError: If a source-shadows-precompiled conflict is detected.
    """
    dir_packages: dict[str, dict[str, list[DistKind]]] = {}
    for d in dists:
        include_dir = d["include_dir"]
        by_name = dir_packages.setdefault(include_dir, {})
        for pkg in d["packages"]:
            by_name.setdefault(pkg, []).append(d["kind"])

    for include_dir, pkgs_by_name in dir_packages.items():
        for pkg_name, kinds in pkgs_by_name.items():
            if DistKind.SOURCE in kinds and DistKind.PRECOMPILED in kinds:
                raise ConfigError(
                    f"environment.{include_dir}",
                    f"source-shadows-precompiled: package {pkg_name!r} exists "
                    f"as both source and precompiled in {include_dir!r}. The "
                    "source silently wins and the consumer is on the slow "
                    "path. Remove one.",
                )


def _extract_lock_version(
    lock_data: dict | None,
    diagnostics: list[Diagnostic],
) -> int | None:
    """Extract and validate the lockfile schema version.

    Args:
        lock_data: Parsed lockfile data, or None.
        diagnostics: Mutable list to append warnings to.

    Returns:
        The lock version integer, or None if no lockfile was provided.
    """
    if lock_data is None:
        return None
    lock_version = lock_data.get("version")
    if lock_version is not None and lock_version > _MAX_KNOWN_LOCK_VERSION:
        diagnostics.append(
            Diagnostic(
                kind="warning",
                message=f"uv.lock schema version {lock_version} is above the "
                f"known maximum ({_MAX_KNOWN_LOCK_VERSION}). Provenance data "
                "may be incomplete.",
            )
        )
    return lock_version
