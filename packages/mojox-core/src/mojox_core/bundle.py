"""Bundle harness generator for single-binary test runners.

Discovers test functions from Mojo source strings and generates a
harness that imports and runs them with error isolation. This module
is pure: no I/O, no subprocess, no filesystem access.
"""

from __future__ import annotations

import re

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
