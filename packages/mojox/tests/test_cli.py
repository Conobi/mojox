"""CLI argument parsing and subcommand routing."""

from __future__ import annotations

import subprocess
import sys

import pytest
from mojox._cli import build_parser


class TestBuildParser:
    def test_test_subcommand(self):
        """Test subcommand is recognized."""
        parser = build_parser()
        args = parser.parse_args(["test"])
        assert args.subcommand == "test"

    def test_run_subcommand_with_file(self):
        """Run subcommand requires a file argument."""
        parser = build_parser()
        args = parser.parse_args(["run", "main.mojo"])
        assert args.subcommand == "run"
        assert args.file == "main.mojo"

    def test_build_subcommand(self):
        """Build subcommand is recognized."""
        parser = build_parser()
        args = parser.parse_args(["build"])
        assert args.subcommand == "build"

    def test_check_subcommand(self):
        """Check subcommand is recognized."""
        parser = build_parser()
        args = parser.parse_args(["check"])
        assert args.subcommand == "check"

    def test_metadata_subcommand(self):
        """Metadata subcommand is recognized."""
        parser = build_parser()
        args = parser.parse_args(["metadata"])
        assert args.subcommand == "metadata"

    def test_profile_flag(self):
        """--profile overrides the default."""
        parser = build_parser()
        args = parser.parse_args(["test", "--profile", "release"])
        assert args.profile == "release"

    def test_default_profile_for_test(self):
        """Test defaults to dev profile."""
        parser = build_parser()
        args = parser.parse_args(["test"])
        assert args.profile == "dev"

    def test_default_profile_for_build(self):
        """Build defaults to release profile."""
        parser = build_parser()
        args = parser.parse_args(["build"])
        assert args.profile == "release"

    def test_jobs_flag(self):
        """--jobs/-j sets concurrency."""
        parser = build_parser()
        args = parser.parse_args(["test", "--jobs", "4"])
        assert args.jobs == 4

    def test_dry_run_flag(self):
        """--dry-run shows commands without executing."""
        parser = build_parser()
        args = parser.parse_args(["test", "--dry-run"])
        assert args.dry_run is True

    def test_no_config_flag(self):
        """--no-config disables settings discovery."""
        parser = build_parser()
        args = parser.parse_args(["test", "--no-config"])
        assert args.no_config is True

    def test_config_file_flag(self):
        """--config-file overrides discovery."""
        parser = build_parser()
        args = parser.parse_args(["test", "--config-file", "/path/to/config.toml"])
        assert args.config_file == "/path/to/config.toml"

    def test_define_flag(self):
        """-D adds define variables."""
        parser = build_parser()
        args = parser.parse_args(["test", "-D", "FAST=true", "-D", "DEBUG=1"])
        assert args.defines == ["FAST=true", "DEBUG=1"]

    def test_timeout_flag(self):
        """--timeout sets per-target timeout."""
        parser = build_parser()
        args = parser.parse_args(["test", "--timeout", "60"])
        assert args.timeout == 60

    def test_no_subcommand_exits(self):
        """No subcommand triggers exit."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


class TestTestSubcommandFlags:
    """Tests for test-only CLI flags (output, fail-fast, verbosity, filtering)."""

    def test_output_format_default(self):
        parser = build_parser()
        args = parser.parse_args(["test"])
        assert args.output_format is None

    def test_output_format_json(self):
        parser = build_parser()
        args = parser.parse_args(["test", "--output-format", "json"])
        assert args.output_format == "json"

    def test_fail_fast_default_true(self):
        parser = build_parser()
        args = parser.parse_args(["test"])
        assert args.fail_fast is True

    def test_no_fail_fast(self):
        parser = build_parser()
        args = parser.parse_args(["test", "--no-fail-fast"])
        assert args.fail_fast is False

    def test_success_output_flag(self):
        parser = build_parser()
        args = parser.parse_args(["test", "--success-output", "immediate"])
        assert args.success_output == "immediate"

    def test_failure_output_flag(self):
        parser = build_parser()
        args = parser.parse_args(["test", "--failure-output", "final"])
        assert args.failure_output == "final"

    def test_filter_flag(self):
        parser = build_parser()
        args = parser.parse_args(["test", "-k", "parse"])
        assert args.filter == "parse"

    def test_positional_paths(self):
        parser = build_parser()
        args = parser.parse_args(["test", "tests/unit/", "tests/integration/"])
        assert args.paths == ["tests/unit/", "tests/integration/"]

    def test_positional_paths_empty_by_default(self):
        parser = build_parser()
        args = parser.parse_args(["test"])
        assert args.paths == []

    def test_build_does_not_have_filter(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["build", "-k", "something"])

    def test_run_does_not_have_filter(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "main.mojo", "-k", "something"])


class TestCLIIntegration:
    def test_check_with_valid_manifest(self, tmp_path):
        """mojox check succeeds with a valid manifest."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "testlib"\nversion = "0.1.0"\n'
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_hello.mojo"
        test_file.write_text("from testing import assert_true\ndef test_hello():\n    assert_true(True)\n")
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "check"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert "testlib" in result.stderr
        assert result.returncode == 0

    def test_check_with_invalid_manifest(self, tmp_path):
        """mojox check exits 2 on invalid manifest."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "1.0.0"\n')
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "check"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "name" in result.stderr.lower()

    def test_check_detects_bare_assert(self, tmp_path):
        """mojox check warns on bare assert in test files but exits 0."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "testlib"\nversion = "0.1.0"\n')
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_bad.mojo"
        test_file.write_text("def test_bad():\n    assert x == 1\n")
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "check"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "assert" in result.stderr.lower()
        assert "warning" in result.stderr.lower()

    def test_check_detects_path_source(self, tmp_path):
        """mojox check warns on path overrides for published deps but exits 0."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "testlib"\nversion = "0.1.0"\n'
            'dependencies = ["mojox-build>=0.4"]\n\n'
            '[tool.uv.sources]\nmojox-build = { path = "../mojox" }\n'
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "check"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "path" in result.stderr.lower()
        assert "warning" in result.stderr.lower()

    def test_no_subcommand_exits_with_error(self):
        """Running without a subcommand shows help and exits 2."""
        result = subprocess.run(
            [sys.executable, "-m", "mojox"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2


class TestTestSubcommandIntegration:
    """Integration tests that run mojox test via subprocess."""

    def _make_project(self, tmp_path):
        """Create a minimal project with a pyproject.toml and a test file."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "testlib"\nversion = "0.1.0"\n'
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_hello.mojo"
        test_file.write_text("from testing import assert_true\ndef test_hello():\n    assert_true(True)\n")
        return tests_dir

    def test_output_format_json_produces_ndjson(self, tmp_path):
        """--output-format json with --dry-run emits JSON to stdout."""
        self._make_project(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "test", "--output-format", "json", "--dry-run"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        import json

        data = json.loads(result.stdout)
        assert data["type"] == "dry-run"

    def test_fail_fast_flag_accepted(self, tmp_path):
        """--no-fail-fast is accepted without error."""
        self._make_project(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "test", "--no-fail-fast", "--dry-run"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_filter_no_match_exits_0(self, tmp_path):
        """-k nonexistent prints warning and exits 0."""
        self._make_project(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "test", "-k", "nonexistent_xyz"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "no tests match" in result.stderr.lower()

    def test_success_output_flag_accepted(self, tmp_path):
        """--success-output=immediate is accepted."""
        self._make_project(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "test", "--success-output", "immediate", "--dry-run"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_filter_no_match_json_emits_suite_events(self, tmp_path):
        """--output-format json -k nonexistent emits suite events with test_count=0."""
        self._make_project(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "test", "--output-format", "json", "-k", "nonexistent_xyz"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        import json

        assert result.returncode == 0
        lines = [line for line in result.stdout.strip().splitlines() if line]
        assert len(lines) == 2
        started = json.loads(lines[0])
        finished = json.loads(lines[1])
        assert started["type"] == "suite"
        assert started["event"] == "started"
        assert started["test_count"] == 0
        assert finished["type"] == "suite"
        assert finished["event"] == "ok"
