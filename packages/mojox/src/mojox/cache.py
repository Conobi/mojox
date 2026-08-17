"""Cache key computation and tree hashing for AOT test binaries.

Deterministic content hashing of source trees and cache metadata
management. The cache key captures everything that could change the
compiled output: source content, project structure, compiler version,
and build flags.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


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


def compute_cache_key(
    *,
    test_source: Path,
    project_hash: str,
    tests_tree_hash: str,
    compiler_version: str,
    flags: tuple[str, ...],
) -> str:
    """Build a composite SHA-256 cache key from all compilation inputs.

    The key changes whenever any input changes, ensuring stale binaries
    are never reused.

    Args:
        test_source: Path to the individual test ``.mojo`` file whose
            content is hashed.
        project_hash: Precomputed hash of the project source tree.
        tests_tree_hash: Precomputed hash of the tests directory tree.
        compiler_version: Mojo compiler version string.
        flags: Compiler flags passed during AOT compilation.

    Returns:
        A 64-character lowercase hex digest.
    """
    h = hashlib.sha256()
    h.update(test_source.read_bytes())
    h.update(project_hash.encode())
    h.update(tests_tree_hash.encode())
    h.update(compiler_version.encode())
    for flag in flags:
        h.update(flag.encode())
    return h.hexdigest()


def read_cache_meta(meta_path: Path) -> str | None:
    """Read cache metadata and return the stored cache key.

    Returns ``None`` when the file is missing, contains invalid JSON,
    or has an unexpected ``schema_version``.

    Args:
        meta_path: Path to the JSON metadata file.

    Returns:
        The ``cache_key`` string, or ``None`` if unavailable.
    """
    try:
        data = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
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

    The file is first written to a temporary sibling (``.<pid>.tmp``),
    then renamed into place so readers never see a partial write.
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

    tmp_path = meta_path.parent / f".{meta_path.name}.{os.getpid()}.tmp"
    try:
        tmp_path.write_text(json.dumps(payload))
        os.rename(tmp_path, meta_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
