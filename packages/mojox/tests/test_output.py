"""Output rendering: format results for human consumption."""

from __future__ import annotations

import io
from pathlib import PurePosixPath

import pytest
from mojox.output import (
    describe_jobs,
    render_bundle_header,
    render_diagnostics,
    render_dry_run,
    render_final_output,
    render_outcome,
    render_parallelism,
    render_starting,
    render_summary,
)
from mojox.types import Outcome, OutcomeKind, OutputMode
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
            _outcome("a", OutcomeKind.PASS, 1.2),
            _outcome("b", OutcomeKind.PASS, 0.8),
        )
        buf = io.StringIO()
        render_summary(outcomes, stream=buf)
        output = buf.getvalue()
        assert "2 passed" in output
        assert "0 failed" in output or "failed" not in output

    def test_mixed_results(self):
        outcomes = (
            _outcome("a", OutcomeKind.PASS, 1.0),
            _outcome("b", OutcomeKind.FAIL, 2.0),
            _outcome("c", OutcomeKind.TIMEOUT, 300.0),
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
            _outcome("a", OutcomeKind.PASS, 1.5),
            _outcome("b", OutcomeKind.PASS, 2.5),
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
            target_id="test.mojo",
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
            target_id="test.mojo",
            timeout_s=300,
            outputs=(),
            depends_on=(),
        )
        buf = io.StringIO()
        render_dry_run((cmd,), stream=buf)
        output = buf.getvalue()
        assert "/workspace/project" in output


class TestRenderDiagnostics:
    def test_full_location(self):
        """Diagnostic with file, line, and column renders all three."""
        diag = Diagnostic(
            kind="error",
            message="type mismatch",
            file="test.mojo",
            line=5,
            column=10,
        )
        buf = io.StringIO()
        render_diagnostics((diag,), stream=buf)
        output = buf.getvalue()
        assert "test.mojo:5:10: error: type mismatch\n" == output

    def test_file_only(self):
        """Diagnostic with file but no line/column."""
        diag = Diagnostic(
            kind="warning",
            message="unused import",
            file="lib.mojo",
        )
        buf = io.StringIO()
        render_diagnostics((diag,), stream=buf)
        output = buf.getvalue()
        assert "lib.mojo: warning: unused import\n" == output

    def test_file_and_line_no_column(self):
        """Diagnostic with file and line but no column."""
        diag = Diagnostic(
            kind="error",
            message="syntax error",
            file="main.mojo",
            line=42,
        )
        buf = io.StringIO()
        render_diagnostics((diag,), stream=buf)
        output = buf.getvalue()
        assert "main.mojo:42: error: syntax error\n" == output

    def test_note_kind_prefix_suppressed(self):
        """Note-kind diagnostics do not get a 'note: ' prefix."""
        diag = Diagnostic(
            kind="note",
            message="see also: previous definition",
            file="test.mojo",
            line=1,
            column=1,
        )
        buf = io.StringIO()
        render_diagnostics((diag,), stream=buf)
        output = buf.getvalue()
        assert "test.mojo:1:1: see also: previous definition\n" == output
        assert "note:" not in output

    def test_error_kind_prefix_shown(self):
        """Error-kind diagnostics get an 'error: ' prefix."""
        diag = Diagnostic(
            kind="error",
            message="undeclared variable",
        )
        buf = io.StringIO()
        render_diagnostics((diag,), stream=buf)
        output = buf.getvalue()
        assert "error: undeclared variable\n" == output

    def test_warning_kind_prefix_shown(self):
        """Warning-kind diagnostics get a 'warning: ' prefix."""
        diag = Diagnostic(
            kind="warning",
            message="unused variable",
        )
        buf = io.StringIO()
        render_diagnostics((diag,), stream=buf)
        output = buf.getvalue()
        assert "warning: unused variable\n" == output

    def test_empty_diagnostics(self):
        """Empty diagnostics tuple produces no output."""
        buf = io.StringIO()
        render_diagnostics((), stream=buf)
        assert buf.getvalue() == ""


class TestRenderOutcomeVerbosity:
    """Tests for nextest-style output verbosity modes."""

    def test_pass_never_hides_output(self):
        outcome = _outcome("t", OutcomeKind.PASS, stdout="hello")
        buf = io.StringIO()
        render_outcome(outcome, stream=buf, success_output=OutputMode.NEVER, failure_output=OutputMode.IMMEDIATE)
        assert "hello" not in buf.getvalue()
        assert "PASS" in buf.getvalue()

    def test_pass_immediate_shows_output(self):
        outcome = _outcome("t", OutcomeKind.PASS, stdout="hello")
        buf = io.StringIO()
        render_outcome(outcome, stream=buf, success_output=OutputMode.IMMEDIATE, failure_output=OutputMode.IMMEDIATE)
        assert "hello" in buf.getvalue()

    def test_fail_immediate_shows_full_stderr(self):
        stderr = "\n".join(f"line{i}" for i in range(20))
        outcome = _outcome("t", OutcomeKind.FAIL, stderr=stderr)
        buf = io.StringIO()
        render_outcome(outcome, stream=buf, success_output=OutputMode.NEVER, failure_output=OutputMode.IMMEDIATE)
        assert "line19" in buf.getvalue()

    def test_fail_never_hides_output(self):
        outcome = _outcome("t", OutcomeKind.FAIL, stderr="error details")
        buf = io.StringIO()
        render_outcome(outcome, stream=buf, success_output=OutputMode.NEVER, failure_output=OutputMode.NEVER)
        assert "error details" not in buf.getvalue()
        assert "FAIL" in buf.getvalue()

    def test_fail_final_hides_inline_output(self):
        outcome = _outcome("t", OutcomeKind.FAIL, stderr="error details")
        buf = io.StringIO()
        render_outcome(outcome, stream=buf, success_output=OutputMode.NEVER, failure_output=OutputMode.FINAL)
        assert "error details" not in buf.getvalue()

    def test_skipped_renders_skip_label(self):
        outcome = _outcome("t", OutcomeKind.SKIPPED)
        buf = io.StringIO()
        render_outcome(outcome, stream=buf, success_output=OutputMode.NEVER, failure_output=OutputMode.IMMEDIATE)
        assert "SKIP" in buf.getvalue()

    def test_skipped_never_shows_output(self):
        outcome = _outcome("t", OutcomeKind.SKIPPED, stderr="cancelled")
        buf = io.StringIO()
        render_outcome(outcome, stream=buf, success_output=OutputMode.IMMEDIATE, failure_output=OutputMode.IMMEDIATE)
        assert "cancelled" not in buf.getvalue()


class TestRenderFinalOutput:
    """Tests for deferred output display."""

    def test_final_output_shows_deferred_failures(self):
        outcomes = (
            _outcome("t1", OutcomeKind.PASS),
            _outcome("t2", OutcomeKind.FAIL, stderr="the error"),
        )
        buf = io.StringIO()
        render_final_output(
            outcomes,
            stream=buf,
            success_output=OutputMode.NEVER,
            failure_output=OutputMode.FINAL,
        )
        assert "the error" in buf.getvalue()
        assert "t2" in buf.getvalue()

    def test_final_output_empty_when_mode_is_never(self):
        outcomes = (_outcome("t", OutcomeKind.FAIL, stderr="err"),)
        buf = io.StringIO()
        render_final_output(outcomes, stream=buf, success_output=OutputMode.NEVER, failure_output=OutputMode.NEVER)
        assert buf.getvalue() == ""

    def test_final_output_shows_success_when_mode_is_final(self):
        outcomes = (_outcome("t", OutcomeKind.PASS, stdout="output"),)
        buf = io.StringIO()
        render_final_output(outcomes, stream=buf, success_output=OutputMode.FINAL, failure_output=OutputMode.IMMEDIATE)
        assert "output" in buf.getvalue()

    def test_final_output_skips_skipped(self):
        outcomes = (_outcome("t", OutcomeKind.SKIPPED, stderr="skipped"),)
        buf = io.StringIO()
        render_final_output(outcomes, stream=buf, success_output=OutputMode.FINAL, failure_output=OutputMode.FINAL)
        assert buf.getvalue() == ""


class TestRenderSummaryWithSkipped:
    """Tests for skipped count in summary."""

    def test_skipped_count_in_summary(self):
        outcomes = (
            _outcome("t1", OutcomeKind.PASS),
            _outcome("t2", OutcomeKind.SKIPPED),
        )
        buf = io.StringIO()
        render_summary(outcomes, stream=buf)
        assert "1 passed" in buf.getvalue()
        assert "1 skipped" in buf.getvalue()


class TestParallelismHeader:
    def test_starting_reports_jobs_and_compile_threads(self):
        buf = io.StringIO()
        render_starting(7, jobs=2, compile_threads=1, stream=buf)
        assert buf.getvalue() == "Starting 7 targets (jobs 2, compile threads 1)\n"

    def test_starting_singular(self):
        buf = io.StringIO()
        render_starting(1, jobs=1, compile_threads=4, stream=buf)
        assert buf.getvalue() == "Starting 1 target (jobs 1, compile threads 4)\n"

    def test_starting_with_detail(self):
        buf = io.StringIO()
        render_starting(7, jobs=8, compile_threads=1, jobs_detail="num-cpus=8 via cgroup", stream=buf)
        assert buf.getvalue() == "Starting 7 targets (jobs 8 (num-cpus=8 via cgroup), compile threads 1)\n"

    def test_using_line(self):
        buf = io.StringIO()
        render_parallelism(jobs=6, compile_threads=2, jobs_detail="-2 of 8 via affinity", stream=buf)
        assert buf.getvalue() == "Using jobs 6 (-2 of 8 via affinity), compile threads 2\n"


class TestBundleHeader:
    def test_counts_origin_and_timeout(self):
        buf = io.StringIO()
        render_bundle_header(
            functions=12,
            examples=3,
            origin="--bundle",
            timeout_s=900,
            timeout_origin="/p/c.toml:timeout",
            stream=buf,
        )
        assert buf.getvalue() == (
            "bundle: 1 binary, 12 test functions, 3 examples (--bundle); "
            "timeout 900 s for the harness (/p/c.toml:timeout)\n"
        )

    def test_zero_counts_are_left_out_and_one_is_singular(self):
        buf = io.StringIO()
        render_bundle_header(
            functions=1,
            examples=0,
            origin="tool.mojox.test-bundle",
            timeout_s=300,
            timeout_origin="default",
            stream=buf,
        )
        assert buf.getvalue() == (
            "bundle: 1 binary, 1 test function (tool.mojox.test-bundle); timeout 300 s for the harness (default)\n"
        )


class TestDescribeJobs:
    @pytest.mark.parametrize(
        ("spec", "cpu_count", "source", "expected"),
        [
            ("num-cpus", 8, "cgroup", "num-cpus=8 via cgroup"),
            ("-2", 8, "affinity", "-2 of 8 via affinity"),
            ("-1", 4, "MOJOX_CPU_COUNT", "-1 of 4 via MOJOX_CPU_COUNT"),
            ("4", 8, "os", None),
            ("1", 1, "os", None),
        ],
    )
    def test_detail_only_for_cpu_relative_specs(self, spec, cpu_count, source, expected):
        assert describe_jobs(spec, cpu_count, source) == expected


class TestCompileKilledRendering:
    @staticmethod
    def _killed(sig: int = -9) -> Outcome:
        return _outcome(
            "lib",
            OutcomeKind.COMPILE_KILLED,
            exit_code=sig,
            stderr="mojox: compile killed by signal 9 (SIGKILL)",
            cmd_kind=CommandKind.COMPILE_PACKAGE,
        )

    def test_label_names_the_signal(self):
        buf = io.StringIO()
        render_outcome(self._killed(), stream=buf)
        assert buf.getvalue().startswith("KILLED (SIGKILL) ")
        assert "COMPILE ERROR" not in buf.getvalue()

    def test_final_output_label_names_the_signal(self):
        buf = io.StringIO()
        render_final_output((self._killed(),), stream=buf, failure_output=OutputMode.FINAL)
        assert "KILLED (SIGKILL) lib" in buf.getvalue()

    def test_summary_counts_killed(self):
        buf = io.StringIO()
        render_summary((self._killed(), _outcome("a", OutcomeKind.PASS)), stream=buf)
        assert "1 killed" in buf.getvalue()
        assert "compile error" not in buf.getvalue()
