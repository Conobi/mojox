"""Wrapper around mojo that auto-injects -I for installed Mojo packages."""
import os
import sys
import sysconfig

# Mojo subcommands that accept -I for import paths. Both `package` (Mojo < 1.0)
# and its `precompile` successor (Mojo >= 1.0) are included so import injection
# works whichever toolchain is installed.
_SUBCOMMANDS = {
    "run", "build", "test", "repl", "doc", "package", "precompile", "format", "debug",
}


def _check() -> None:
    """Validate every configured package by compiling it to a throwaway dir."""
    import tempfile
    from pathlib import Path

    from mojox_build._build import _compile_package, _resolve_package_dirs
    from mojox_build._config import load
    from mojox_build._toolchain import Toolchain

    root = Path.cwd()
    _, backend = load(root / "pyproject.toml")
    packages = _resolve_package_dirs(root, backend)

    # Filter by --package if provided
    filter_name = None
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--package" and i + 1 < len(args):
            filter_name = args[i + 1]
            i += 2
        else:
            i += 1

    if filter_name:
        packages = [p for p in packages if p.name == filter_name]
        if not packages:
            print(f"No package named '{filter_name}' found.", file=sys.stderr)
            sys.exit(1)

    toolchain = Toolchain.detect()
    all_passed = True
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        for pkg in packages:
            try:
                _compile_package(pkg, out_dir, backend, toolchain, verbose=False)
                print(f"{pkg.name}: OK")
            except RuntimeError as e:
                all_passed = False
                print(f"{pkg.name}: FAILED")
                print(str(e), file=sys.stderr)

    sys.exit(0 if all_passed else 1)


def main():
    pkg = sysconfig.get_path("platlib") + "/mojo_packages"
    lib = pkg + "/lib"

    # Set library paths (read by mojo's exec_mojo -> os.execve)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        existing = os.environ.get(var, "")
        os.environ[var] = f"{lib}:{existing}" if existing else lib

    # Intercept 'check' subcommand before exec
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        _check()
        return

    # Inject -I after the subcommand, only for subcommands that accept it
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        sys.argv.insert(2, f"-I{pkg}")

    from mojo._entrypoints import exec_mojo

    exec_mojo()
