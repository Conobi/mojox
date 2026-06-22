"""Detect which Mojo toolchain is on PATH and what packaging surface it exposes.

Mojo 1.0 renamed the package-compilation command and its output extension:

* Mojo < 1.0  → ``mojo package``    emitting ``.mojopkg``
* Mojo >= 1.0 → ``mojo precompile`` emitting ``.mojoc``

mojox deliberately does not pin the Mojo version, so the build backend must
work against either toolchain. This module probes ``mojo --version`` once and
resolves the correct command + extension; everything downstream branches on the
resulting :class:`Toolchain`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import lru_cache

from packaging.version import InvalidVersion, Version


def parse_mojo_version(output: str) -> tuple[int, ...]:
    """Return the PEP 440 release tuple from ``mojo --version`` output.

    Scans whitespace-delimited tokens and returns the release segment of the
    first token that parses as a valid version. This tolerates the surrounding
    ``Mojo`` prefix and the trailing parenthesised git hash, e.g.::

        "Mojo 0.26.3.0.dev2026042005 (32e188d3)" -> (0, 26, 3, 0)
        "Mojo 1.0.0b2 (abc1234)"                 -> (1, 0, 0)

    Args:
        output: Raw text emitted by ``mojo --version``.

    Returns:
        The release component of the parsed version (pre-release/dev suffixes
        are dropped — only the numeric release matters for toolchain selection).

    Raises:
        ValueError: If no token parses as a valid version.
    """
    for token in output.split():
        try:
            return Version(token).release
        except InvalidVersion:
            continue
    raise ValueError(f"could not parse a Mojo version from: {output!r}")


@lru_cache(maxsize=1)
def detect_mojo_release() -> tuple[int, ...]:
    """Run ``mojo --version`` and return its release tuple (cached per process).

    Returns:
        The release tuple of the ``mojo`` binary found on PATH.

    Raises:
        RuntimeError: If ``mojo --version`` exits non-zero.
        ValueError: If the output cannot be parsed (propagated from
            :func:`parse_mojo_version`).
    """
    result = subprocess.run(
        ["mojo", "--version"], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`mojo --version` failed (exit {result.returncode}). stderr:\n"
            f"  {result.stderr.strip()}"
        )
    # Some builds print the banner to stdout, others to stderr — try both.
    return parse_mojo_version(result.stdout or result.stderr)


@dataclass(frozen=True)
class Toolchain:
    """The package-compilation surface of a specific Mojo toolchain.

    Attributes:
        subcommand: ``mojo`` subcommand that compiles a source dir into a
            precompiled package (``"package"`` or ``"precompile"``).
        extension: File extension of the emitted package (``".mojopkg"`` or
            ``".mojoc"``).
    """

    subcommand: str
    extension: str

    @classmethod
    def from_release(cls, release: tuple[int, ...]) -> "Toolchain":
        """Select the toolchain surface for a given Mojo release tuple.

        Mojo 1.0 is the cutover: any major version >= 1 uses the
        ``precompile`` / ``.mojoc`` surface, everything below stays on the
        legacy ``package`` / ``.mojopkg`` surface.

        Args:
            release: A PEP 440 release tuple, e.g. ``(0, 26, 3, 0)`` or
                ``(1, 0, 0)``.

        Returns:
            The matching :class:`Toolchain`.
        """
        if release and release[0] >= 1:
            return cls(subcommand="precompile", extension=".mojoc")
        return cls(subcommand="package", extension=".mojopkg")

    @classmethod
    def detect(cls) -> "Toolchain":
        """Resolve the toolchain from the ``mojo`` binary on PATH.

        Returns:
            The :class:`Toolchain` matching the locally installed Mojo.
        """
        return cls.from_release(detect_mojo_release())
