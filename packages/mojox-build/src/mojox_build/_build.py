"""Compile Mojo packages and assemble wheels / sdists."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
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

from ._config import BackendConfig, BinaryEntry, ProjectMetadata, normalize_name
from ._metadata import render_metadata, render_wheel_file

GENERATOR_VERSION = "0.3.0"

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


def _run_pre_build(
    root: Path,
    commands: list[list[str]],
    *,
    verbose: bool,
) -> None:
    """Run user-defined pre-build hooks before any Mojo compilation.

    Each command runs with cwd=<project root>. Inherits the build env's PATH
    (so build dependencies on PATH are available) and PYTHONPATH (so the build
    env's site-packages is reachable). Any non-zero exit aborts the wheel
    build with the captured stderr.
    """
    for cmd in commands:
        if verbose:
            print(f"[mojox-build] pre-build: {' '.join(cmd)}", file=sys.stderr)
        result = subprocess.run(
            cmd,
            cwd=root,
            capture_output=not verbose,
            text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() if not verbose else "(see above)"
            raise RuntimeError(
                f"pre-build hook failed (exit {result.returncode}): {' '.join(cmd)}\n"
                f"  stderr: {stderr}"
            )


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


def _compile_binary(
    root: Path,
    source: str,
    output: Path,
    cfg: BackendConfig,
    *,
    verbose: bool,
) -> None:
    """Compile one `.mojo` entrypoint into an executable via `mojo build`.

    Mirrors `_compile_mojopkg`'s `-I site-packages/mojo_packages` injection so
    cross-package imports (the project's own runtime deps, installed in the
    build env) resolve.

    Also injects an `$ORIGIN`-relative RUNPATH pointing at the install-site
    `modular/lib`. Without this, `mojo build` only embeds the build-env's
    absolute path, which is an ephemeral PEP 517 temp dir — the binary can
    no longer resolve `libKGENCompilerRTShared.so` etc. once installed.
    """
    src_path = root / source
    cmd = ["mojo", "build", str(src_path), "-o", str(output)]
    pkg_path = sysconfig.get_path("platlib") + "/mojo_packages"
    if os.path.isdir(pkg_path):
        cmd.extend(["-I", pkg_path])
    for key, value in cfg.defines.items():
        cmd.extend(["-D", f"{key}={value}"])

    # PEP 427 install layout: <wheel>.data/scripts/ → <venv>/bin/
    # Inject two `$ORIGIN`-relative RUNPATHs for the venv install layout:
    #   * modular/lib    → Mojo runtime (libKGENCompilerRTShared.so etc.)
    #   * mojo_packages/lib → project + dep native-libs declared via
    #                         [tool.mojox-build].native-libs, dlopened by
    #                         bare soname from Mojo code.
    py_ver = sysconfig.get_python_version()
    site_pkg_rel = f"$ORIGIN/../lib/python{py_ver}/site-packages"
    for sub in ("modular/lib", "mojo_packages/lib"):
        cmd.extend(["-Xlinker", "-rpath", "-Xlinker", f"{site_pkg_rel}/{sub}"])

    cmd.extend(cfg.flags)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"`mojo build` failed for {source}:\n"
            f"  cmd:    {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    if verbose:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")


def _build_binaries(
    root: Path,
    scripts_dir: Path,
    binaries: list[BinaryEntry],
    cfg: BackendConfig,
    *,
    verbose: bool,
) -> None:
    if not binaries:
        return
    scripts_dir.mkdir(parents=True, exist_ok=True)
    tasks = [(b, scripts_dir / b.name) for b in binaries]

    if len(tasks) <= 1:
        for b, out in tasks:
            _compile_binary(root, b.source, out, cfg, verbose=verbose)
            out.chmod(0o755)
        return

    workers = min(len(tasks), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_compile_binary, root, b.source, out, cfg, verbose=verbose): out
            for b, out in tasks
        }
        for f in concurrent.futures.as_completed(futures):
            f.result()
            futures[f].chmod(0o755)


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
        scripts_dir = data_dir / "scripts"
        dist_info = staging / f"{name}-{version}.dist-info"
        dist_info.mkdir()

        _run_pre_build(root, backend.pre_build, verbose=verbose)
        # Validate that pre-build produced the declared native libs (skipped in
        # the initial preflight when pre_build is configured, since they may
        # not exist yet at that point).
        from ._preflight import check_post_pre_build
        check_post_pre_build(root, backend)
        packages = _resolve_package_dirs(root, backend)
        _compile_all(packages, pkg_dir, backend, verbose=verbose)
        _copy_native_libs(root, lib_dir, backend.native_libs)
        _build_binaries(root, scripts_dir, backend.binaries, backend, verbose=verbose)

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


def build_editable_wheel(
    root: Path,
    project: ProjectMetadata,
    backend: BackendConfig,
    *,
    wheel_directory: Path,
    verbose: bool = False,
) -> str:
    """Build an editable wheel that symlinks source dirs at runtime.

    Instead of compiling .mojopkg files, the wheel contains a .pth hook
    that creates symlinks from site-packages/mojo_packages/<pkg> to the
    project's source directories. Source changes are picked up immediately.
    """
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
        scripts_dir = data_dir / "scripts"
        dist_info = staging / f"{name}-{version}.dist-info"
        dist_info.mkdir()

        _run_pre_build(root, backend.pre_build, verbose=verbose)
        from ._preflight import check_post_pre_build
        check_post_pre_build(root, backend)

        packages = _resolve_package_dirs(root, backend)
        platlib.mkdir(parents=True, exist_ok=True)
        _write_editable_hook(root, platlib, packages)

        _copy_native_libs(root, lib_dir, backend.native_libs)
        _build_binaries(root, scripts_dir, backend.binaries, backend, verbose=verbose)

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


def _write_editable_hook(
    root: Path,
    platlib: Path,
    packages: list[Path],
) -> None:
    """Write the .pth, hook module, and manifest for editable installs."""
    hook_template = Path(__file__).parent / "_editable_hook.py"
    shutil.copy2(hook_template, platlib / "_mojox_editable_hook.py")
    (platlib / "_mojox_editable.pth").write_text("import _mojox_editable_hook\n")
    manifest = {
        "packages": {
            pkg.name: str(pkg.resolve()) for pkg in packages
        }
    }
    (platlib / "_mojox_editable_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )


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
