"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def minimal_pyproject() -> dict:
    """Minimal valid pyproject.toml as a parsed dict."""
    return {
        "project": {
            "name": "mylib",
            "version": "1.0.0",
        },
    }


@pytest.fixture
def full_pyproject() -> dict:
    """Full pyproject.toml with all mojox keys populated."""
    return {
        "project": {
            "name": "mylib",
            "version": "2.0.0",
            "description": "A test library",
            "readme": "README.md",
            "license": "MIT",
            "license-files": ["LICENSE"],
            "requires-python": ">=3.10",
            "dependencies": ["packaging>=23.0"],
            "keywords": ["mojo", "test"],
            "authors": [{"name": "Dev"}],
            "urls": {"Repository": "https://example.com"},
            "classifiers": ["Development Status :: 3 - Alpha"],
        },
        "tool": {
            "mojox": {
                "packages": ["src/mylib"],
                "package-root": "src",
                "binaries": [{"source": "main.mojo", "name": "myapp"}],
                "test-roots": ["tests", "integration"],
                "test-parallel": True,
                "defines": {"FAST": "true"},
                "flags": ["-Xlinker", "-rpath"],
                "optimize": 2,
                "debug-level": "full",
                "pre-build": [["make", "native"]],
                "native-libs": ["lib/libfoo.so"],
                "source-include": ["src/**/*.mojo", "pyproject.toml"],
                "source-exclude": ["src/**/*_test.mojo"],
                "wheel-exclude": ["*.pyc"],
                "rlib-seed": "examples/seed.mojo",
                "lints": {
                    "warnings": "error",
                    "missing-doc-strings": "warn",
                },
                "profile": {
                    "dist": {
                        "optimize": 3,
                        "defines": {"PROFILE_ACCEPT": "true"},
                    },
                },
            },
        },
    }
