"""Discover build targets from a Manifest and source tree.

Discovery is root-relative, never repo-recursive. Excluded directories
are never descended into. Test targets use the test_ prefix convention.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ConfigError
from .types import Manifest, Target, TargetGraph, TargetKind

_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".claude",
        ".worktrees",
        ".venv",
        "venv",
        "build",
        "dist",
        "site-packages",
        "mojo_packages",
        "target",
    }
)

_EGG_INFO_SUFFIX = ".egg-info"


def discover(manifest: Manifest, root: Path) -> TargetGraph:
    """Discover all targets from the manifest and source tree.

    Walks the source tree rooted at *root*, using *manifest* to determine
    which directories are test roots, which files are binary entry points,
    and where library packages live.  Returns a ``TargetGraph`` containing
    every discovered ``Target`` plus advisory ``unsearched_test_dirs``.

    Raises:
        ConfigError: If a configured test root does not exist, or if
            configured test roots contain no ``test_*.mojo`` files.
    """
    targets: list[Target] = []
    binary_sources = {b.source for b in manifest.binaries}

    # Lib targets
    if manifest.packages is not None:
        for pkg in manifest.packages:
            pkg_path = root / pkg
            if pkg_path.is_dir():
                targets.append(
                    Target(
                        kind=TargetKind.LIB,
                        path=pkg,
                        target_id=pkg,
                    )
                )
    else:
        pkg_root = root / manifest.package_root
        if pkg_root.is_dir():
            for child in sorted(pkg_root.iterdir()):
                if child.is_dir() and not child.name.startswith(".") and not _is_excluded(child.name):
                    rel = str(child.relative_to(root))
                    targets.append(
                        Target(
                            kind=TargetKind.LIB,
                            path=rel,
                            target_id=rel,
                        )
                    )

    # Binary targets
    for b in manifest.binaries:
        targets.append(
            Target(
                kind=TargetKind.BIN,
                path=b.source,
                target_id=b.name,
            )
        )

    # Test targets
    unsearched: list[tuple[str, int]] = []
    if manifest.test_roots:
        test_files: list[str] = []
        for test_root in manifest.test_roots:
            tr_path = root / test_root
            if not tr_path.exists():
                raise ConfigError(
                    "tool.mojox.test-roots",
                    f"test root {test_root!r} does not exist",
                )
            if tr_path.is_dir():
                _walk_test_dir(root, tr_path, test_files)

        if not test_files and manifest.test_roots:
            roots_str = ", ".join(repr(r) for r in manifest.test_roots)
            raise ConfigError(
                "tool.mojox.test-roots",
                f"no test targets found in configured roots: {roots_str}. Test files must be named test_*.mojo.",
            )

        for tf in test_files:
            targets.append(
                Target(
                    kind=TargetKind.TEST,
                    path=tf,
                    target_id=tf,
                )
            )

        # Check for unsearched directories containing test files
        searched = set(manifest.test_roots)
        unsearched = _find_unsearched_test_dirs(root, searched)

    # Example targets
    examples_dir = root / "examples"
    if examples_dir.is_dir():
        for child in sorted(examples_dir.iterdir()):
            if child.suffix == ".mojo" and child.is_file():
                rel = str(child.relative_to(root))
                if rel not in binary_sources:
                    targets.append(
                        Target(
                            kind=TargetKind.EXAMPLE,
                            path=rel,
                            target_id=rel,
                        )
                    )
            elif child.is_dir() and not _is_excluded(child.name):
                main = child / "main.mojo"
                if main.is_file():
                    rel = str(main.relative_to(root))
                    if rel not in binary_sources:
                        targets.append(
                            Target(
                                kind=TargetKind.EXAMPLE,
                                path=rel,
                                target_id=rel,
                            )
                        )

    return TargetGraph(
        targets=tuple(targets),
        edges=(),
        unsearched_test_dirs=tuple(unsearched),
    )


def _is_excluded(name: str) -> bool:
    """Check if a directory name should be excluded from discovery."""
    if name in _EXCLUDED_DIRS:
        return True
    return bool(name.endswith(_EGG_INFO_SUFFIX))


def _walk_test_dir(root: Path, directory: Path, out: list[str]) -> None:
    """Walk a test directory, collecting test_*.mojo files.

    Recursively descends into subdirectories, skipping excluded directories
    and directories containing a ``pyproject.toml`` (nested project boundary).
    """
    for child in sorted(directory.iterdir()):
        if child.is_dir():
            if _is_excluded(child.name):
                continue
            if (child / "pyproject.toml").exists():
                continue
            _walk_test_dir(root, child, out)
        elif child.is_file() and child.name.startswith("test_") and child.suffix == ".mojo":
            out.append(str(child.relative_to(root)))


def _find_unsearched_test_dirs(root: Path, searched: set[str]) -> list[tuple[str, int]]:
    """Find top-level directories not in *searched* that contain test_*.mojo files.

    Returns a list of ``(dir_name, count)`` tuples for directories that were
    not part of the configured test roots but do contain test files, serving
    as an advisory warning to the user.
    """
    unsearched: list[tuple[str, int]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in searched:
            continue
        if _is_excluded(child.name):
            continue
        if child.name.startswith("."):
            continue
        count = sum(1 for _ in child.rglob("test_*.mojo"))
        if count > 0:
            unsearched.append((child.name, count))
    return unsearched
