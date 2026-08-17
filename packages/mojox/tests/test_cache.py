"""Cache key computation and tree hashing tests."""

from __future__ import annotations

import json
from pathlib import Path

from mojox.cache import (
    compute_cache_key,
    hash_directory_tree,
    read_cache_meta,
    write_cache_meta,
)


class TestHashDirectoryTree:
    def test_empty_dir_returns_hex(self, tmp_path: Path):
        """An empty directory produces a valid 64-char hex digest."""
        result = hash_directory_tree(tmp_path)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_content_same_hash(self, tmp_path: Path):
        """Identical file content produces identical hashes."""
        (tmp_path / "a.mojo").write_text("fn main(): pass")
        h1 = hash_directory_tree(tmp_path)

        other = tmp_path / "other"
        other.mkdir()
        (other / "a.mojo").write_text("fn main(): pass")
        h2 = hash_directory_tree(other)

        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path: Path):
        """Different file content produces different hashes."""
        (tmp_path / "a.mojo").write_text("fn main(): pass")
        h1 = hash_directory_tree(tmp_path)

        (tmp_path / "a.mojo").write_text("fn main(): return 1")
        h2 = hash_directory_tree(tmp_path)

        assert h1 != h2

    def test_ignores_non_mojo_files(self, tmp_path: Path):
        """Non-.mojo files do not affect the hash."""
        (tmp_path / "a.mojo").write_text("fn main(): pass")
        h1 = hash_directory_tree(tmp_path)

        (tmp_path / "README.md").write_text("docs")
        (tmp_path / "config.toml").write_text("[build]")
        h2 = hash_directory_tree(tmp_path)

        assert h1 == h2

    def test_nested_dirs_included(self, tmp_path: Path):
        """Files in subdirectories are included in the hash."""
        sub = tmp_path / "nested" / "deep"
        sub.mkdir(parents=True)
        (sub / "inner.mojo").write_text("fn helper(): pass")
        h1 = hash_directory_tree(tmp_path)

        # Hash should differ from an empty directory.
        empty = tmp_path / "empty_dir"
        empty.mkdir()
        h2 = hash_directory_tree(empty)

        assert h1 != h2

    def test_nonexistent_dir_returns_empty_hash(self, tmp_path: Path):
        """A nonexistent directory returns the hash of empty input."""
        missing = tmp_path / "does_not_exist"
        result = hash_directory_tree(missing)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestComputeCacheKey:
    def test_same_inputs_same_key(self, tmp_path: Path):
        """Identical inputs produce identical cache keys."""
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")

        k1 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            compiler_version="25.4.0",
            flags=("-O2",),
        )
        k2 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            compiler_version="25.4.0",
            flags=("-O2",),
        )
        assert k1 == k2

    def test_source_change_different_key(self, tmp_path: Path):
        """Changing the test source file produces a different key."""
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")
        k1 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            compiler_version="25.4.0",
            flags=(),
        )

        src.write_text("fn main(): return 1")
        k2 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            compiler_version="25.4.0",
            flags=(),
        )
        assert k1 != k2

    def test_project_hash_change_different_key(self, tmp_path: Path):
        """Changing the project hash produces a different key."""
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")

        k1 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            compiler_version="25.4.0",
            flags=(),
        )
        k2 = compute_cache_key(
            test_source=src,
            project_hash="zzz",
            tests_tree_hash="bbb",
            compiler_version="25.4.0",
            flags=(),
        )
        assert k1 != k2

    def test_flag_change_different_key(self, tmp_path: Path):
        """Changing compiler flags produces a different key."""
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")

        k1 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            compiler_version="25.4.0",
            flags=("-O2",),
        )
        k2 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            compiler_version="25.4.0",
            flags=("-O0",),
        )
        assert k1 != k2


class TestCacheMetaRoundtrip:
    def test_write_then_read_returns_same_key(self, tmp_path: Path):
        """A written cache key can be read back identically."""
        meta = tmp_path / "cache_meta.json"
        write_cache_meta(meta, cache_key="abc123", compiler_version="25.4.0")
        assert read_cache_meta(meta) == "abc123"

    def test_overwrite_updates_key(self, tmp_path: Path):
        """Overwriting metadata replaces the stored key."""
        meta = tmp_path / "cache_meta.json"
        write_cache_meta(meta, cache_key="old_key", compiler_version="25.4.0")
        write_cache_meta(meta, cache_key="new_key", compiler_version="25.4.0")
        assert read_cache_meta(meta) == "new_key"

    def test_corrupt_json_returns_none(self, tmp_path: Path):
        """Corrupt JSON content returns None."""
        meta = tmp_path / "cache_meta.json"
        meta.write_text("{not valid json!!!")
        assert read_cache_meta(meta) is None

    def test_wrong_schema_version_returns_none(self, tmp_path: Path):
        """An unexpected schema_version returns None."""
        meta = tmp_path / "cache_meta.json"
        meta.write_text(json.dumps({
            "schema_version": 999,
            "cache_key": "abc",
            "compiler_version": "25.4.0",
        }))
        assert read_cache_meta(meta) is None

    def test_missing_file_returns_none(self, tmp_path: Path):
        """A nonexistent metadata file returns None."""
        meta = tmp_path / "does_not_exist.json"
        assert read_cache_meta(meta) is None

    def test_no_tmp_files_remain_after_write(self, tmp_path: Path):
        """Atomic write leaves no temporary files behind."""
        meta = tmp_path / "cache_meta.json"
        write_cache_meta(meta, cache_key="abc123", compiler_version="25.4.0")
        remaining = list(tmp_path.iterdir())
        assert all(not f.name.endswith(".tmp") for f in remaining)
        assert len(remaining) == 1

    def test_parent_dirs_created(self, tmp_path: Path):
        """Parent directories are created if they do not exist."""
        meta = tmp_path / "deep" / "nested" / "cache_meta.json"
        write_cache_meta(meta, cache_key="abc", compiler_version="25.4.0")
        assert read_cache_meta(meta) == "abc"

    def test_concurrent_writes_no_cross_contamination(self, tmp_path: Path):
        """Two threads writing different targets in the same dir don't clobber."""
        from concurrent.futures import ThreadPoolExecutor

        meta_a = tmp_path / "test_a.json"
        meta_b = tmp_path / "test_b.json"

        def write_a() -> None:
            for _ in range(20):
                write_cache_meta(meta_a, cache_key="key_a", compiler_version="1.0.0b2")

        def write_b() -> None:
            for _ in range(20):
                write_cache_meta(meta_b, cache_key="key_b", compiler_version="1.0.0b2")

        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(write_a)
            fb = pool.submit(write_b)
            fa.result()
            fb.result()

        assert read_cache_meta(meta_a) == "key_a"
        assert read_cache_meta(meta_b) == "key_b"
