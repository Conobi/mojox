"""CLI argument parsing and subcommand routing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from mojox.cli import build_parser


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

    def test_no_cache_flag_default_false(self):
        """--no-cache defaults to False."""
        parser = build_parser()
        args = parser.parse_args(["test"])
        assert args.no_cache is False

    def test_no_cache_flag(self):
        """--no-cache sets no_cache to True."""
        parser = build_parser()
        args = parser.parse_args(["test", "--no-cache"])
        assert args.no_cache is True

    def test_examples_flag_default_false(self):
        """Example checking is opt-in."""
        parser = build_parser()
        args = parser.parse_args(["test"])
        assert args.examples is False

    def test_examples_flag(self):
        parser = build_parser()
        args = parser.parse_args(["test", "--examples"])
        assert args.examples is True

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


class TestExamplesOptIn:
    """Examples are planned by ``mojox test`` only on request."""

    def _make_project(self, tmp_path):
        """Project with one test and one example."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "testlib"\nversion = "0.1.0"\n'
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_hello.mojo").write_text(
            "from testing import assert_true\ndef test_hello():\n    assert_true(True)\n"
        )
        example_dir = tmp_path / "examples" / "hello"
        example_dir.mkdir(parents=True)
        (example_dir / "main.mojo").write_text("def main():\n    print('hi')\n")

    def _dry_run_kinds(self, tmp_path, *extra_args):
        import json

        result = subprocess.run(
            [sys.executable, "-m", "mojox", "test", "--output-format", "json", "--dry-run", *extra_args],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return [c["kind"] for c in json.loads(result.stdout)["commands"]]

    def test_default_excludes_examples(self, tmp_path):
        self._make_project(tmp_path)
        kinds = self._dry_run_kinds(tmp_path)
        assert "check-example" not in kinds
        assert "build-test" in kinds

    def test_examples_flag_includes_examples(self, tmp_path):
        self._make_project(tmp_path)
        kinds = self._dry_run_kinds(tmp_path, "--examples")
        assert "check-example" in kinds
        assert "build-test" in kinds

    def test_examples_path_includes_examples(self, tmp_path):
        """A positional ``examples/...`` path opts in even though it matches no test."""
        self._make_project(tmp_path)
        kinds = self._dry_run_kinds(tmp_path, "examples/hello")
        assert "check-example" in kinds
        assert "build-test" not in kinds

    def test_test_path_excludes_examples(self, tmp_path):
        self._make_project(tmp_path)
        kinds = self._dry_run_kinds(tmp_path, "tests/test_hello.mojo")
        assert kinds == ["build-test"]

    def test_human_dry_run_omits_check_example_group(self, tmp_path):
        self._make_project(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "test", "--dry-run"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "check-example" not in result.stdout + result.stderr


class TestCacheSubcommand:
    """Tests for the cache subcommand."""

    def test_cache_subcommand_recognized(self):
        """cache clean is recognized by the parser."""
        parser = build_parser()
        args = parser.parse_args(["cache", "clean"])
        assert args.subcommand == "cache"
        assert args.cache_action == "clean"

    def test_cache_without_action_exits(self):
        """cache without an action triggers an error."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["cache"])

    def test_cache_clean_removes_directory(self, tmp_path):
        """cache clean removes the .mojox/cache directory."""
        cache_dir = tmp_path / ".mojox" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "bin").mkdir()
        (cache_dir / "bin" / "test_hello").write_text("binary")
        (cache_dir / "meta").mkdir()
        (cache_dir / "meta" / "test_hello.json").write_text("{}")

        result = subprocess.run(
            [sys.executable, "-m", "mojox", "cache", "clean"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Removed" in result.stdout
        assert not cache_dir.exists()

    def test_cache_clean_nothing_to_clean(self, tmp_path):
        """cache clean with no cache directory prints info message."""
        result = subprocess.run(
            [sys.executable, "-m", "mojox", "cache", "clean"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Nothing to clean" in result.stdout


class TestBundleFlag:
    def test_bundle_flag_accepted(self):
        """--bundle is accepted by the test subparser."""
        parser = build_parser()
        args = parser.parse_args(["test", "--bundle"])
        assert args.bundle is True

    def test_bundle_flag_default_false(self):
        """--bundle defaults to False."""
        parser = build_parser()
        args = parser.parse_args(["test"])
        assert args.bundle is False

    def test_bundle_with_no_cache(self):
        """--bundle --no-cache is accepted."""
        parser = build_parser()
        args = parser.parse_args(["test", "--bundle", "--no-cache"])
        assert args.bundle is True
        assert args.no_cache is True

    def test_bundle_with_filter(self):
        """--bundle -k pattern is accepted."""
        parser = build_parser()
        args = parser.parse_args(["test", "--bundle", "-k", "test_foo"])
        assert args.bundle is True
        assert args.filter == "test_foo"


_FAKE_MOJO = """#!{python}
import pathlib, stat, sys
args = sys.argv[1:]
assert args[0] == "build", args
out = pathlib.Path(args[args.index("-o") + 1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("#!/bin/sh\\nexit 0\\n")
out.chmod(out.stat().st_mode | stat.S_IEXEC)
with open({log!r}, "a") as f:
    f.write(args[1] + "\\n")
"""


class TestCacheInvalidatesOnDependencyChange:
    """``mojox test`` end to end with a fake compiler: a dependency edit forces a rebuild."""

    @pytest.fixture
    def project(self, tmp_path, monkeypatch):
        """Project with one test and one path dependency, wired to a fake ``mojo``."""
        from mojox_core import DistKind, Toolchain

        proj = tmp_path / "proj"
        (proj / "tests").mkdir(parents=True)
        (proj / "pyproject.toml").write_text('[project]\nname = "testlib"\nversion = "0.1.0"\n')
        (proj / "tests" / "test_hello.mojo").write_text("def test_hello():\n    pass\n")
        # A lib package plus a sibling module that is not a target: bundle
        # mode puts src/ on -I, so util.mojo is importable yet in no target.
        (proj / "src" / "mylib").mkdir(parents=True)
        (proj / "src" / "mylib" / "__init__.mojo").write_text("")
        (proj / "src" / "util.mojo").write_text("fn u(): pass\n")

        dep = tmp_path / "mojo_packages"
        (dep / "navette").mkdir(parents=True)
        (dep / "navette" / "__init__.mojo").write_text("fn f(): pass\n")

        log = tmp_path / "builds.log"
        log.touch()
        fake = tmp_path / "bin" / "mojo"
        fake.parent.mkdir()
        fake.write_text(_FAKE_MOJO.format(python=sys.executable, log=str(log)))
        fake.chmod(0o755)

        toolchain = Toolchain(mojo_path=str(fake), version="1.0.0", subcommand="precompile", extension=".mojoc")
        dist = {
            "name": "navette",
            "include_dir": str(dep),
            "kind": DistKind.SOURCE,
            "packages": ["navette"],
            "provenance": "0.1.0",
            "native_lib_dirs": (),
        }
        monkeypatch.setattr("mojox_core.io.toolchain.resolve", lambda: toolchain)
        monkeypatch.setattr("mojox_core.io.environment.read_distributions", lambda: [dist])
        monkeypatch.chdir(proj)
        return dep, log

    def _run(self, *extra: str) -> int:
        from mojox.cli import _cmd_test

        args = build_parser().parse_args(["test", "--no-config", *extra])
        with pytest.raises(SystemExit) as exc:
            _cmd_test(args)
        return exc.value.code

    @pytest.mark.parametrize("mode", [(), ("--bundle",)], ids=["per-file", "bundle"])
    def test_unchanged_dependency_is_a_hit(self, project, mode):
        _dep, log = project
        assert self._run(*mode) == 0
        assert self._run(*mode) == 0
        assert len(log.read_text().splitlines()) == 1

    @pytest.mark.parametrize("mode", [(), ("--bundle",)], ids=["per-file", "bundle"])
    def test_dependency_edit_rebuilds(self, project, mode):
        dep, log = project
        assert self._run(*mode) == 0
        (dep / "navette" / "__init__.mojo").write_text("fn f(): return\n")
        assert self._run(*mode) == 0
        assert len(log.read_text().splitlines()) == 2

    def test_bundle_non_target_sibling_module_edit_rebuilds(self, project):
        _dep, log = project
        assert self._run("--bundle") == 0
        (Path.cwd() / "src" / "util.mojo").write_text("fn u(): return\n")
        assert self._run("--bundle") == 0
        assert len(log.read_text().splitlines()) == 2
