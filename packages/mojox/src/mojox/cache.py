"""Cache key computation and tree hashing for AOT test binaries.

Deterministic hashing of source trees, stat stamps of dependency include
dirs, and cache metadata management. The cache key captures everything
that could change the compiled output: test and project sources,
dependency files, compiler identity, build flags and build environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

# Files the Mojo compiler can resolve an import to: source modules
# (both spellings) and precompiled packages, as recognised by
# mojox_core.io.environment.read_distributions.
MOJO_IMPORT_SUFFIXES = (".mojo", ".\U0001f525", ".mojopkg", ".mojoc")


def hash_directory_tree(directory: Path) -> str:
    """Compute a SHA-256 hash over all ``.mojo`` files in a directory tree.

    Files are sorted by their relative path to guarantee deterministic
    output regardless of filesystem enumeration order.  Non-``.mojo``
    files are silently ignored.

    Args:
        directory: Root directory to scan.  If it does not exist, the
            hash of empty input is returned.

    Returns:
        A 64-character lowercase hex digest.
    """
    h = hashlib.sha256()

    if directory.is_dir():
        mojo_files = sorted(directory.rglob("*.mojo"))
        for path in mojo_files:
            rel = path.relative_to(directory)
            h.update(str(rel).encode())
            h.update(path.read_bytes())

    return h.hexdigest()


def stamp_include_dirs(include_dirs: Iterable[str]) -> str:
    """Fingerprint the importable Mojo files under each include dir, in order.

    Hashes ``(relative path, st_size, st_mtime_ns)`` of every file ending in
    :data:`MOJO_IMPORT_SUFFIXES`, never file contents, so a whole
    site-packages Mojo dir stamps in milliseconds. The dir order is kept
    because the compiler resolves imports first-match-wins. A missing or
    unreadable dir is recorded as such, so it stamps deterministically and
    its later appearance changes the stamp.

    Symlinks are followed, including out of the include dir: editable
    installs populate ``mojo_packages/<pkg>`` with symlinks to the project's
    source tree, and those sources are exactly the ones edited in place.
    Each directory is visited at most once (by device and inode), which cuts
    symlink loops. Dot-prefixed entries are skipped: they cannot be
    imported.

    Limits: an edit that preserves both size and mtime (at the filesystem's
    timestamp granularity) is not detected.
    """
    h = hashlib.sha256()
    for include_dir in include_dirs:
        _stamp_record(h, "dir", include_dir)
        _stamp_tree(h, include_dir)
    return h.hexdigest()


def _stamp_record(h: hashlib._Hash, *fields: object) -> None:
    """Feed one self-delimiting record into *h* (JSON escapes any byte in a name)."""
    h.update(json.dumps(fields).encode())
    h.update(b"\n")


def _stamp_tree(h: hashlib._Hash, root: str) -> None:
    """Stamp every importable file below *root*, walking entries in name order."""
    visited: set[tuple[int, int]] = set()

    def walk(path: str, rel: str) -> None:
        try:
            st = os.stat(path)
            ident = (st.st_dev, st.st_ino)
            if ident in visited:
                return
            visited.add(ident)
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as e:
            _stamp_record(h, "unreadable", rel, type(e).__name__)
            return

        for entry in entries:
            if entry.name.startswith("."):
                continue
            child = f"{rel}/{entry.name}" if rel else entry.name
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                walk(entry.path, child)
            elif entry.name.endswith(MOJO_IMPORT_SUFFIXES):
                try:
                    fst = entry.stat()
                except OSError:
                    _stamp_record(h, "dangling", child)
                else:
                    _stamp_record(h, "file", child, fst.st_size, fst.st_mtime_ns)

    walk(root, "")


def compute_cache_key(
    *,
    test_source: Path,
    project_hash: str,
    tests_tree_hash: str,
    deps_stamp: str,
    compiler_version: str,
    mojo_path: str,
    flags: tuple[str, ...],
    env: Mapping[str, str],
) -> str:
    """Build a composite SHA-256 cache key from all compilation inputs.

    The key changes whenever any input changes, ensuring stale binaries
    are never reused. *flags* are hashed in order (``-I`` order decides
    which package wins); *env* is order-insensitive. Inputs are encoded
    as one JSON document so no two distinct inputs share a byte stream.

    Args:
        test_source: The test ``.mojo`` file; its content is hashed.
        project_hash: Hash of the project's library source trees.
        tests_tree_hash: Hash of the tests directory trees.
        deps_stamp: :func:`stamp_include_dirs` of the dependency dirs.
        compiler_version: Mojo compiler version string.
        mojo_path: The compiler binary the build invokes.
        flags: Build argv after the output path, in argv order.
        env: Environment the build runs with.
    """
    h = hashlib.sha256()
    h.update(test_source.read_bytes())
    h.update(b"\0")
    h.update(
        json.dumps(
            {
                "project_hash": project_hash,
                "tests_tree_hash": tests_tree_hash,
                "deps_stamp": deps_stamp,
                "compiler_version": compiler_version,
                "mojo_path": mojo_path,
                "flags": list(flags),
                "env": sorted(env.items()),
            },
            sort_keys=True,
        ).encode()
    )
    return h.hexdigest()


def read_cache_meta(meta_path: Path) -> str | None:
    """Return the stored cache key, or ``None`` to signal a cache miss.

    Any unreadable, non-UTF-8, truncated or schema-mismatched file is a
    miss rather than an error: a damaged meta only costs a rebuild.
    """
    try:
        data = json.loads(meta_path.read_bytes())
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != 1:
        return None

    key = data.get("cache_key")
    if not isinstance(key, str):
        return None
    return key


def write_cache_meta(
    meta_path: Path,
    *,
    cache_key: str,
    compiler_version: str,
) -> None:
    """Write cache metadata atomically.

    The payload goes to a unique ``mkstemp`` sibling, then ``os.replace``
    moves it into place, so readers never see a partial write and
    concurrent writers (threads share a pid) never share a temp file.
    Parent directories are created if needed.

    Args:
        meta_path: Destination path for the JSON metadata file.
        cache_key: The composite cache key to store.
        compiler_version: Mojo compiler version used for the build.
    """
    meta_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": 1,
        "cache_key": cache_key,
        "compiler_version": compiler_version,
        "built_at_epoch": time.time(),
    }

    fd, tmp_name = tempfile.mkstemp(dir=meta_path.parent, prefix=f".{meta_path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp_name, meta_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
