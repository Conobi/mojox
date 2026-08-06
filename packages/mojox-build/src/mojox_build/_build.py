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

from mojox_core import Manifest, Policy, Toolchain

from ._metadata import render_metadata, render_wheel_file

GENERATOR_VERSION = "0.5.0"


def _normalize_name(name: str) -> str:
    """PEP 503 / PEP 491 normalization for wheel filenames."""
    return name.lower().replace("-", "_").replace(".", "_")


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
    commands: tuple[tuple[str, ...], ...],
    *,
    verbose: bool,
) -> None:
    """Run user-defined pre-build hooks before any Mojo compilation.

    Each command runs with cwd=<project root>. Any non-zero exit
    aborts the wheel build with the captured stderr.
    """
    for cmd in commands:
        cmd_list = list(cmd)
        if verbose:
            print(f"[mojox-build] pre-build: {' '.join(cmd_list)}", file=sys.stderr)
        result = subprocess.run(
            cmd_list,
            cwd=root,
            capture_output=not verbose,
            text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() if not verbose else "(see above)"
            raise RuntimeError(
                f"pre-build hook failed (exit {result.returncode}): {' '.join(cmd_list)}\n"
                f"  stderr: {stderr}"
            )


def _compile_package(
    source_dir: Path,
    out_dir: Path,
    policy: Policy,
    toolchain: Toolchain,
    *,
    verbose: bool,
) -> Path:
    """Compile one source dir into a precompiled Mojo package (.mojoc).

    Args:
        source_dir: Directory of ``.mojo`` sources forming one package.
        out_dir: Directory the compiled package is written into.
        policy: Resolved policy supplying ``-D`` defines and extra flags.
        toolchain: Resolved Mojo toolchain.
        verbose: Whether to stream compiler stdout/stderr.

    Returns:
        The path of the compiled package written under *out_dir*.

    Raises:
        RuntimeError: If the compile command exits non-zero.
    """
    output = out_dir / f"{source_dir.name}{toolchain.extension}"
    cmd = [toolchain.mojo_path, toolchain.subcommand, str(source_dir), "-o", str(output)]
    pkg_path = sysconfig.get_path("platlib") + "/mojo_packages"
    if os.path.isdir(pkg_path):
        cmd.extend(["-I", pkg_path])
    # mojo precompile only accepts -o, -I, and diagnostic flags.
    # -D defines and policy flags are elaboration-time concerns that
    # flow through at the consumer's mojo build, not at precompile.
    if toolchain.subcommand != "precompile":
        for key, value in policy.defines.items():
            cmd.extend(["-D", f"{key}={value}"])
        cmd.extend(policy.flags)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"`mojo {toolchain.subcommand}` failed for {source_dir.name}:\n"
            f"  cmd:    {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    if verbose:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
    return output


def _compile_binary(
    root: Path,
    source: str,
    output: Path,
    policy: Policy,
    toolchain: Toolchain,
    *,
    verbose: bool,
) -> None:
    """Compile one ``.mojo`` entrypoint into an executable via ``mojo build``.

    Injects ``-I site-packages/mojo_packages`` and an ``$ORIGIN``-relative
    RUNPATH so cross-package imports and the Mojo runtime resolve after
    install.
    """
    src_path = root / source
    cmd = [toolchain.mojo_path, "build", str(src_path), "-o", str(output)]
    pkg_path = sysconfig.get_path("platlib") + "/mojo_packages"
    if os.path.isdir(pkg_path):
        cmd.extend(["-I", pkg_path])
    for key, value in policy.defines.items():
        cmd.extend(["-D", f"{key}={value}"])

    py_ver = sysconfig.get_python_version()
    site_pkg_rel = f"$ORIGIN/../lib/python{py_ver}/site-packages"
    for sub in ("modular/lib", "mojo_packages/lib"):
        cmd.extend(["-Xlinker", "-rpath", "-Xlinker", f"{site_pkg_rel}/{sub}"])

    cmd.extend(policy.flags)

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
    binaries: tuple,
    policy: Policy,
    toolchain: Toolchain,
    *,
    verbose: bool,
) -> None:
    """Build all declared binary entries concurrently."""
    if not binaries:
        return
    scripts_dir.mkdir(parents=True, exist_ok=True)
    tasks = [(b, scripts_dir / b.name) for b in binaries]

    if len(tasks) <= 1:
        for b, out in tasks:
            _compile_binary(root, b.source, out, policy, toolchain, verbose=verbose)
            out.chmod(0o755)
        return

    workers = min(len(tasks), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_compile_binary, root, b.source, out, policy, toolchain, verbose=verbose): out
            for b, out in tasks
        }
        for f in concurrent.futures.as_completed(futures):
            f.result()
            futures[f].chmod(0o755)


def _resolve_package_dirs(root: Path, manifest: Manifest) -> list[Path]:
    """Resolve the source directories that become compiled packages."""
    if manifest.packages is not None:
        return [root / name for name in manifest.packages]
    pkg_root = root / manifest.package_root
    return [p for p in sorted(pkg_root.iterdir()) if p.is_dir()]


def _compile_all(
    packages: list[Path],
    out_dir: Path,
    policy: Policy,
    toolchain: Toolchain,
    *,
    verbose: bool,
) -> None:
    """Compile all source packages into precompiled .mojoc files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if not packages:
        return

    if len(packages) <= 1:
        for src in packages:
            _compile_package(src, out_dir, policy, toolchain, verbose=verbose)
        return

    workers = min(len(packages), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_compile_package, src, out_dir, policy, toolchain, verbose=verbose)
            for src in packages
        ]
        for f in concurrent.futures.as_completed(futures):
            f.result()


def _copy_native_libs(root: Path, lib_dir: Path, native_libs: tuple[str, ...]) -> None:
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


def _write_provenance(
    dist_info: Path,
    toolchain: Toolchain,
    generator_version: str,
) -> None:
    """Write mojox-provenance.json to the dist-info directory."""
    provenance = {
        "mojo_compiler_version": toolchain.version,
        "mojox_build_version": generator_version,
        "toolchain_surface": f"{toolchain.subcommand}/{toolchain.extension}",
    }
    (dist_info / "mojox-provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )


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
    manifest: Manifest,
    policy: Policy,
    toolchain: Toolchain,
    *,
    wheel_directory: Path,
    verbose: bool = False,
) -> str:
    """Build a wheel containing compiled Mojo packages.

    Args:
        root: Project root directory.
        manifest: Parsed project manifest.
        policy: Resolved policy (defines, flags for the build profile).
        toolchain: Resolved Mojo toolchain.
        wheel_directory: Output directory for the wheel file.
        verbose: Whether to stream compiler output.

    Returns:
        The wheel filename.
    """
    name = _normalize_name(manifest.name)
    version = manifest.version

    has_native = bool(manifest.native_libs) or bool(manifest.binaries)
    if has_native:
        platform_tag = host_platform_tag()
        tag = f"py3-none-{platform_tag}"
    else:
        tag = "py3-none-any"

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

        _run_pre_build(root, manifest.pre_build, verbose=verbose)
        from ._preflight import check_post_pre_build
        check_post_pre_build(root, manifest)
        packages = _resolve_package_dirs(root, manifest)
        _compile_all(packages, pkg_dir, policy, toolchain, verbose=verbose)
        _copy_native_libs(root, lib_dir, manifest.native_libs)
        _build_binaries(root, scripts_dir, manifest.binaries, policy, toolchain, verbose=verbose)

        has_compiled = bool(packages)

        license_relpaths = _copy_license_files(
            root, dist_info, list(manifest.license_files)
        )

        compiler_version = toolchain.version if has_compiled else None
        (dist_info / "METADATA").write_text(
            render_metadata(manifest, root, license_relpaths, compiler_version=compiler_version)
        )
        (dist_info / "WHEEL").write_text(
            render_wheel_file(
                tag=tag,
                root_is_purelib=False,
                generator_version=GENERATOR_VERSION,
            )
        )

        if has_compiled:
            _write_provenance(
                dist_info, toolchain, GENERATOR_VERSION,
            )

        _zip_dir(staging, wheel_directory / wheel_name, dist_info.name, list(manifest.wheel_exclude))

    return wheel_name


def build_editable_wheel(
    root: Path,
    manifest: Manifest,
    policy: Policy,
    toolchain: Toolchain,
    *,
    wheel_directory: Path,
    verbose: bool = False,
) -> str:
    """Build an editable wheel that symlinks source dirs at runtime.

    Instead of compiling packages, the wheel contains a .pth hook
    that creates symlinks from site-packages/mojo_packages/<pkg> to the
    project's source directories. Source changes are picked up immediately.
    """
    name = _normalize_name(manifest.name)
    version = manifest.version

    has_native = bool(manifest.native_libs) or bool(manifest.binaries)
    if has_native:
        platform_tag = host_platform_tag()
        tag = f"py3-none-{platform_tag}"
    else:
        tag = "py3-none-any"

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

        _run_pre_build(root, manifest.pre_build, verbose=verbose)
        from ._preflight import check_post_pre_build
        check_post_pre_build(root, manifest)

        packages = _resolve_package_dirs(root, manifest)
        platlib.mkdir(parents=True, exist_ok=True)
        _write_editable_hook(platlib, packages, name)

        _copy_native_libs(root, lib_dir, manifest.native_libs)
        _build_binaries(root, scripts_dir, manifest.binaries, policy, toolchain, verbose=verbose)

        license_relpaths = _copy_license_files(
            root, dist_info, list(manifest.license_files)
        )
        (dist_info / "METADATA").write_text(
            render_metadata(manifest, root, license_relpaths)
        )
        (dist_info / "WHEEL").write_text(
            render_wheel_file(
                tag=tag,
                root_is_purelib=False,
                generator_version=GENERATOR_VERSION,
            )
        )

        _zip_dir(staging, wheel_directory / wheel_name, dist_info.name, list(manifest.wheel_exclude))

    return wheel_name


def _write_editable_hook(
    platlib: Path,
    packages: list[Path],
    dist_name: str,
) -> None:
    """Write per-distribution .pth, hook module, and manifest for editable installs.

    Files are qualified by *dist_name* so that two mojox projects
    installed editable into the same venv do not clobber each other.
    """
    hook_template = Path(__file__).parent / "_editable_hook.py"
    hook_name = f"_mojox_editable_{dist_name}_hook.py"
    manifest_name = f"_mojox_editable_{dist_name}_manifest.json"
    pth_name = f"_mojox_editable_{dist_name}.pth"

    shutil.copy2(hook_template, platlib / hook_name)
    (platlib / pth_name).write_text(f"import {hook_name[:-3]}\n")
    manifest_data = {
        "packages": {
            pkg.name: str(pkg.resolve()) for pkg in packages
        }
    }
    (platlib / manifest_name).write_text(
        json.dumps(manifest_data, indent=2) + "\n"
    )


# ============================================================
# Sdist assembly
# ============================================================


_DEFAULT_SDIST_SKIP_TOP = {"dist", "build", "__pycache__", ".venv", ".git", ".tox", ".mypy_cache", ".pytest_cache"}


def _match_any(rel: str, patterns: list[str]) -> bool:
    """Check if a relative path matches any of the given patterns."""
    return any(fnmatch(rel, pat) for pat in patterns)


def _is_not_symlink(p: Path) -> bool:
    """Return True if the path is not a symlink (safe to include)."""
    return not p.is_symlink()


def _sdist_files(root: Path, manifest: Manifest) -> list[Path]:
    """Collect files for the sdist, always including README and license files.

    Symlinks are refused on all Python versions to prevent directory-
    escape attacks during sdist assembly.
    """
    if manifest.source_include:
        files: list[Path] = []
        for pattern in manifest.source_include:
            files.extend(
                p for p in root.glob(pattern)
                if p.is_file() and _is_not_symlink(p)
            )
    else:
        files = []
        for p in root.rglob("*"):
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if rel.parts and rel.parts[0] in _DEFAULT_SDIST_SKIP_TOP:
                continue
            files.append(p)

    if manifest.source_exclude:
        files = [
            p for p in files
            if not _match_any(str(p.relative_to(root)).replace(os.sep, "/"), list(manifest.source_exclude))
        ]

    # Always include pyproject.toml, README, and license files.
    extras: list[Path] = []
    always_include = ["pyproject.toml"]
    if manifest.readme:
        always_include.append(manifest.readme)
    extras = [root / name for name in always_include if (root / name).is_file() and _is_not_symlink(root / name)]
    for pattern in manifest.license_files:
        for lic in sorted(root.glob(pattern)):
            if lic.is_file() and _is_not_symlink(lic):
                extras.append(lic)

    return sorted(set(files) | set(extras))


def build_sdist(
    root: Path,
    manifest: Manifest,
    *,
    sdist_directory: Path,
) -> str:
    """Build a source distribution (.tar.gz)."""
    name = _normalize_name(manifest.name)
    version = manifest.version
    sdist_name = f"{name}-{version}.tar.gz"
    sdist_path = sdist_directory / sdist_name

    files = _sdist_files(root, manifest)
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
