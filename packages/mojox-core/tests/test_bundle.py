"""Tests for bundle harness generator."""

from __future__ import annotations

from mojox_core.bundle import discover_test_functions, file_path_to_module_path


class TestDiscoverTestFunctions:
    def test_finds_def_functions(self) -> None:
        """Finds def test_* at column 0."""
        source = "def test_foo():\n    pass\ndef test_bar():\n    pass\n"
        assert discover_test_functions(source) == ("test_foo", "test_bar")

    def test_finds_fn_functions(self) -> None:
        """Finds fn test_* at column 0."""
        source = "fn test_foo():\n    pass\n"
        assert discover_test_functions(source) == ("test_foo",)

    def test_finds_raising_signatures(self) -> None:
        """Both def and fn with 'raises' are discovered."""
        source = "def test_a() raises:\n    pass\nfn test_b() raises:\n    pass\n"
        assert discover_test_functions(source) == ("test_a", "test_b")

    def test_ignores_nested_functions(self) -> None:
        """Functions indented inside structs or blocks are not discovered."""
        source = "struct Foo:\n    def test_nested(self):\n        pass\n"
        assert discover_test_functions(source) == ()

    def test_ignores_non_test_functions(self) -> None:
        """Functions not prefixed with test_ are ignored."""
        source = "def helper():\n    pass\ndef main():\n    pass\n"
        assert discover_test_functions(source) == ()

    def test_empty_source(self) -> None:
        """Empty source returns empty tuple."""
        assert discover_test_functions("") == ()

    def test_mixed_content(self) -> None:
        """Only top-level test_* functions are found among other declarations."""
        source = (
            "from testing import assert_true\n\n"
            "def helper(): pass\n\n"
            "def test_first() raises:\n    assert_true(True)\n\n"
            "struct Suite:\n    def test_inside(self): pass\n\n"
            "fn test_second():\n    pass\n"
        )
        assert discover_test_functions(source) == ("test_first", "test_second")

    def test_preserves_declaration_order(self) -> None:
        """Functions are returned in declaration order, not sorted."""
        source = "def test_z(): pass\ndef test_a(): pass\ndef test_m(): pass\n"
        assert discover_test_functions(source) == ("test_z", "test_a", "test_m")

    def test_with_typed_parameters(self) -> None:
        """Functions with typed parameters are discovered."""
        source = "def test_add(owned x: Int):\n    pass\n"
        assert discover_test_functions(source) == ("test_add",)


class TestFilePathToModulePath:
    def test_simple_path(self) -> None:
        """Single-level test file path."""
        assert file_path_to_module_path("tests/test_a.mojo") == "tests.test_a"

    def test_nested_path(self) -> None:
        """Multi-level test file path."""
        assert (
            file_path_to_module_path("tests/http/test_method.mojo")
            == "tests.http.test_method"
        )

    def test_deeply_nested(self) -> None:
        """Three levels of nesting."""
        assert (
            file_path_to_module_path("tests/a/b/test_c.mojo") == "tests.a.b.test_c"
        )
