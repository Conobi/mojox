"""Output rendering: format results for human consumption."""

from __future__ import annotations

import io
from pathlib import PurePosixPath

from mojox._output import render_summary, render_dry_run
from mojox._types import Outcome, OutcomeKind
from mojox_core import Command, CommandKind, Diagnostic


def _outcome(target_id: str, kind: OutcomeKind, elapsed: float = 1.0, **kw) -> Outcome:
    """Build a test outcome with defaults."""
    cmd = Command(
        argv=("/usr/bin/mojo", "run", "test.mojo"),
        cwd=PurePosixPath("/workspace/project"),
        env={"PATH": "/usr/bin"},
        kind=kw.get("cmd_kind", CommandKind.RUN_TEST),
        target_id=target_id,
        timeout_s=300,
        outputs=(),
        depends_on=(),
    )
    return Outcome(
        command=cmd,
        kind=kind,
        exit_code=kw.get("exit_code", 0 if kind == OutcomeKind.PASS else 1),
        stdout=kw.get("stdout", ""),
        stderr=kw.get("stderr", ""),
        diagnostics=kw.get("diagnostics", ()),
        elapsed_s=elapsed,
    )


class TestRenderSummary:
    def test_all_pass(self):
        outcomes = (
            _outcome("test::a", OutcomeKind.PASS, 1.2),
            _outcome("test::b", OutcomeKind.PASS, 0.8),
        )
        buf = io.StringIO()
        render_summary(outcomes, stream=buf)
        output = buf.getvalue()
        assert "2 passed" in output
        assert "0 failed" in output or "failed" not in output

    def test_mixed_results(self):
        outcomes = (
            _outcome("test::a", OutcomeKind.PASS, 1.0),
            _outcome("test::b", OutcomeKind.FAIL, 2.0),
            _outcome("test::c", OutcomeKind.TIMEOUT, 300.0),
        )
        buf = io.StringIO()
        render_summary(outcomes, stream=buf)
        output = buf.getvalue()
        assert "1 passed" in output
        assert "1 failed" in output
        assert "1 timed out" in output

    def test_empty_outcomes(self):
        buf = io.StringIO()
        render_summary((), stream=buf)
        output = buf.getvalue()
        assert "no targets" in output.lower() or "0" in output

    def test_total_time_reported(self):
        outcomes = (
            _outcome("test::a", OutcomeKind.PASS, 1.5),
            _outcome("test::b", OutcomeKind.PASS, 2.5),
        )
        buf = io.StringIO()
        render_summary(outcomes, stream=buf)
        output = buf.getvalue()
        assert "4.0" in output or "4.00" in output


class TestRenderDryRun:
    def test_dry_run_shows_argv(self):
        cmd = Command(
            argv=("/usr/bin/mojo", "run", "-O0", "test.mojo"),
            cwd=PurePosixPath("/workspace/project"),
            env={"PATH": "/usr/bin"},
            kind=CommandKind.RUN_TEST,
            target_id="test::test.mojo",
            timeout_s=300,
            outputs=(),
            depends_on=(),
        )
        buf = io.StringIO()
        render_dry_run((cmd,), stream=buf)
        output = buf.getvalue()
        assert "/usr/bin/mojo run -O0 test.mojo" in output

    def test_dry_run_shows_cwd(self):
        cmd = Command(
            argv=("/usr/bin/mojo", "run", "test.mojo"),
            cwd=PurePosixPath("/workspace/project"),
            env={"PATH": "/usr/bin"},
            kind=CommandKind.RUN_TEST,
            target_id="test::test.mojo",
            timeout_s=300,
            outputs=(),
            depends_on=(),
        )
        buf = io.StringIO()
        render_dry_run((cmd,), stream=buf)
        output = buf.getvalue()
        assert "/workspace/project" in output
