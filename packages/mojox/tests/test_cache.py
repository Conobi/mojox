"""Cache key computation and tree hashing tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mojox.cache import (
    MOJO_IMPORT_SUFFIXES,
    compute_cache_key,
    hash_directory_tree,
    read_cache_meta,
    stamp_include_dirs,
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


    def test_fire_extension_edit_changes_hash(self, tmp_path: Path):
        """A lib written only in ``.\U0001f525`` files is still hashed."""
        lib = tmp_path / "navette"
        lib.mkdir()
        (lib / "__init__.\U0001f525").write_text("fn f(): pass")
        h1 = hash_directory_tree(lib)
        (lib / "__init__.\U0001f525").write_text("fn f(): return")
        assert hash_directory_tree(lib) != h1

    def test_every_import_suffix_hashed(self, tmp_path: Path):
        """Every suffix the dependency stamp watches also feeds the tree hash."""
        before = hash_directory_tree(tmp_path)
        for suffix in MOJO_IMPORT_SUFFIXES:
            (tmp_path / f"m{suffix}").write_bytes(b"x")
            after = hash_directory_tree(tmp_path)
            assert after != before, suffix
            before = after

class TestComputeCacheKey:
    def test_same_inputs_same_key(self, tmp_path: Path):
        """Identical inputs produce identical cache keys."""
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")

        k1 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            deps_stamp="ddd",
            compiler_version="25.4.0",
            mojo_path="/usr/bin/mojo",
            env={},
            flags=("-O2",),
        )
        k2 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            deps_stamp="ddd",
            compiler_version="25.4.0",
            mojo_path="/usr/bin/mojo",
            env={},
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
            deps_stamp="ddd",
            compiler_version="25.4.0",
            mojo_path="/usr/bin/mojo",
            env={},
            flags=(),
        )

        src.write_text("fn main(): return 1")
        k2 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            deps_stamp="ddd",
            compiler_version="25.4.0",
            mojo_path="/usr/bin/mojo",
            env={},
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
            deps_stamp="ddd",
            compiler_version="25.4.0",
            mojo_path="/usr/bin/mojo",
            env={},
            flags=(),
        )
        k2 = compute_cache_key(
            test_source=src,
            project_hash="zzz",
            tests_tree_hash="bbb",
            deps_stamp="ddd",
            compiler_version="25.4.0",
            mojo_path="/usr/bin/mojo",
            env={},
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
            deps_stamp="ddd",
            compiler_version="25.4.0",
            mojo_path="/usr/bin/mojo",
            env={},
            flags=("-O2",),
        )
        k2 = compute_cache_key(
            test_source=src,
            project_hash="aaa",
            tests_tree_hash="bbb",
            deps_stamp="ddd",
            compiler_version="25.4.0",
            mojo_path="/usr/bin/mojo",
            env={},
            flags=("-O0",),
        )
        assert k1 != k2


def _key(src: Path, **overrides) -> str:
    """Cache key over fixed baseline inputs, with *overrides* replacing any of them."""
    inputs = {
        "test_source": src,
        "project_hash": "aaa",
        "tests_tree_hash": "bbb",
        "deps_stamp": "ddd",
        "compiler_version": "25.4.0",
        "mojo_path": "/usr/bin/mojo",
        "env": {"A": "1"},
        "flags": ("-I", "/x", "-I", "/y"),
    }
    inputs.update(overrides)
    return compute_cache_key(**inputs)


class TestCacheKeyInputs:
    """The inputs added so stale binaries are never reused."""

    def test_deps_stamp_change_different_key(self, tmp_path: Path):
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")
        assert _key(src) != _key(src, deps_stamp="other")

    def test_env_change_different_key(self, tmp_path: Path):
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")
        assert _key(src) != _key(src, env={"A": "2"})
        assert _key(src) != _key(src, env={"A": "1", "B": "1"})

    def test_env_order_irrelevant(self, tmp_path: Path):
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")
        a = _key(src, env={"A": "1", "B": "2"})
        b = _key(src, env={"B": "2", "A": "1"})
        assert a == b

    def test_mojo_path_change_different_key(self, tmp_path: Path):
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")
        assert _key(src) != _key(src, mojo_path="/opt/other/mojo")

    def test_include_order_changes_key(self, tmp_path: Path):
        """First -I match wins in Mojo, so flag order is part of the key."""
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")
        assert _key(src) != _key(src, flags=("-I", "/y", "-I", "/x"))

    def test_flag_boundaries_unambiguous(self, tmp_path: Path):
        """Concatenation must not let ("ab", "c") collide with ("a", "bc")."""
        src = tmp_path / "t.mojo"
        src.write_text("fn main(): pass")
        assert _key(src, flags=("ab", "c")) != _key(src, flags=("a", "bc"))


class TestStampIncludeDirs:
    """Stat-based fingerprint of dependency include dirs."""

    def _dep(self, root: Path) -> Path:
        """A fake include dir holding one source package and one precompiled one."""
        pkg = root / "dep" / "navette"
        pkg.mkdir(parents=True)
        (pkg / "__init__.mojo").write_text("")
        (pkg / "core.mojo").write_text("fn f(): pass")
        (root / "dep" / "wire.mojoc").write_bytes(b"\x00" * 16)
        return root / "dep"

    def test_unchanged_tree_is_stable(self, tmp_path: Path):
        dep = self._dep(tmp_path)
        assert stamp_include_dirs((str(dep),)) == stamp_include_dirs((str(dep),))

    def test_size_change_changes_stamp(self, tmp_path: Path):
        dep = self._dep(tmp_path)
        target = dep / "navette" / "core.mojo"
        st = target.stat()
        before = stamp_include_dirs((str(dep),))
        target.write_text("fn f(): pass  # longer")
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert stamp_include_dirs((str(dep),)) != before

    def test_mtime_change_changes_stamp(self, tmp_path: Path):
        dep = self._dep(tmp_path)
        target = dep / "wire.mojoc"
        before = stamp_include_dirs((str(dep),))
        st = target.stat()
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        assert stamp_include_dirs((str(dep),)) != before

    def test_added_file_changes_stamp(self, tmp_path: Path):
        dep = self._dep(tmp_path)
        before = stamp_include_dirs((str(dep),))
        (dep / "navette" / "extra.mojo").write_text("")
        assert stamp_include_dirs((str(dep),)) != before

    def test_irrelevant_files_ignored(self, tmp_path: Path):
        dep = self._dep(tmp_path)
        before = stamp_include_dirs((str(dep),))
        (dep / "README.md").write_text("docs")
        (dep / "navette" / "notes.txt").write_text("x")
        assert stamp_include_dirs((str(dep),)) == before

    def test_all_mojo_artifact_suffixes_stamped(self, tmp_path: Path):
        dep = tmp_path / "dep"
        dep.mkdir()
        before = stamp_include_dirs((str(dep),))
        for name in ("a.mojo", "b.\U0001f525", "c.mojopkg", "d.mojoc"):
            (dep / name).write_text("")
            after = stamp_include_dirs((str(dep),))
            assert after != before, name
            before = after

    def test_dir_order_changes_stamp(self, tmp_path: Path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        for d in (a, b):
            d.mkdir()
            (d / "m.mojo").write_text(d.name)
        assert stamp_include_dirs((str(a), str(b))) != stamp_include_dirs((str(b), str(a)))

    def test_missing_dir_is_stable(self, tmp_path: Path):
        missing = str(tmp_path / "nope")
        s1 = stamp_include_dirs((missing,))
        s2 = stamp_include_dirs((missing,))
        assert s1 == s2
        assert s1 != stamp_include_dirs(())

    def test_missing_dir_appearing_changes_stamp(self, tmp_path: Path):
        path = tmp_path / "later"
        before = stamp_include_dirs((str(path),))
        path.mkdir()
        assert stamp_include_dirs((str(path),)) != before

    def test_symlinked_package_is_followed(self, tmp_path: Path):
        """Editable installs symlink mojo_packages/<pkg> to the project source."""
        src = tmp_path / "project" / "src" / "navette"
        src.mkdir(parents=True)
        (src / "__init__.mojo").write_text("")
        inc = tmp_path / "mojo_packages"
        inc.mkdir()
        (inc / "navette").symlink_to(src, target_is_directory=True)
        before = stamp_include_dirs((str(inc),))
        (src / "__init__.mojo").write_text("fn g(): pass")
        assert stamp_include_dirs((str(inc),)) != before

    def test_symlink_loop_terminates(self, tmp_path: Path):
        dep = self._dep(tmp_path)
        (dep / "navette" / "loop").symlink_to(dep, target_is_directory=True)
        (dep / "self").symlink_to(".", target_is_directory=True)
        assert stamp_include_dirs((str(dep),)) == stamp_include_dirs((str(dep),))

    def test_dangling_symlink_is_stable(self, tmp_path: Path):
        dep = self._dep(tmp_path)
        (dep / "gone.mojo").symlink_to(tmp_path / "does-not-exist.mojo")
        assert stamp_include_dirs((str(dep),)) == stamp_include_dirs((str(dep),))


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

    def test_concurrent_writes_same_target_do_not_crash(self, tmp_path: Path):
        """Threads share a pid, so a pid-based temp name would collide and raise."""
        from concurrent.futures import ThreadPoolExecutor

        meta = tmp_path / "test_same.json"

        def write(i: int) -> None:
            for _ in range(50):
                write_cache_meta(meta, cache_key=f"key_{i}", compiler_version="1.0.0b2")

        with ThreadPoolExecutor(max_workers=8) as pool:
            for future in [pool.submit(write, i) for i in range(8)]:
                future.result()

        assert read_cache_meta(meta) in {f"key_{i}" for i in range(8)}
        assert [p.name for p in tmp_path.iterdir()] == ["test_same.json"]

    def test_write_uses_unique_temp_names(self, tmp_path: Path, monkeypatch):
        """Each write stages through its own mkstemp file, never a pid-derived name."""
        import os

        staged: list[str] = []
        real_replace = os.replace

        def spy(src, dst):
            staged.append(os.fspath(src))
            real_replace(src, dst)

        monkeypatch.setattr("mojox.cache.os.replace", spy)
        meta = tmp_path / "m.json"
        write_cache_meta(meta, cache_key="a", compiler_version="1")
        write_cache_meta(meta, cache_key="b", compiler_version="1")
        assert len(staged) == 2
        assert staged[0] != staged[1]
        assert all(str(os.getpid()) not in Path(s).name for s in staged)


class TestCorruptMeta:
    """A damaged meta file must read as a cache miss, never raise."""

    def test_truncated_json(self, tmp_path: Path):
        meta = tmp_path / "m.json"
        meta.write_text('{"schema_version": 1, "cache_key": "ab')
        assert read_cache_meta(meta) is None

    def test_empty_file(self, tmp_path: Path):
        meta = tmp_path / "m.json"
        meta.write_bytes(b"")
        assert read_cache_meta(meta) is None

    def test_non_utf8_bytes(self, tmp_path: Path):
        meta = tmp_path / "m.json"
        meta.write_bytes(b"\xff\xfe\x00garbage")
        assert read_cache_meta(meta) is None

    def test_non_object_json(self, tmp_path: Path):
        meta = tmp_path / "m.json"
        meta.write_text("[1, 2, 3]")
        assert read_cache_meta(meta) is None

    def test_non_string_key(self, tmp_path: Path):
        meta = tmp_path / "m.json"
        meta.write_text(json.dumps({"schema_version": 1, "cache_key": 42}))
        assert read_cache_meta(meta) is None

    def test_meta_path_is_directory(self, tmp_path: Path):
        meta = tmp_path / "m.json"
        meta.mkdir()
        assert read_cache_meta(meta) is None
