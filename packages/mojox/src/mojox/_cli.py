"""Wrapper around mojo that auto-injects -I for installed Mojo packages."""
import os
import sys
import sysconfig

# Mojo subcommands that accept -I for import paths
_SUBCOMMANDS = {"run", "build", "test", "repl", "doc", "package", "format", "debug"}


def _check(pkg_path: str) -> None:
    """Run `mojo package` in validation-only mode."""
    import tempfile
    from pathlib import Path

    from mojox_build._build import _compile_mojopkg
    from mojox_build._config import load

    root = Path.cwd()
    _, backend = load(root / "pyproject.toml")

    if backend.packages is not None:
        packages = [root / name for name in backend.packages]
    else:
        pkg_root = root / backend.package_root
        packages = [p for p in sorted(pkg_root.iterdir()) if p.is_dir()]

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

    all_passed = True
    with tempfile.TemporaryDirectory() as tmpdir:
        for pkg in packages:
            out = Path(tmpdir) / f"{pkg.name}.mojopkg"
            try:
                _compile_mojopkg(pkg, out, backend, verbose=False)
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
        _check(pkg)
        return

    # Inject -I after the subcommand, only for subcommands that accept it
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        sys.argv.insert(2, f"-I{pkg}")

    from mojo._entrypoints import exec_mojo

    exec_mojo()
