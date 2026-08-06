"""Target discovery: source tree -> TargetGraph."""

from __future__ import annotations

from pathlib import Path

import pytest

from mojox_core._errors import ConfigError
from mojox_core._types import BinaryEntry, TargetKind, LintConfig
from mojox_core.targets import discover


def _make_tree(tmp_path: Path, files: list[str]) -> Path:
    """Create a source tree with the given file paths."""
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {f}\n")
    return tmp_path


def _minimal_manifest(**overrides):
    from mojox_core._types import Manifest

    defaults = dict(
        name="x", version="1.0.0", description=None, readme=None,
        license_expr=None, license_files=(), requires_python=None,
        dependencies=(), optional_dependencies={}, keywords=(), authors=(),
        maintainers=(), urls={}, classifiers=(), packages=None,
        package_root="src", binaries=(), test_roots=("tests",),
        test_parallel=False, defines={}, flags=(), lints=LintConfig(),
        optimize=None, debug_level=None, pre_build=(), native_libs=(),
        source_include=None, source_exclude=(), wheel_exclude=(),
        profiles={}, ore_seed=None, build_profile="release",
    )
    defaults.update(overrides)
    return Manifest(**defaults)


class TestTestDiscovery:
    def test_finds_test_files_at_depth_one(self, tmp_path):
        root = _make_tree(tmp_path, ["tests/test_parser.mojo"])
        m = _minimal_manifest()
        g = discover(m, root)
        assert len([t for t in g.targets if t.kind == TargetKind.TEST]) == 1

    def test_finds_test_files_at_depth_five(self, tmp_path):
        root = _make_tree(tmp_path, [
            "tests/a/b/c/d/test_deep.mojo",
        ])
        m = _minimal_manifest()
        g = discover(m, root)
        tests = [t for t in g.targets if t.kind == TargetKind.TEST]
        assert len(tests) == 1
        assert "test_deep.mojo" in tests[0].path

    def test_ignores_non_test_prefixed_files(self, tmp_path):
        root = _make_tree(tmp_path, [
            "tests/test_good.mojo",
            "tests/helper.mojo",
            "tests/utils.mojo",
            "tests/conftest.mojo",
        ])
        m = _minimal_manifest()
        g = discover(m, root)
        tests = [t for t in g.targets if t.kind == TargetKind.TEST]
        assert len(tests) == 1
        assert "test_good" in tests[0].path

    def test_honours_test_roots_override(self, tmp_path):
        root = _make_tree(tmp_path, [
            "tests/test_default.mojo",
            "custom/test_custom.mojo",
        ])
        m = _minimal_manifest(test_roots=("custom",))
        g = discover(m, root)
        tests = [t for t in g.targets if t.kind == TargetKind.TEST]
        assert len(tests) == 1
        assert "test_custom" in tests[0].path


class TestExcludedDirectories:
    @pytest.mark.parametrize("excluded", [
        ".git", ".claude", ".worktrees", ".venv", "venv", "build",
        "dist", "site-packages", "mojo_packages", "target",
    ])
    def test_never_descends_excluded_directories(self, tmp_path, excluded):
        root = _make_tree(tmp_path, [
            f"tests/{excluded}/test_hidden.mojo",
            "tests/test_visible.mojo",
        ])
        m = _minimal_manifest()
        g = discover(m, root)
        tests = [t for t in g.targets if t.kind == TargetKind.TEST]
        assert len(tests) == 1
        assert "test_visible" in tests[0].path

    def test_nested_pyproject_excludes_directory(self, tmp_path):
        root = _make_tree(tmp_path, [
            "tests/nested/pyproject.toml",
            "tests/nested/test_in_nested.mojo",
            "tests/test_visible.mojo",
        ])
        m = _minimal_manifest()
        g = discover(m, root)
        tests = [t for t in g.targets if t.kind == TargetKind.TEST]
        assert len(tests) == 1
        assert "test_visible" in tests[0].path


class TestTestRootsEdgeCases:
    def test_empty_test_roots_exits_zero(self, tmp_path):
        root = _make_tree(tmp_path, ["tests/test_ignored.mojo"])
        m = _minimal_manifest(test_roots=())
        g = discover(m, root)
        tests = [t for t in g.targets if t.kind == TargetKind.TEST]
        assert len(tests) == 0

    def test_configured_roots_with_no_targets_exits_nonzero(self, tmp_path):
        root = _make_tree(tmp_path, ["tests/helper.mojo"])
        m = _minimal_manifest(test_roots=("tests",))
        with pytest.raises(ConfigError, match="no test targets"):
            discover(m, root)

    def test_missing_test_root_dir_errors(self, tmp_path):
        root = _make_tree(tmp_path, [])
        m = _minimal_manifest(test_roots=("nonexistent",))
        with pytest.raises(ConfigError, match="nonexistent"):
            discover(m, root)


class TestExampleDiscovery:
    def test_discovers_example_files(self, tmp_path):
        root = _make_tree(tmp_path, [
            "examples/demo.mojo",
            "examples/complex/main.mojo",
            "tests/.gitkeep",
        ])
        m = _minimal_manifest(test_roots=())
        g = discover(m, root)
        examples = [t for t in g.targets if t.kind == TargetKind.EXAMPLE]
        assert len(examples) == 2

    def test_binary_entry_suppresses_example_discovery(self, tmp_path):
        root = _make_tree(tmp_path, [
            "examples/demo.mojo",
            "tests/.gitkeep",
        ])
        m = _minimal_manifest(
            test_roots=(),
            binaries=(BinaryEntry(source="examples/demo.mojo", name="demo"),),
        )
        g = discover(m, root)
        examples = [t for t in g.targets if t.kind == TargetKind.EXAMPLE]
        bins = [t for t in g.targets if t.kind == TargetKind.BIN]
        assert len(examples) == 0
        assert len(bins) == 1


class TestLibDiscovery:
    def test_egg_info_excluded_from_lib_discovery(self, tmp_path):
        root = _make_tree(tmp_path, [
            "src/mylib/__init__.mojo",
            "src/mylib.egg-info/PKG-INFO",
            "tests/.gitkeep",
        ])
        m = _minimal_manifest(test_roots=())
        g = discover(m, root)
        libs = [t for t in g.targets if t.kind == TargetKind.LIB]
        assert len(libs) == 1
        assert "egg-info" not in libs[0].path

    def test_excluded_dirs_skipped_in_lib_discovery(self, tmp_path):
        root = _make_tree(tmp_path, [
            "src/mylib/__init__.mojo",
            "src/build/something.mojo",
            "tests/.gitkeep",
        ])
        m = _minimal_manifest(test_roots=())
        g = discover(m, root)
        libs = [t for t in g.targets if t.kind == TargetKind.LIB]
        assert len(libs) == 1
        assert "build" not in libs[0].path


class TestExampleExclusion:
    def test_excluded_dirs_skipped_in_example_discovery(self, tmp_path):
        root = _make_tree(tmp_path, [
            "examples/demo.mojo",
            "examples/build/main.mojo",
            "tests/.gitkeep",
        ])
        m = _minimal_manifest(test_roots=())
        g = discover(m, root)
        examples = [t for t in g.targets if t.kind == TargetKind.EXAMPLE]
        assert len(examples) == 1
        assert "demo" in examples[0].path


class TestUnsearchedDirsWarning:
    def test_reports_unsearched_dirs_with_test_files(self, tmp_path):
        root = _make_tree(tmp_path, [
            "tests/test_found.mojo",
            "conformance/test_missed.mojo",
        ])
        m = _minimal_manifest()
        g = discover(m, root)
        assert len(g.unsearched_test_dirs) >= 1
        dirs = {d[0] for d in g.unsearched_test_dirs}
        assert "conformance" in dirs
