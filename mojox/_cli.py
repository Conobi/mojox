"""Wrapper around mojo that auto-injects -I for installed Mojo packages."""
import os
import sys
import sysconfig


def main():
    pkg = sysconfig.get_path("platlib") + "/mojo_packages"
    lib = pkg + "/lib"

    # Set library paths (read by mojo's exec_mojo -> os.execve)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        existing = os.environ.get(var, "")
        os.environ[var] = f"{lib}:{existing}" if existing else lib

    # Inject -I after the subcommand: mojox run file.mojo -> mojo run -I<pkg> file.mojo
    if len(sys.argv) > 1:
        sys.argv.insert(2, f"-I{pkg}")

    from mojo._entrypoints import exec_mojo

    exec_mojo()
