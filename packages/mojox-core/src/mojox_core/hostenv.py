"""Which host variables reach ``mojo`` and the test binaries it builds.

Pure: names and lexical path rules only. Filesystem questions (does this
directory sit inside the project, does ``cc`` resolve) are answered by
``mojox_core.io.environment``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

#: Forwarded verbatim when set and non-empty. Never ``NIX_CONFIG``,
#: ``NIX_PATH`` or ``NIX_REMOTE``: they can hold access tokens.
FORWARDED_NAMES: frozenset[str] = frozenset(
    {
        "AR",
        "CARGO_HOME",
        "CARGO_TARGET_DIR",
        "CC",
        "CXX",
        "LD",
        "PKG_CONFIG_PATH",
        "RUSTFLAGS",
        "SOURCE_DATE_EPOCH",
        "SSL_CERT_FILE",
        "TMPDIR",
        # The Nix cc-wrapper and bintools-wrapper switches, as a nix-shell sets them.
        "NIX_BINTOOLS",
        "NIX_BUILD_CORES",  # cc-wrapper/stdenv variable, no secrets
        "NIX_CC",
        "NIX_CFLAGS_COMPILE",
        "NIX_ENFORCE_NO_NATIVE",  # cc-wrapper/stdenv variable, no secrets
        "NIX_HARDENING_ENABLE",
        "NIX_LDFLAGS",
        "NIX_SSL_CERT_FILE",
        "NIX_STORE",  # cc-wrapper/stdenv variable, no secrets
    }
)

#: Per-target variants of the wrapper variables (``NIX_LDFLAGS_x86_64_unknown_linux_gnu``).
FORWARDED_PREFIXES: tuple[str, ...] = (
    "NIX_BINTOOLS_WRAPPER_",
    "NIX_CC_WRAPPER_",
    "NIX_CFLAGS_COMPILE_",
    "NIX_LDFLAGS_",
)

_COMPILER_NAMES = frozenset({"CC", "CXX"})


def is_forwarded_name(name: str) -> bool:
    """Whether *name* is on the allowlist (exact, or a prefix plus a non-empty suffix).

    Case-sensitive. ``PATH``, ``HOME`` and the locale are handled elsewhere
    and are deliberately not on the list.
    """
    return name in FORWARDED_NAMES or any(name.startswith(p) and len(name) > len(p) for p in FORWARDED_PREFIXES)


def select_forwarded_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The allowlisted, non-empty entries of *environ*.

    ``CC`` and ``CXX`` must also look like a compiler: an absolute path or a
    bare name. A relative path (``./cc``, ``bin/cc``) is dropped, because it
    would resolve inside the project. Whether a name resolves on the
    constructed PATH is checked by ``read_host_facts``.
    """
    return {
        name: value
        for name, value in environ.items()
        if value and is_forwarded_name(name) and (name not in _COMPILER_NAMES or _is_compiler_shape(value))
    }


def _is_compiler_shape(value: str) -> bool:
    return value.startswith("/") or "/" not in value


_STANDARD_DIRS = ("/usr/local/bin", "/usr/bin", "/bin")


def absolute_path_entries(raw_path: str) -> tuple[str, ...]:
    """The absolute entries of a ``PATH`` string; the first occurrence of a duplicate wins.

    Empty entries (``::``, a leading or trailing ``:``) and relative ones
    (``.``, ``bin``) mean "the current directory" to a shell. They are
    dropped so a binary planted in the project can never shadow ``cc``.
    """
    return tuple(dict.fromkeys(entry for entry in raw_path.split(":") if entry.startswith("/")))


def construct_path(mojo_dir: str | None, entries: Iterable[str]) -> str:
    """``<mojo dir>:<entries>:/usr/local/bin:/usr/bin:/bin``, duplicates removed, first wins.

    *entries* are re-checked here, so a hand-built ``HostFacts`` cannot
    smuggle in a relative entry or a ``:``-joined pair.
    """
    ordered = [*([mojo_dir] if mojo_dir else []), *entries, *_STANDARD_DIRS]
    return ":".join(dict.fromkeys(entry for entry in ordered if entry.startswith("/") and ":" not in entry))
