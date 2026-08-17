"""Tests for bundle staging area management."""

from __future__ import annotations

from pathlib import Path

from mojox.staging import StagingResult, cleanup_staging, create_bundle_staging


class TestCreateBundleStaging:
    """Tests for create_bundle_staging."""

    def test_symlinks_test_files(self, tmp_path: Path) -> None:
        """Test .mojo files are symlinked into the staging area."""
        root = tmp_path / "project"
        root.mkdir()
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_a.mojo").write_text("def test_foo(): pass")

        result = create_bundle_staging(
            root,
            ["tests/test_a.mojo"],
            "def main(): pass",
            staging_dir=root / ".mojox" / "bundle",
        )

        staged = Path(result.include_path) / "tests" / "test_a.mojo"
        assert staged.is_symlink()
        assert staged.read_text() == "def test_foo(): pass"

    def test_creates_init_mojo_where_missing(self, tmp_path: Path) -> None:
        """Empty __init__.mojo is created for dirs that lack one."""
        root = tmp_path / "project"
        root.mkdir()
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_a.mojo").write_text("def test_foo(): pass")

        result = create_bundle_staging(
            root,
            ["tests/test_a.mojo"],
            "def main(): pass",
        )

        init = Path(result.include_path) / "tests" / "__init__.mojo"
        assert init.exists()
        assert init.read_text() == ""

    def test_symlinks_existing_init_mojo(self, tmp_path: Path) -> None:
        """Existing __init__.mojo is symlinked, not replaced."""
        root = tmp_path / "project"
        root.mkdir()
        tests = root / "tests"
        tests.mkdir()
        (tests / "__init__.mojo").write_text("# real init")
        (tests / "test_a.mojo").write_text("def test_foo(): pass")

        result = create_bundle_staging(
            root,
            ["tests/test_a.mojo"],
            "def main(): pass",
        )

        init = Path(result.include_path) / "tests" / "__init__.mojo"
        assert init.is_symlink()
        assert init.read_text() == "# real init"

    def test_nested_dirs_all_get_init(self, tmp_path: Path) -> None:
        """Every directory in the path chain gets an __init__.mojo."""
        root = tmp_path / "project"
        root.mkdir()
        deep = root / "tests" / "http"
        deep.mkdir(parents=True)
        (deep / "test_method.mojo").write_text("def test_get(): pass")

        result = create_bundle_staging(
            root,
            ["tests/http/test_method.mojo"],
            "def main(): pass",
        )

        stage = Path(result.include_path)
        assert (stage / "tests" / "__init__.mojo").exists()
        assert (stage / "tests" / "http" / "__init__.mojo").exists()

    def test_writes_harness_file(self, tmp_path: Path) -> None:
        """The harness source is written to the staging directory."""
        root = tmp_path / "project"
        root.mkdir()
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_a.mojo").write_text("def test_foo(): pass")

        result = create_bundle_staging(
            root,
            ["tests/test_a.mojo"],
            "from sys import argv\ndef main(): pass",
        )

        assert result.harness_path.exists()
        assert result.harness_path.read_text() == "from sys import argv\ndef main(): pass"
        assert result.harness_path.suffix == ".mojo"

    def test_multiple_test_files(self, tmp_path: Path) -> None:
        """Multiple test files from different dirs are all staged."""
        root = tmp_path / "project"
        root.mkdir()
        (root / "tests").mkdir()
        (root / "tests" / "test_a.mojo").write_text("a")
        (root / "tests" / "http").mkdir()
        (root / "tests" / "http" / "test_b.mojo").write_text("b")

        result = create_bundle_staging(
            root,
            ["tests/test_a.mojo", "tests/http/test_b.mojo"],
            "harness",
        )

        stage = Path(result.include_path)
        assert (stage / "tests" / "test_a.mojo").read_text() == "a"
        assert (stage / "tests" / "http" / "test_b.mojo").read_text() == "b"

    def test_result_type(self, tmp_path: Path) -> None:
        """Returns a StagingResult with all fields populated."""
        root = tmp_path / "project"
        root.mkdir()
        (root / "tests").mkdir()
        (root / "tests" / "test_a.mojo").write_text("x")

        result = create_bundle_staging(root, ["tests/test_a.mojo"], "h")

        assert isinstance(result, StagingResult)
        assert Path(result.include_path).is_dir()
        assert result.harness_path.is_file()
        assert isinstance(result.diagnostics, tuple)

    def test_idempotent_on_rerun(self, tmp_path: Path) -> None:
        """Running twice on the same staging dir does not fail."""
        root = tmp_path / "project"
        root.mkdir()
        (root / "tests").mkdir()
        (root / "tests" / "test_a.mojo").write_text("x")
        staging = root / ".mojox" / "bundle"

        create_bundle_staging(root, ["tests/test_a.mojo"], "h1", staging)
        result = create_bundle_staging(root, ["tests/test_a.mojo"], "h2", staging)

        assert result.harness_path.read_text() == "h2"


class TestCleanupStaging:
    """Tests for cleanup_staging."""

    def test_removes_staging_dir(self, tmp_path: Path) -> None:
        """Removes the entire staging directory tree."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "stage").mkdir()
        (staging / "harness.mojo").write_text("test")

        cleanup_staging(staging)
        assert not staging.exists()

    def test_noop_if_absent(self, tmp_path: Path) -> None:
        """Does not raise when the directory does not exist."""
        cleanup_staging(tmp_path / "nonexistent")
