"""Read the installed environment — the effectful half of environment building.

Enumerates installed distributions via importlib.metadata, checks which
ship mojo_packages/, reads uv.lock when present, and gathers HostFacts.
"""

from __future__ import annotations

import importlib.metadata
import os
import sysconfig
from pathlib import Path

from .._types import DistKind, HostFacts


def read_distributions(platlib: str | None = None) -> list[dict]:
    """Enumerate installed distributions that ship Mojo packages.

    Returns a list of dicts suitable for passing to environment.build_env().
    """
    if platlib is None:
        platlib = sysconfig.get_path("platlib")
    assert platlib is not None

    mojo_packages_dir = Path(platlib) / "mojo_packages"
    if not mojo_packages_dir.is_dir():
        return []

    dists: list[dict] = []
    for dist in importlib.metadata.distributions():
        if dist.files is None:
            continue
        has_mojo = False
        packages: list[str] = []

        for f in dist.files:
            parts = str(f).split("/")
            if len(parts) >= 2 and parts[0] == "mojo_packages":
                has_mojo = True
                pkg_name = parts[1]
                if pkg_name.endswith(".mojoc") or pkg_name.endswith(".mojopkg"):
                    pkg_name = pkg_name.rsplit(".", 1)[0]
                    kind = DistKind.PRECOMPILED
                elif not pkg_name.startswith("_") and pkg_name != "lib":
                    kind = DistKind.SOURCE
                else:
                    continue
                if pkg_name not in packages:
                    packages.append(pkg_name)

        if has_mojo and packages:
            dists.append({
                "name": dist.metadata["Name"],
                "include_dir": str(mojo_packages_dir),
                "kind": kind,
                "packages": packages,
                "provenance": dist.metadata.get("Version", "unknown"),
                "native_lib_dirs": (),
            })

    return dists


def read_lockfile(project_root: Path) -> dict | None:
    """Read uv.lock if it exists, returning parsed TOML or None."""
    import sys

    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[import-not-found, no-redef]

    lock_path = project_root / "uv.lock"
    if not lock_path.is_file():
        return None

    try:
        with open(lock_path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return None


def read_host_facts(manifest_dir: Path) -> HostFacts:
    """Gather machine-specific facts for the planner."""
    from pathlib import PurePosixPath

    cpu_count = os.cpu_count() or 1
    mem_mb = _available_memory_mb()

    return HostFacts(
        cpu_count=cpu_count,
        available_memory_mb=mem_mb,
        manifest_dir=PurePosixPath(str(manifest_dir.resolve())),
    )


def _available_memory_mb() -> int:
    """Best-effort available memory in MB."""
    try:
        import resource  # noqa: F401

        # Try /proc/meminfo on Linux
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (ImportError, OSError):
        pass
    return 16384
