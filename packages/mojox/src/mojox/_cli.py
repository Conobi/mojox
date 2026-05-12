"""Wrapper around mojo that auto-injects -I for installed Mojo packages."""
import os
import sys
import sysconfig

# Mojo subcommands that accept -I for import paths
_SUBCOMMANDS = {"run", "build", "test", "repl", "doc", "package", "format", "debug"}


def main():
    pkg = sysconfig.get_path("platlib") + "/mojo_packages"
    lib = pkg + "/lib"

    # Set library paths (read by mojo's exec_mojo -> os.execve)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        existing = os.environ.get(var, "")
        os.environ[var] = f"{lib}:{existing}" if existing else lib

    # Inject -I after the subcommand, only for subcommands that accept it
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        sys.argv.insert(2, f"-I{pkg}")

    from mojo._entrypoints import exec_mojo

    exec_mojo()
