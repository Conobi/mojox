"""Staging area management for bundle mode.

Creates a temporary directory tree that mirrors the test layout with
symlinks for ``.mojo`` files, adds ``__init__.mojo`` stubs where
missing, and writes the generated harness source. The staging area
is separate from the user's source tree.

Each staging tree lives in a directory named after the hash of its content,
so runs that need different harnesses never share one, and runs that need
the same one share a stable path (which keeps the harness binary cached).
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StagingResult:
    """Result of creating a bundle staging area.

    Attributes:
        harness_path: Absolute path to the generated harness ``.mojo``
            file.
        staging_dir: Root of this content-addressed staging directory.
        include_path: Path to add to ``-I`` for Mojo module resolution
            of test packages.
        diagnostics: Warning messages for excluded or problematic files.
    """

    harness_path: Path
    staging_dir: Path
    include_path: str
    diagnostics: tuple[str, ...]


def create_bundle_staging(
    project_root: Path,
    test_file_paths: list[str],
    harness_source: str,
    staging_dir: Path | None = None,
) -> StagingResult:
    """Create a staging area with symlinked test files and init stubs.

    Mirrors the test directory tree inside a ``stage/`` subdirectory of a
    ``<hash>`` directory under *staging_dir*, symlinking each ``.mojo``
    file listed in *test_file_paths*.  Directories that lack an
    ``__init__.mojo`` get an empty one so Mojo treats them as importable
    packages.

    Args:
        project_root: Absolute path to the project root.
        test_file_paths: Relative paths to test ``.mojo`` files.
        harness_source: Generated Mojo harness source code.
        staging_dir: Parent of the content-addressed staging directories.
            Defaults to ``project_root / ".mojox" / "bundle"``.

    Returns:
        A :class:`StagingResult` with paths and any diagnostics. The tree is
        built privately and renamed into place only if no identical one
        exists, so concurrent callers never observe a partial tree and
        never modify another's. It is never removed by a run: another run
        may be compiling against it.
    """
    if staging_dir is None:
        staging_dir = project_root / ".mojox" / "bundle"

    staging_dir.mkdir(parents=True, exist_ok=True)
    build_dir = Path(tempfile.mkdtemp(dir=staging_dir, prefix=".build."))
    try:
        return _publish(build_dir, _build_tree(project_root, test_file_paths, harness_source, build_dir), staging_dir)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def _build_tree(
    project_root: Path, test_file_paths: list[str], harness_source: str, build_dir: Path
) -> tuple[str, ...]:
    """Fill *build_dir* with ``stage/`` and ``harness.mojo``; return the diagnostics."""
    stage_root = build_dir / "stage"
    diagnostics: list[str] = []

    dirs_seen: set[str] = set()

    for rel_path in test_file_paths:
        src = project_root / rel_path
        dest = stage_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists() or dest.is_symlink():
            dest.unlink()
        os.symlink(src.resolve(), dest)

        parts = Path(rel_path).parent.parts
        for i in range(len(parts)):
            dir_rel = str(Path(*parts[: i + 1]))
            dirs_seen.add(dir_rel)

    for dir_rel in sorted(dirs_seen):
        original_dir = project_root / dir_rel
        staged_dir = stage_root / dir_rel
        staged_dir.mkdir(parents=True, exist_ok=True)

        for mojo_file in sorted(original_dir.glob("*.mojo")):
            dest = staged_dir / mojo_file.name
            if dest.exists() or dest.is_symlink():
                continue
            os.symlink(mojo_file.resolve(), dest)

        if not (staged_dir / "__init__.mojo").exists():
            (staged_dir / "__init__.mojo").write_text("")

    (build_dir / "harness.mojo").write_text(harness_source)
    return tuple(diagnostics)


def _tree_digest(root: Path) -> str:
    """Hash every entry of *root*: names, symlink targets and file contents."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            body = os.readlink(path) if path.is_symlink() else path.read_text()
            h.update(f"{path.relative_to(root)}\0{body}\0".encode())
    return h.hexdigest()[:16]


def _publish(build_dir: Path, diagnostics: tuple[str, ...], parent: Path) -> StagingResult:
    """Rename *build_dir* to ``parent/<digest>``, unless an identical tree is already there."""
    final = parent / _tree_digest(build_dir)
    try:
        os.rename(build_dir, final)
    except OSError as e:
        # A non-empty target means an identical tree won the race: use it.
        if e.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            raise
    return StagingResult(
        harness_path=final / "harness.mojo",
        staging_dir=final,
        include_path=str(final / "stage"),
        diagnostics=diagnostics,
    )
