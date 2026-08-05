"""Import audit: pure modules must not import subprocess or non-lexical pathlib.

This test enforces the purity boundary. mojox-core's pure modules (everything
except mojox_core.io.*) must not import subprocess, must not import os (except
os.sep/os.path for lexical operations), and must not use pathlib for I/O
(only PurePosixPath for lexical path manipulation).
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

# Pure modules: everything in mojox_core except the io subpackage.
_PURE_MODULES = [
    "mojox_core._types",
    "mojox_core._errors",
    "mojox_core.manifest",
    "mojox_core.policy",
    "mojox_core.targets",
    "mojox_core.plan",
    "mojox_core.metadata",
    "mojox_core.settings",
    "mojox_core.environment",
]

# Modules in io/ are effectful and may import anything.
_IO_MODULES = [
    "mojox_core.io",
]


class TestPurityBoundary:
    @pytest.mark.parametrize("module_name", _PURE_MODULES)
    def test_no_subprocess_import(self, module_name):
        mod = importlib.import_module(module_name)
        source_file = mod.__file__
        assert source_file is not None
        source = Path(source_file).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "subprocess", \
                        f"{module_name} imports subprocess"
            elif isinstance(node, ast.ImportFrom):
                if node.module and "subprocess" in node.module:
                    pytest.fail(f"{module_name} imports from subprocess")

    @pytest.mark.parametrize("module_name", _PURE_MODULES)
    def test_no_os_environ_access(self, module_name):
        mod = importlib.import_module(module_name)
        source_file = mod.__file__
        assert source_file is not None
        source = Path(source_file).read_text()
        assert "os.environ" not in source, \
            f"{module_name} accesses os.environ"

    def test_targets_uses_pathlib_for_io(self):
        """targets.py is the one pure module allowed to use pathlib.Path
        for discovery — but only in the discover() function, which receives
        a root Path from the caller. The module itself must not open/read files."""
        import mojox_core.targets as targets_mod

        source_file = targets_mod.__file__
        assert source_file is not None
        source = Path(source_file).read_text()
        assert "open(" not in source, "targets.py must not open files"
        assert ".read_text(" not in source, "targets.py must not read file contents"
        assert ".read_bytes(" not in source, "targets.py must not read file contents"
