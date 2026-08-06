"""Owned lints: bare-assert-in-test-target and path-source-in-published-manifest."""

from __future__ import annotations

from pathlib import Path

from mojox._lints import lint_bare_assert, lint_path_source, LintFinding


class TestLintBareAssert:
    def test_bare_assert_detected(self, tmp_path):
        test_file = tmp_path / "test_foo.mojo"
        test_file.write_text(
            "def test_it():\n"
            "    assert x == 1\n"
        )
        findings = lint_bare_assert(test_file)
        assert len(findings) == 1
        assert findings[0].line == 2
        assert "assert" in findings[0].message.lower()

    def test_assert_true_not_flagged(self, tmp_path):
        test_file = tmp_path / "test_foo.mojo"
        test_file.write_text(
            "from testing import assert_true\n"
            "def test_it():\n"
            "    assert_true(x == 1)\n"
        )
        findings = lint_bare_assert(test_file)
        assert len(findings) == 0

    def test_assert_equal_not_flagged(self, tmp_path):
        test_file = tmp_path / "test_foo.mojo"
        test_file.write_text(
            "from testing import assert_equal\n"
            "def test_it():\n"
            "    assert_equal(x, 1)\n"
        )
        findings = lint_bare_assert(test_file)
        assert len(findings) == 0

    def test_debug_assert_flagged(self, tmp_path):
        test_file = tmp_path / "test_foo.mojo"
        test_file.write_text(
            "def test_it():\n"
            "    debug_assert(x > 0)\n"
        )
        findings = lint_bare_assert(test_file)
        assert len(findings) == 1

    def test_multiple_bare_asserts(self, tmp_path):
        test_file = tmp_path / "test_foo.mojo"
        test_file.write_text(
            "def test_it():\n"
            "    assert x == 1\n"
            "    assert y == 2\n"
            "    assert z == 3\n"
        )
        findings = lint_bare_assert(test_file)
        assert len(findings) == 3

    def test_assert_paren_form_detected(self, tmp_path):
        """The assert(cond) form (no space) is also detected."""
        test_file = tmp_path / "test_foo.mojo"
        test_file.write_text(
            "def test_it():\n"
            "    assert(x == 1)\n"
        )
        findings = lint_bare_assert(test_file)
        assert len(findings) == 1
        assert findings[0].line == 2

    def test_assert_in_comment_not_flagged(self, tmp_path):
        test_file = tmp_path / "test_foo.mojo"
        test_file.write_text(
            "def test_it():\n"
            "    # assert x == 1\n"
            "    pass\n"
        )
        findings = lint_bare_assert(test_file)
        assert len(findings) == 0

    def test_assert_in_middle_of_line_not_flagged(self, tmp_path):
        """A line starting with 'var' that contains 'assert' mid-line is not flagged."""
        test_file = tmp_path / "test_foo.mojo"
        test_file.write_text(
            'def test_it():\n'
            '    var msg = "assert something"\n'
        )
        findings = lint_bare_assert(test_file)
        assert len(findings) == 0

    def test_non_test_file_returns_empty(self, tmp_path):
        lib_file = tmp_path / "lib.mojo"
        lib_file.write_text(
            "def helper():\n"
            "    assert x > 0\n"
        )
        findings = lint_bare_assert(lib_file)
        assert len(findings) == 0


class TestLintPathSource:
    def test_path_source_detected(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "mylib"\nversion = "1.0.0"\n'
            'dependencies = ["mojox-build>=0.4"]\n\n'
            '[tool.uv.sources]\nmojox-build = { path = "../mojox" }\n'
        )
        findings = lint_path_source(pyproject)
        assert len(findings) == 1
        assert "path" in findings[0].message.lower()
        assert "mojox-build" in findings[0].message

    def test_no_path_source(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "mylib"\nversion = "1.0.0"\n'
            'dependencies = ["mojox-build>=0.4"]\n\n'
            '[tool.uv.sources]\nmojox-build = { git = "https://github.com/foo/bar" }\n'
        )
        findings = lint_path_source(pyproject)
        assert len(findings) == 0

    def test_no_uv_sources(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "mylib"\nversion = "1.0.0"\n'
        )
        findings = lint_path_source(pyproject)
        assert len(findings) == 0

    def test_path_source_with_undeclared_dep_not_flagged(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "mylib"\nversion = "1.0.0"\n'
            'dependencies = []\n\n'
            '[tool.uv.sources]\ndev-tool = { path = "../dev" }\n'
        )
        findings = lint_path_source(pyproject)
        assert len(findings) == 0
