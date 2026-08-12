"""All frozen dataclasses for the mojox-core model layer.

Every type here is immutable (frozen=True). No type in this module imports
subprocess or performs I/O. Path-valued fields use PurePosixPath for lexical
paths (manifest-relative) and str for resolved absolute paths (from readers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import ClassVar, Literal, Mapping


# -- Enums -----------------------------------------------------------------


class CommandKind(Enum):
    """The kind of mojo invocation a Command represents."""

    COMPILE_PACKAGE = "compile-package"
    COMPILE_BINARY = "compile-binary"
    RUN_TEST = "run-test"
    CHECK_EXAMPLE = "check-example"
    RUN = "run"


class TargetKind(Enum):
    """Discovered target kinds."""

    LIB = "lib"
    BIN = "bin"
    TEST = "test"
    EXAMPLE = "example"


class DistKind(Enum):
    """How a distribution provides its Mojo packages."""

    SOURCE = "source"
    PRECOMPILED = "precompiled"


# -- Leaf types ------------------------------------------------------------


@dataclass(frozen=True)
class BinaryEntry:
    """One executable produced by ``mojo build``."""

    source: str
    name: str


@dataclass(frozen=True)
class LintConfig:
    """Lint settings that translate to compiler flags."""

    warnings_as_errors: bool = False
    check_doc_strings: bool = False
    missing_doc_strings: bool = False
    unstable_apis: bool = False


@dataclass(frozen=True)
class Diagnostic:
    """A compiler or mojox diagnostic."""

    kind: Literal["error", "warning", "note"]
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    source_text: str | None = None


@dataclass(frozen=True)
class Target:
    """A discovered build target."""

    kind: TargetKind
    path: str
    target_id: str


@dataclass(frozen=True)
class DistEntry:
    """One installed distribution contributing to the include sequence."""

    name: str
    include_dir: str
    kind: DistKind
    packages: tuple[str, ...]
    provenance: str
    native_lib_dirs: tuple[str, ...] = ()


# -- Compound types --------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """A named compilation profile (dev, release, or user-declared)."""

    optimize: int | None = None
    debug_level: str | None = None
    defines: dict[str, str] = field(default_factory=dict)
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    """Parsed pyproject.toml -- portable, published, versioned with the code."""

    # [project]
    name: str
    version: str
    description: str | None
    readme: str | None
    license_expr: str | None
    license_files: tuple[str, ...]
    requires_python: str | None
    dependencies: tuple[str, ...]
    optional_dependencies: dict[str, tuple[str, ...]]
    keywords: tuple[str, ...]
    authors: tuple[dict[str, str], ...]
    maintainers: tuple[dict[str, str], ...]
    urls: dict[str, str]
    classifiers: tuple[str, ...]

    # [tool.mojox]
    packages: tuple[str, ...] | None
    package_root: str
    binaries: tuple[BinaryEntry, ...]
    test_roots: tuple[str, ...]
    test_parallel: bool
    defines: dict[str, str]
    flags: tuple[str, ...]
    lints: LintConfig
    optimize: int | None
    debug_level: str | None
    pre_build: tuple[tuple[str, ...], ...]
    native_libs: tuple[str, ...]
    source_include: tuple[str, ...] | None
    source_exclude: tuple[str, ...]
    wheel_exclude: tuple[str, ...]
    profiles: dict[str, Profile]
    ore_seed: str | None
    build_profile: str


@dataclass(frozen=True)
class TargetGraph:
    """Discovered targets in topological order (lib targets first)."""

    targets: tuple[Target, ...]
    edges: tuple[tuple[str, str], ...]
    unsearched_test_dirs: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ResolvedEnv:
    """The resolved build environment -- include sequence, mojo binary, provenance."""

    include_sequence: tuple[DistEntry, ...]
    mojo_path: str
    mojo_version: str
    path_mojo: str | None
    lock_version: int | None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class Toolchain:
    """Resolved Mojo toolchain identity."""

    mojo_path: str
    version: str
    subcommand: str
    extension: str


@dataclass(frozen=True)
class HostFacts:
    """Machine-specific facts injected into the planner for determinism."""

    cpu_count: int
    available_memory_mb: int
    manifest_dir: PurePosixPath


@dataclass(frozen=True)
class LocalSettings:
    """Machine-local settings (from .mojox/config.toml + env vars)."""

    jobs: int | None = None
    timeout_s: int | None = None
    config_paths: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)

    EMPTY: ClassVar[LocalSettings]  # set below


# Bootstrap the EMPTY sentinel after the class is defined.
LocalSettings.EMPTY = LocalSettings()  # type: ignore[attr-defined]


@dataclass(frozen=True)
class Policy:
    """The fully resolved flag set the planner consumes."""

    optimize: int
    debug_level: str
    defines: dict[str, str]
    flags: tuple[str, ...]
    include_paths: tuple[str, ...]
    lints: LintConfig
    jobs: int
    jobs_compile: int
    jobs_tests: int
    timeout_s: int


@dataclass(frozen=True)
class Command:
    """One mojo invocation the planner produces."""

    argv: tuple[str, ...]
    cwd: PurePosixPath
    env: Mapping[str, str]
    kind: CommandKind
    target_id: str
    timeout_s: int | None
    outputs: tuple[str, ...]
    depends_on: tuple[str, ...]
