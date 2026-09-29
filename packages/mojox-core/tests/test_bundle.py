"""Tests for bundle harness generator."""

from __future__ import annotations

from mojox_core.bundle import (
    TestModule,
    discover_test_functions,
    file_path_to_module_path,
    generate_harness,
    has_boucle,
    make_alias,
)


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
        assert file_path_to_module_path("tests/http/test_method.mojo") == "tests.http.test_method"

    def test_deeply_nested(self) -> None:
        """Three levels of nesting."""
        assert file_path_to_module_path("tests/a/b/test_c.mojo") == "tests.a.b.test_c"


class TestMakeAlias:
    def test_simple_module(self) -> None:
        """Single-level module produces underscored alias."""
        assert make_alias("tests.test_a", "test_foo") == "tests_test_a__test_foo"

    def test_nested_module(self) -> None:
        """Multi-level module dots become underscores."""
        assert make_alias("tests.http.test_method", "test_get") == "tests_http_test_method__test_get"


class TestTestModule:
    def test_frozen_dataclass(self) -> None:
        """TestModule is a frozen dataclass with the expected fields."""
        m = TestModule(
            module_path="tests.test_a",
            file_path="tests/test_a.mojo",
            functions=("test_foo", "test_bar"),
        )
        assert m.module_path == "tests.test_a"
        assert m.file_path == "tests/test_a.mojo"
        assert m.functions == ("test_foo", "test_bar")


class TestGenerateHarness:
    def test_single_module_single_function(self) -> None:
        """Harness with one module and one function has all required parts."""
        modules = [TestModule("tests.test_a", "tests/test_a.mojo", ("test_foo",))]
        source = generate_harness(modules)
        assert "from tests.test_a import test_foo as tests_test_a__test_foo" in source
        assert "tests_test_a__test_foo()" in source
        assert "def main()" in source

    def test_multiple_modules_multiple_functions(self) -> None:
        """Multiple modules produce distinct aliased imports."""
        modules = [
            TestModule("tests.test_a", "tests/test_a.mojo", ("test_foo", "test_bar")),
            TestModule(
                "tests.http.test_method",
                "tests/http/test_method.mojo",
                ("test_get",),
            ),
        ]
        source = generate_harness(modules)
        assert "from tests.test_a import test_foo as tests_test_a__test_foo" in source
        assert "from tests.test_a import test_bar as tests_test_a__test_bar" in source
        assert "from tests.http.test_method import test_get as tests_http_test_method__test_get" in source

    def test_collision_safe_aliases(self) -> None:
        """Same function name in different modules produces distinct aliases."""
        modules = [
            TestModule(
                "tests.http.test_method",
                "tests/http/test_method.mojo",
                ("test_init",),
            ),
            TestModule(
                "tests.dns.test_resolver",
                "tests/dns/test_resolver.mojo",
                ("test_init",),
            ),
        ]
        source = generate_harness(modules)
        assert "tests_http_test_method__test_init" in source
        assert "tests_dns_test_resolver__test_init" in source
        import_lines = [l for l in source.splitlines() if l.startswith("from ")]
        aliases = [l.split(" as ")[-1] for l in import_lines]
        assert len(aliases) == len(set(aliases))

    def test_filter_logic_present(self) -> None:
        """Harness contains argv-based filter infrastructure."""
        modules = [TestModule("tests.test_a", "tests/test_a.mojo", ("test_foo",))]
        source = generate_harness(modules)
        assert "filter_pattern" in source
        assert "argv" in source

    def test_error_handling_wraps_each_call(self) -> None:
        """Each test call is wrapped in try/except."""
        modules = [TestModule("tests.test_a", "tests/test_a.mojo", ("test_foo",))]
        source = generate_harness(modules)
        assert "try:" in source
        assert "except" in source
        assert "FAIL" in source

    def test_summary_with_counters(self) -> None:
        """Harness prints pass/fail/skip counters."""
        modules = [TestModule("tests.test_a", "tests/test_a.mojo", ("test_foo",))]
        source = generate_harness(modules)
        assert "passed" in source
        assert "failed" in source
        assert "skipped" in source

    def test_raises_on_failure(self) -> None:
        """Harness raises Error when any test fails."""
        modules = [TestModule("tests.test_a", "tests/test_a.mojo", ("test_foo",))]
        source = generate_harness(modules)
        assert "raise" in source.lower() or "Error" in source

    def test_empty_modules_produces_valid_harness(self) -> None:
        """An empty module list still produces a valid harness with main."""
        source = generate_harness([])
        assert "def main()" in source

    def test_label_includes_file_and_function(self) -> None:
        """FAIL label includes both file path and function name."""
        modules = [TestModule("tests.test_a", "tests/test_a.mojo", ("test_foo",))]
        source = generate_harness(modules)
        assert "tests/test_a.mojo::test_foo" in source


class TestHasBoucle:
    def test_found_when_boucle_dir_exists(self, tmp_path: object) -> None:
        """Returns True when a boucle/ directory exists in an include path."""
        from pathlib import Path

        path = Path(str(tmp_path)) / "packages"
        (path / "boucle").mkdir(parents=True)
        assert has_boucle((str(path),)) is True

    def test_not_found_when_absent(self, tmp_path: object) -> None:
        """Returns False when no include path contains boucle/."""
        from pathlib import Path

        path = Path(str(tmp_path)) / "packages"
        path.mkdir()
        assert has_boucle((str(path),)) is False

    def test_empty_include_paths(self) -> None:
        """Returns False with no include paths."""
        assert has_boucle(()) is False
