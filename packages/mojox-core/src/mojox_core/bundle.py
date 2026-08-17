"""Bundle harness generator for single-binary test runners.

Discovers test functions from Mojo source strings and generates a
harness that imports and runs them with error isolation. All functions
are pure (no I/O) except :func:`has_boucle`, which probes include
paths for a ``boucle/`` directory.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_TEST_FUNC_RE = re.compile(r"^(?:def|fn)\s+(test_\w+)\s*\(", re.MULTILINE)


def discover_test_functions(source: str) -> tuple[str, ...]:
    """Find top-level ``test_*`` function names in a Mojo source string.

    Only functions defined at column 0 (no leading whitespace) are
    matched, excluding those nested inside structs or other blocks.
    Both ``def`` and ``fn`` declarations are recognized.

    Args:
        source: The full source text of a ``.mojo`` file.

    Returns:
        A tuple of function names in declaration order.
    """
    return tuple(m.group(1) for m in _TEST_FUNC_RE.finditer(source))


def file_path_to_module_path(file_path: str) -> str:
    """Convert a relative file path to a Mojo module path.

    Strips the ``.mojo`` extension and replaces path separators
    with dots.

    Args:
        file_path: Relative file path (e.g., ``tests/http/test_method.mojo``).

    Returns:
        Dot-separated module path (e.g., ``tests.http.test_method``).
    """
    return file_path.removesuffix(".mojo").replace("/", ".")


@dataclass(frozen=True)
class TestModule:
    """A test module with its discovered test functions.

    Attributes:
        module_path: Dot-separated Mojo module path
            (e.g., ``tests.http.test_method``).
        file_path: Original file path relative to project root
            (e.g., ``tests/http/test_method.mojo``).
        functions: Test function names in declaration order.
    """

    __test__ = False  # Prevent pytest collection

    module_path: str
    file_path: str
    functions: tuple[str, ...]


def make_alias(module_path: str, func_name: str) -> str:
    """Build the import alias for a test function.

    Replaces dots in the module path with underscores, then appends
    the function name after a double-underscore separator.

    Args:
        module_path: Dot-separated module path.
        func_name: Function name to alias.

    Returns:
        A collision-safe alias string.
    """
    return f"{module_path.replace('.', '_')}__{func_name}"


def generate_harness(modules: Sequence[TestModule]) -> str:
    """Generate a Mojo harness source that imports and runs all test functions.

    Every function is imported with a fully-qualified alias to avoid
    name collisions. Each call is wrapped in error handling that
    catches ``Error`` and records pass/fail per function. An optional
    command-line argument filters tests by name (case-insensitive
    substring match).

    Args:
        modules: Test modules with their discovered functions.

    Returns:
        Complete Mojo source code for the harness entry point.
    """
    lines: list[str] = []
    lines.append("from sys import argv")
    lines.append("")

    all_tests: list[tuple[str, str, str]] = []
    for mod in modules:
        for func in mod.functions:
            alias = make_alias(mod.module_path, func)
            lines.append(f"from {mod.module_path} import {func} as {alias}")
            all_tests.append((alias, mod.file_path, func))

    lines.append("")
    lines.append("")
    lines.append("def main() raises:")
    lines.append('    var filter_pattern = String("")')
    lines.append("    if len(argv()) > 1:")
    lines.append("        filter_pattern = str(argv()[1]).lower()")
    lines.append("")
    lines.append("    var passed: Int = 0")
    lines.append("    var failed: Int = 0")
    lines.append("    var skipped: Int = 0")

    for alias, file_path, func_name in all_tests:
        label = f"{file_path}::{func_name}"
        lines.append("")
        lines.append(
            f"    if len(filter_pattern) == 0"
            f' or String("{func_name}").lower().find(filter_pattern) != -1:'
        )
        lines.append("        try:")
        lines.append(f"            {alias}()")
        lines.append("            passed += 1")
        lines.append("        except e:")
        lines.append("            failed += 1")
        lines.append(f'            print("FAIL {label}:", e)')
        lines.append("    else:")
        lines.append("        skipped += 1")

    lines.append("")
    lines.append('    print("---")')
    lines.append(
        '    print(passed, "passed,", failed, "failed,", skipped, "skipped")'
    )
    lines.append("    if failed > 0:")
    lines.append('        raise Error(str(failed) + " test(s) failed")')
    lines.append("")

    return "\n".join(lines)


def has_boucle(include_paths: Sequence[str]) -> bool:
    """Check whether boucle is available in any include path.

    Checks for the presence of a ``boucle/`` directory in each
    include path, using directory existence only -- no Mojo import
    attempt is made.

    Args:
        include_paths: Resolved include directories to search.

    Returns:
        True if a ``boucle/`` directory was found.
    """
    return any((Path(p) / "boucle").is_dir() for p in include_paths)
