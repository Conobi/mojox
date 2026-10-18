"""Read the installed environment — the effectful half of environment building.

Enumerates installed distributions via importlib.metadata, checks which
ship mojo_packages/, reads uv.lock when present, and gathers HostFacts.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import sysconfig
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..plan import select_locale_env
from ..policy import parse_count_text
from ..types import DistKind, HostFacts


def read_distributions(platlib: str | None = None) -> list[dict[str, Any]]:
    """Enumerate installed distributions that ship Mojo packages.

    Returns a list of dicts suitable for passing to environment.build_env().
    """
    if platlib is None:
        platlib = sysconfig.get_path("platlib")
    assert platlib is not None

    mojo_packages_dir = Path(platlib) / "mojo_packages"
    if not mojo_packages_dir.is_dir():
        return []

    dists: list[dict[str, Any]] = []
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
                if pkg_name.endswith((".mojoc", ".mojopkg")):
                    pkg_name = pkg_name.rsplit(".", 1)[0]
                    kind = DistKind.PRECOMPILED
                elif not pkg_name.startswith("_") and pkg_name != "lib":
                    kind = DistKind.SOURCE
                else:
                    continue
                if pkg_name not in packages:
                    packages.append(pkg_name)

        if has_mojo and packages:
            dists.append(
                {
                    "name": dist.metadata["Name"],
                    "include_dir": str(mojo_packages_dir),
                    "kind": kind,
                    "packages": packages,
                    "provenance": dist.metadata.get("Version", "unknown"),
                    "native_lib_dirs": (),
                }
            )

    return dists


def read_lockfile(project_root: Path) -> dict[str, Any] | None:
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


def read_host_facts(manifest_dir: Path, environ: Mapping[str, str] | None = None) -> HostFacts:
    """Gather machine-specific facts for the planner.

    Args:
        environ: Host environment to take the locale and ``MOJOX_CPU_COUNT``
            from; defaults to ``os.environ``. Only ``LANG`` and ``LC_*``
            reach planned commands.

    Raises:
        ConfigError: ``MOJOX_CPU_COUNT`` is set but not an integer >= 1.
    """
    from pathlib import PurePosixPath

    if environ is None:
        environ = os.environ
    cpu_count, cpu_count_source = detect_cpu_count(environ)

    return HostFacts(
        cpu_count=cpu_count,
        available_memory_mb=_available_memory_mb(),
        manifest_dir=PurePosixPath(str(manifest_dir.resolve())),
        locale_env=select_locale_env(environ),
        cpu_count_source=cpu_count_source,
    )


def _affinity_count() -> int | None:
    """CPUs this process may run on, or None where affinity is unsupported."""
    if not hasattr(os, "sched_getaffinity"):
        return None
    return len(os.sched_getaffinity(0))


def detect_cpu_count(
    environ: Mapping[str, str],
    *,
    root: Path = Path("/"),
    affinity: Callable[[], int | None] = _affinity_count,
    os_count: Callable[[], int | None] = os.cpu_count,
) -> tuple[int, str]:
    """CPUs usable by this process, and the source that bounded the count.

    ``MOJOX_CPU_COUNT``, when set, is used verbatim. Otherwise the result is
    the minimum of the affinity mask, the cgroup v2 ``cpu.max`` quotas from
    the process's cgroup up to the mount root, and ``os.cpu_count()``; on a
    tie the least specific source is reported (``os``, then ``affinity``,
    then ``cgroup``), so the source names a limit that actually binds.
    Probes that fail or are missing are skipped, and none at all gives 1.
    *root* stands in for ``/`` so tests can fake ``/proc`` and ``/sys``.

    Raises:
        ConfigError: ``MOJOX_CPU_COUNT`` is set but not an integer >= 1.
            Nothing else raises.
    """
    override = environ.get("MOJOX_CPU_COUNT")
    if override is not None:
        return parse_count_text(override, "MOJOX_CPU_COUNT"), "MOJOX_CPU_COUNT"

    candidates = [
        ("os", _probe(os_count)),
        ("affinity", _probe(affinity)),
        ("cgroup", _probe(lambda: _cgroup_cpu_limit(root))),
    ]
    available = [(count, source) for source, count in candidates if count is not None]
    if not available:
        return 1, "os"
    return min(available, key=lambda pair: pair[0])


def _probe(probe: Callable[[], int | None]) -> int | None:
    """Run a CPU-count probe; failures and non-positive answers count as unavailable."""
    try:
        count = probe()
    except Exception:
        return None
    return count if count is not None and count >= 1 else None


_DIGITS = re.compile(r"[0-9]+")


def _cgroup_cpu_limit(root: Path) -> int | None:
    """Tightest cgroup v2 CPU quota on the way from this process's cgroup to the mount root.

    The cgroup path from ``/proc/self/cgroup`` is joined lexically below
    the mount and rejected outright if it has a ``..`` component (a process
    outside its cgroup namespace sees one), so no file above the mount is
    ever read. If the path does not exist below the mount (a container
    without its own cgroup namespace), only the mount root is read.
    cgroup v1 is not supported.
    """
    try:
        lines = (root / "proc" / "self" / "cgroup").read_text(encoding="ascii").splitlines()
    except (OSError, ValueError):
        return None
    paths = [line[len("0::") :] for line in lines if line.startswith("0::")]
    if len(paths) != 1:
        return None
    parts = [part for part in paths[0].strip().split("/") if part not in ("", ".")]
    if ".." in parts:
        return None

    mount = root / "sys" / "fs" / "cgroup"
    if not mount.joinpath(*parts).is_dir():
        parts = []
    limits = [_read_cpu_max(mount.joinpath(*parts[:depth]) / "cpu.max") for depth in range(len(parts), -1, -1)]
    found = [limit for limit in limits if limit is not None]
    return min(found) if found else None


def _read_cpu_max(path: Path) -> int | None:
    """Parse ``cpu.max`` (``"<quota> <period>"`` or ``"max <period>"``) into ceil(quota/period), min 1.

    None for unlimited, missing, unreadable or malformed files, including a
    zero quota or period.
    """
    try:
        fields = path.read_text(encoding="ascii").split()
    except (OSError, ValueError):
        return None
    if len(fields) != 2 or not all(_DIGITS.fullmatch(f) for f in fields):
        return None
    try:
        quota, period = int(fields[0]), int(fields[1])
    except ValueError:  # beyond the interpreter's int-from-str digit limit
        return None
    if quota == 0 or period == 0:
        return None
    return max(1, -(-quota // period))


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
