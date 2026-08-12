"""Shared test fixtures for the mojox CLI package."""

from __future__ import annotations

import sys
from pathlib import PurePosixPath

import pytest

from mojox_core import Command, CommandKind


@pytest.fixture
def sample_test_command() -> Command:
    """A sample test command for use in exec tests."""
    return Command(
        argv=(sys.executable, "-c", "print('hello')"),
        cwd=PurePosixPath("/workspace/project"),
        env={"PATH": "/usr/bin", "HOME": ""},
        kind=CommandKind.RUN_TEST,
        target_id="tests/test_a.mojo",
        timeout_s=300,
        outputs=(),
        depends_on=(),
    )


@pytest.fixture
def sample_failing_command() -> Command:
    """A command that exits with code 1."""
    return Command(
        argv=(sys.executable, "-c", "import sys; sys.exit(1)"),
        cwd=PurePosixPath("/workspace/project"),
        env={"PATH": "/usr/bin", "HOME": ""},
        kind=CommandKind.RUN_TEST,
        target_id="tests/test_fail.mojo",
        timeout_s=300,
        outputs=(),
        depends_on=(),
    )
