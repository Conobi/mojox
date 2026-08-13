"""Test filtering: pure function from commands x filter -> commands."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

import pytest

from mojox._cli import apply_filters
from mojox_core import Command, CommandKind


def _cmd(target_id: str, kind: CommandKind = CommandKind.RUN_TEST) -> Command:
    """Build a minimal Command for filter testing."""
    return Command(
        argv=("/usr/bin/mojo", "run", "t.mojo"),
        cwd=PurePosixPath("/workspace"),
        env={"PATH": "/usr/bin"},
        kind=kind,
        target_id=target_id,
        timeout_s=300,
        outputs=(),
        depends_on=(),
    )


class TestApplyFilters:
    """Filter logic: paths and name patterns."""

    def test_no_filters_returns_all(self, tmp_path: Path):
        cmds = (_cmd("tests/test_a.mojo"), _cmd("tests/test_b.mojo"))
        result = apply_filters(cmds, paths=(), pattern=None, project_root=tmp_path)
        assert result == cmds

    def test_path_prefix_filters_tests(self, tmp_path: Path):
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        cmds = (
            _cmd("lib/mylib", CommandKind.COMPILE_PACKAGE),
            _cmd("tests/unit/test_a.mojo"),
            _cmd("tests/integration/test_b.mojo"),
        )
        result = apply_filters(
            cmds, paths=(str(tmp_path / "tests" / "unit"),),
            pattern=None, project_root=tmp_path,
        )
        target_ids = [c.target_id for c in result]
        assert "lib/mylib" in target_ids
        assert "tests/unit/test_a.mojo" in target_ids
        assert "tests/integration/test_b.mojo" not in target_ids

    def test_compile_commands_never_filtered(self, tmp_path: Path):
        cmds = (
            _cmd("lib/mylib", CommandKind.COMPILE_PACKAGE),
            _cmd("bin/mybin", CommandKind.COMPILE_BINARY),
            _cmd("examples/ex", CommandKind.CHECK_EXAMPLE),
            _cmd("tests/test_a.mojo"),
        )
        result = apply_filters(cmds, paths=(), pattern="nonexistent", project_root=tmp_path)
        kinds = [c.kind for c in result]
        assert CommandKind.COMPILE_PACKAGE in kinds
        assert CommandKind.COMPILE_BINARY in kinds
        assert CommandKind.CHECK_EXAMPLE in kinds
        assert CommandKind.RUN_TEST not in kinds

    def test_name_pattern_case_insensitive(self, tmp_path: Path):
        cmds = (
            _cmd("tests/test_Parse.mojo"),
            _cmd("tests/test_other.mojo"),
        )
        result = apply_filters(cmds, paths=(), pattern="parse", project_root=tmp_path)
        assert len(result) == 1
        assert result[0].target_id == "tests/test_Parse.mojo"

    def test_path_and_pattern_intersection(self, tmp_path: Path):
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        cmds = (
            _cmd("tests/unit/test_parse.mojo"),
            _cmd("tests/unit/test_build.mojo"),
            _cmd("tests/integration/test_parse.mojo"),
        )
        result = apply_filters(
            cmds, paths=(str(tmp_path / "tests" / "unit"),),
            pattern="parse", project_root=tmp_path,
        )
        assert len(result) == 1
        assert result[0].target_id == "tests/unit/test_parse.mojo"

    def test_file_path_exact_match(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        cmds = (
            _cmd("tests/test_a.mojo"),
            _cmd("tests/test_ab.mojo"),
        )
        result = apply_filters(
            cmds, paths=(str(tmp_path / "tests" / "test_a.mojo"),),
            pattern=None, project_root=tmp_path,
        )
        assert len(result) == 1
        assert result[0].target_id == "tests/test_a.mojo"

    def test_trailing_slash_normalized(self, tmp_path: Path):
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        cmds = (_cmd("tests/unit/test_a.mojo"),)
        result = apply_filters(
            cmds, paths=(str(tmp_path / "tests" / "unit") + "/",),
            pattern=None, project_root=tmp_path,
        )
        assert len(result) == 1

    def test_dot_matches_all(self, tmp_path: Path):
        cmds = (
            _cmd("tests/test_a.mojo"),
            _cmd("tests/test_b.mojo"),
        )
        result = apply_filters(
            cmds, paths=(str(tmp_path),),
            pattern=None, project_root=tmp_path,
        )
        assert len(result) == 2

    def test_empty_pattern_matches_all(self, tmp_path: Path):
        cmds = (
            _cmd("tests/test_a.mojo"),
            _cmd("tests/test_b.mojo"),
        )
        result = apply_filters(cmds, paths=(), pattern="", project_root=tmp_path)
        assert len(result) == 2

    def test_multiple_paths(self, tmp_path: Path):
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        (tmp_path / "tests" / "e2e").mkdir(parents=True)
        cmds = (
            _cmd("tests/unit/test_a.mojo"),
            _cmd("tests/integration/test_b.mojo"),
            _cmd("tests/e2e/test_c.mojo"),
        )
        result = apply_filters(
            cmds,
            paths=(str(tmp_path / "tests" / "unit"), str(tmp_path / "tests" / "e2e")),
            pattern=None, project_root=tmp_path,
        )
        target_ids = [c.target_id for c in result]
        assert "tests/unit/test_a.mojo" in target_ids
        assert "tests/e2e/test_c.mojo" in target_ids
        assert "tests/integration/test_b.mojo" not in target_ids

    def test_outside_project_root_matches_nothing(self, tmp_path: Path):
        cmds = (_cmd("tests/test_a.mojo"),)
        result = apply_filters(
            cmds, paths=("/some/other/path",),
            pattern=None, project_root=tmp_path,
        )
        assert len(result) == 0

    def test_relative_path_resolved_against_cwd(self, tmp_path: Path):
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        cmds = (_cmd("tests/unit/test_a.mojo"),)
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = apply_filters(
                cmds, paths=("tests/unit",),
                pattern=None, project_root=tmp_path,
            )
        finally:
            os.chdir(old_cwd)
        assert len(result) == 1
        assert result[0].target_id == "tests/unit/test_a.mojo"
