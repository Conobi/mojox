"""JSON diagnostic parser: Mojo compiler output → Diagnostic tuples."""

from __future__ import annotations

from mojox._diagnostics import parse_diagnostics


class TestParseDiagnostics:
    def test_empty_input(self):
        assert parse_diagnostics("") == ()

    def test_valid_json_error(self):
        line = '{"kind":"error","message":"type mismatch","file":"test.mojo","line":5,"column":10}'
        result = parse_diagnostics(line)
        assert len(result) == 1
        assert result[0].kind == "error"
        assert result[0].message == "type mismatch"
        assert result[0].file == "test.mojo"
        assert result[0].line == 5
        assert result[0].column == 10

    def test_valid_json_without_location(self):
        line = '{"kind":"error","message":"failed to parse the provided Mojo source module"}'
        result = parse_diagnostics(line)
        assert len(result) == 1
        assert result[0].kind == "error"
        assert result[0].message == "failed to parse the provided Mojo source module"
        assert result[0].file is None
        assert result[0].line is None

    def test_multiple_lines(self):
        text = '{"kind":"warning","message":"unused variable"}\n{"kind":"error","message":"type mismatch"}\n'
        result = parse_diagnostics(text)
        assert len(result) == 2
        assert result[0].kind == "warning"
        assert result[1].kind == "error"

    def test_unparseable_line_surfaced_verbatim(self):
        text = "error: something went wrong\n"
        result = parse_diagnostics(text)
        assert len(result) == 1
        assert result[0].kind == "note"
        assert result[0].message == "error: something went wrong"

    def test_mixed_json_and_text(self):
        text = 'Compiling test.mojo...\n{"kind":"error","message":"type mismatch"}\nBuild failed.\n'
        result = parse_diagnostics(text)
        assert len(result) == 3
        assert result[0].message == "Compiling test.mojo..."
        assert result[1].kind == "error"
        assert result[1].message == "type mismatch"
        assert result[2].message == "Build failed."

    def test_empty_lines_skipped(self):
        text = "\n\n"
        assert parse_diagnostics(text) == ()

    def test_json_without_kind_field(self):
        line = '{"message":"some info","severity":"high"}'
        result = parse_diagnostics(line)
        assert len(result) == 1
        assert result[0].kind == "note"
        assert result[0].message == '{"message":"some info","severity":"high"}'

    def test_source_text_preserved(self):
        line = '{"kind":"error","message":"bad","file":"t.mojo","line":1,"column":1,"source_text":"let x = bad"}'
        result = parse_diagnostics(line)
        assert result[0].source_text == "let x = bad"

    def test_non_dict_json_surfaced_verbatim(self):
        """Valid JSON that is not a dict is surfaced verbatim as a note."""
        result = parse_diagnostics("[1, 2, 3]\n")
        assert len(result) == 1
        assert result[0].kind == "note"
        assert result[0].message == "[1, 2, 3]"
