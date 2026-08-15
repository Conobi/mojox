"""Parse Mojo compiler JSON diagnostic output into Diagnostic objects.

Tolerates objects without a ``kind`` key and surfaces unparseable lines
verbatim as note-level diagnostics. Empty lines are skipped.
"""

from __future__ import annotations

import json

from mojox_core import Diagnostic


def parse_diagnostics(text: str) -> tuple[Diagnostic, ...]:
    """Parse compiler output text into a tuple of Diagnostics.

    Each non-empty line is attempted as JSON first. If it parses and has
    a ``kind`` field, it becomes a structured diagnostic. Otherwise the
    raw line is surfaced verbatim as a note-level diagnostic.
    """
    if not text:
        return ()

    result: list[Diagnostic] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        diag = _try_parse_json_diagnostic(stripped)
        if diag is not None:
            result.append(diag)
        else:
            result.append(Diagnostic(kind="note", message=stripped))
    return tuple(result)


def _try_parse_json_diagnostic(line: str) -> Diagnostic | None:
    """Attempt to parse a single line as a JSON diagnostic.

    Returns a Diagnostic if the line is valid JSON with a ``kind`` field,
    otherwise returns None so the caller can surface it verbatim.
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(obj, dict):
        return None

    kind = obj.get("kind")
    if kind not in ("error", "warning", "note"):
        return None

    return Diagnostic(
        kind=kind,
        message=obj.get("message", ""),
        file=obj.get("file"),
        line=obj.get("line"),
        column=obj.get("column"),
        source_text=obj.get("source_text"),
    )
