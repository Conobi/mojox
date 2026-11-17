"""Print the toolchain facts mojox would plan with, for shell self-checks.

``python -m mojox_core.io.hostcheck [--root DIR]`` prints one ``key=value``
line each for ``mojo`` (the compiler binary), ``mojo_version``, ``cc`` (the
real path of the ``cc`` on the PATH mojox constructs; empty when there is
none), ``path`` (that PATH) and ``mojox`` (the installed ``mojox`` version;
empty when it is not installed), in that order. The Nix shell hook compares
them with ``/nix/store``, so it never reimplements the PATH rule.

Exit status 2 and a message on stderr when no Mojo 1.x compiler is installed
or the host facts cannot be read.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from ..errors import ConfigError
from ..plan import _construct_env
from .environment import read_host_facts
from .toolchain import resolve as resolve_toolchain


def _installed_version(distribution: str) -> str:
    """Version of an installed distribution, or ``""`` when it is not installed."""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return ""


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run the check; *environ* and *stdout* default to the process's, for tests."""
    parser = argparse.ArgumentParser(
        prog="python -m mojox_core.io.hostcheck", description="Print the toolchain facts mojox would plan with."
    )
    parser.add_argument("--root", default=".", help="project root; PATH entries inside it are ignored")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    out = sys.stdout if stdout is None else stdout

    try:
        toolchain = resolve_toolchain()
        host = read_host_facts(Path(args.root), environ, mojo_path=toolchain.mojo_path)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2

    print(f"mojo={toolchain.mojo_path}", file=out)
    print(f"mojo_version={toolchain.version}", file=out)
    print(f"cc={host.cc_realpath}", file=out)
    print(f"path={_construct_env(toolchain, host)['PATH']}", file=out)
    print(f"mojox={_installed_version('mojox')}", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
