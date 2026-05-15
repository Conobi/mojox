"""Compile Mojo packages and assemble wheels / sdists."""

from __future__ import annotations

import concurrent.futures
import hashlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
import zipfile
from base64 import urlsafe_b64encode
from fnmatch import fnmatch
from pathlib import Path

from ._config import BackendConfig, ProjectMetadata, normalize_name
from ._metadata import render_metadata, render_wheel_file

GENERATOR_VERSION = "0.2.0"

# ZIP can't represent dates before 1980; SOURCE_DATE_EPOCH=0 must clamp up.
_ZIP_EPOCH_FLOOR = 315532800  # 1980-01-01 UTC


def host_platform_tag() -> str:
    """Return a PEP 425 platform tag for the host."""
    try:
        from packaging.tags import sys_tags  # type: ignore[import-not-found]

        for tag in sys_tags():
            if tag.platform != "any":
                return tag.platform
    except ImportError:
        pass
    return sysconfig.get_platform().replace("-", "_").replace(".", "_")


# ============================================================
# Compilation
# ============================================================


def _compile_mojopkg(
    source_dir: Path,
    output: Path,
    cfg: BackendConfig,
    *,
    verbose: bool,
) -> None:
    cmd = ["mojo", "package", str(source_dir), "-o", str(output)]
    # Auto-inject -I for uv-installed Mojo packages so cross-package imports
    # resolve during PEP 517 builds (mirrors the mojox CLI wrapper's behavior).
    pkg_path = sysconfig.get_path("platlib") + "/mojo_packages"
    if os.path.isdir(pkg_path):
        cmd.extend(["-I", pkg_path])
    for key, value in cfg.defines.items():
        cmd.extend(["-D", f"{key}={value}"])
    cmd.extend(cfg.flags)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"`mojo package` failed for {source_dir.name}:\n"
            f"  cmd:    {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    if verbose:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")


def _resolve_package_dirs(root: Path, cfg: BackendConfig) -> list[Path]:
    if cfg.packages is not None:
        return [root / name for name in cfg.packages]
    pkg_root = root / cfg.package_root
    return [p for p in sorted(pkg_root.iterdir()) if p.is_dir()]


def _compile_all(
    packages: list[Path],
    out_dir: Path,
    cfg: BackendConfig,
    *,
    verbose: bool,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [(src, out_dir / f"{src.name}.mojopkg") for src in packages]

    if len(tasks) <= 1:
        for src, out in tasks:
            _compile_mojopkg(src, out, cfg, verbose=verbose)
        return

    workers = min(len(tasks), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_compile_mojopkg, src, out, cfg, verbose=verbose)
            for src, out in tasks
        ]
        for f in concurrent.futures.as_completed(futures):
            f.result()


def _copy_native_libs(root: Path, lib_dir: Path, native_libs: list[str]) -> None:
    if not native_libs:
        return
    lib_dir.mkdir(parents=True, exist_ok=True)
    for rel in native_libs:
        src = root / rel
        shutil.copy2(src, lib_dir / src.name)


def _copy_license_files(
    root: Path, dist_info: Path, license_files: list[str]
) -> list[str]:
    if not license_files:
        return []
    licenses_dir = dist_info / "licenses"
    licenses_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    seen: set[str] = set()
    for pattern in license_files:
        for src in sorted(root.glob(pattern)):
            if src.is_file() and src.name not in seen:
                shutil.copy2(src, licenses_dir / src.name)
                copied.append(f"licenses/{src.name}")
                seen.add(src.name)
    return copied


# ============================================================
# Deterministic timestamps
# ============================================================


def _zip_date_time() -> tuple[int, int, int, int, int, int]:
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", _ZIP_EPOCH_FLOOR))
    epoch = max(epoch, _ZIP_EPOCH_FLOOR)
    t = time.gmtime(epoch)
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def _tar_epoch() -> int:
    return int(os.environ.get("SOURCE_DATE_EPOCH", _ZIP_EPOCH_FLOOR))


# ============================================================
# Wheel assembly
# ============================================================


def _zip_dir(
    staging: Path,
    wheel_path: Path,
    dist_info_name: str,
    wheel_exclude: list[str],
) -> None:
    date_time = _zip_date_time()
    record_lines: list[str] = []

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        files = sorted(p for p in staging.rglob("*") if p.is_file())
        for path in files:
            arcname = str(path.relative_to(staging)).replace(os.sep, "/")
            if any(fnmatch(arcname, pat) for pat in wheel_exclude):
                continue
            content = path.read_bytes()
            digest = (
                "sha256="
                + urlsafe_b64encode(hashlib.sha256(content).digest())
                .rstrip(b"=")
                .decode()
            )
            zinfo = zipfile.ZipInfo(arcname, date_time=date_time)
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zinfo, content)
            record_lines.append(f"{arcname},{digest},{len(content)}")

        record_arc = f"{dist_info_name}/RECORD"
        record_lines.append(f"{record_arc},,")
        zinfo = zipfile.ZipInfo(record_arc, date_time=date_time)
        zinfo.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(zinfo, "\n".join(record_lines) + "\n")


def build_wheel(
    root: Path,
    project: ProjectMetadata,
    backend: BackendConfig,
    *,
    wheel_directory: Path,
    verbose: bool = False,
) -> str:
    name = normalize_name(project.name)
    version = project.version
    platform_tag = host_platform_tag()
    tag = f"py3-none-{platform_tag}"
    wheel_name = f"{name}-{version}-{tag}.whl"

    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir)
        data_dir = staging / f"{name}-{version}.data"
        platlib = data_dir / "platlib"
        pkg_dir = platlib / "mojo_packages"
        lib_dir = pkg_dir / "lib"
        dist_info = staging / f"{name}-{version}.dist-info"
        dist_info.mkdir()

        packages = _resolve_package_dirs(root, backend)
        _compile_all(packages, pkg_dir, backend, verbose=verbose)
        _copy_native_libs(root, lib_dir, backend.native_libs)

        license_relpaths = _copy_license_files(
            root, dist_info, project.license_files
        )
        (dist_info / "METADATA").write_text(
            render_metadata(project, root, license_relpaths)
        )
        (dist_info / "WHEEL").write_text(
            render_wheel_file(
                tag=tag,
                root_is_purelib=False,
                generator_version=GENERATOR_VERSION,
            )
        )

        _zip_dir(staging, wheel_directory / wheel_name, dist_info.name, backend.wheel_exclude)

    return wheel_name


# ============================================================
# Sdist assembly
# ============================================================


_DEFAULT_SDIST_SKIP_TOP = {"dist", "build", "__pycache__", ".venv", ".git", ".tox", ".mypy_cache", ".pytest_cache"}


def _match_any(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch(rel, pat) for pat in patterns)


def _sdist_files(root: Path, cfg: BackendConfig) -> list[Path]:
    if cfg.source_include:
        files: list[Path] = []
        for pattern in cfg.source_include:
            files.extend(p for p in root.glob(pattern) if p.is_file())
    else:
        files = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if rel.parts and rel.parts[0] in _DEFAULT_SDIST_SKIP_TOP:
                continue
            files.append(p)

    if cfg.source_exclude:
        files = [
            p for p in files
            if not _match_any(str(p.relative_to(root)).replace(os.sep, "/"), cfg.source_exclude)
        ]

    # Always include pyproject.toml + readme + license files if they exist.
    extras: list[Path] = []
    for name in ("pyproject.toml",):
        p = root / name
        if p.is_file():
            extras.append(p)
    return sorted(set(files) | set(extras))


def build_sdist(
    root: Path,
    project: ProjectMetadata,
    backend: BackendConfig,
    *,
    sdist_directory: Path,
) -> str:
    name = normalize_name(project.name)
    version = project.version
    sdist_name = f"{name}-{version}.tar.gz"
    sdist_path = sdist_directory / sdist_name

    files = _sdist_files(root, backend)
    epoch = _tar_epoch()

    def _reset(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.mtime = epoch
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tarfile.open(sdist_path, "w:gz") as tar:
        for path in files:
            arc = f"{name}-{version}/{path.relative_to(root)}"
            tar.add(path, arcname=arc, filter=_reset, recursive=False)

    return sdist_name
