"""Staging area management for bundle mode.

Creates a temporary directory tree that mirrors the test layout with
symlinks for ``.mojo`` files, adds ``__init__.mojo`` stubs where
missing, and writes the generated harness source. The staging area
is separate from the user's source tree.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StagingResult:
    """Result of creating a bundle staging area.

    Attributes:
        harness_path: Absolute path to the generated harness ``.mojo``
            file.
        staging_dir: Root of the staging directory.
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

    Mirrors the test directory tree inside a ``stage/`` subdirectory of
    *staging_dir*, symlinking each ``.mojo`` file listed in
    *test_file_paths*.  Directories that lack an ``__init__.mojo`` get
    an empty one so Mojo treats them as importable packages.

    Args:
        project_root: Absolute path to the project root.
        test_file_paths: Relative paths to test ``.mojo`` files.
        harness_source: Generated Mojo harness source code.
        staging_dir: Where to create the staging area.  Defaults to
            ``project_root / ".mojox" / "bundle"``.

    Returns:
        A :class:`StagingResult` with paths and any diagnostics.
    """
    if staging_dir is None:
        staging_dir = project_root / ".mojox" / "bundle"

    stage_root = staging_dir / "stage"
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

    harness_path = staging_dir / "harness.mojo"
    harness_path.parent.mkdir(parents=True, exist_ok=True)
    harness_path.write_text(harness_source)

    return StagingResult(
        harness_path=harness_path,
        staging_dir=staging_dir,
        include_path=str(stage_root),
        diagnostics=tuple(diagnostics),
    )


def cleanup_staging(staging_dir: Path) -> None:
    """Remove the staging area.

    Safe to call when the directory does not exist.

    Args:
        staging_dir: Root of the staging directory to remove.
    """
    if staging_dir.is_dir():
        shutil.rmtree(staging_dir, ignore_errors=True)
