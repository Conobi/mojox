"""Tests for bundle staging area management."""

from __future__ import annotations

from pathlib import Path

from mojox.staging import StagingResult, create_bundle_staging


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

    def test_same_content_shares_one_dir(self, tmp_path: Path) -> None:
        """Rerunning with identical inputs reuses the staging dir, keeping the harness path stable."""
        root = tmp_path / "project"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_a.mojo").write_text("x")
        staging = root / ".mojox" / "bundle"

        first = create_bundle_staging(root, ["tests/test_a.mojo"], "h", staging)
        second = create_bundle_staging(root, ["tests/test_a.mojo"], "h", staging)

        assert first == second
        assert [p.name for p in staging.iterdir()] == [first.staging_dir.name]

    def test_different_harnesses_never_share_a_dir(self, tmp_path: Path) -> None:
        """Two runs with other test selections must not overwrite or delete each other's harness."""
        root = tmp_path / "project"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_a.mojo").write_text("x")
        (root / "tests" / "test_b.mojo").write_text("y")
        staging = root / ".mojox" / "bundle"

        run_a = create_bundle_staging(root, ["tests/test_a.mojo"], "harness A", staging)
        run_b = create_bundle_staging(root, ["tests/test_b.mojo"], "harness B", staging)

        assert run_a.harness_path != run_b.harness_path
        assert run_a.include_path != run_b.include_path
        assert run_a.harness_path.read_text() == "harness A"
        assert run_b.harness_path.read_text() == "harness B"

    def test_leaves_no_build_leftovers(self, tmp_path: Path) -> None:
        """The private build dir is renamed or removed, never left beside the result."""
        root = tmp_path / "project"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_a.mojo").write_text("x")
        staging = root / ".mojox" / "bundle"

        result = create_bundle_staging(root, ["tests/test_a.mojo"], "h", staging)
        create_bundle_staging(root, ["tests/test_a.mojo"], "h", staging)

        assert [p.name for p in staging.iterdir()] == [result.staging_dir.name]
