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
        test_file.write_text(
            "from testing import assert_true\n"
            "def test_hello():\n"
            "    assert_true(True)\n"
        )
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
        """mojox check catches bare assert in test files."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "testlib"\nversion = "0.1.0"\n'
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_bad.mojo"
        test_file.write_text(
            "def test_bad():\n"
            "    assert x == 1\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "check"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "assert" in result.stderr.lower()

    def test_check_detects_path_source(self, tmp_path):
        """mojox check catches path overrides for published deps."""
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
        assert result.returncode == 1
        assert "path" in result.stderr.lower()

    def test_no_subcommand_exits_with_error(self):
        """Running without a subcommand shows help and exits 2."""
        result = subprocess.run(
            [sys.executable, "-m", "mojox"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
