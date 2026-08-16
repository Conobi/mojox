"""Owned lints that the compiler cannot emit.

bare-assert-in-test-target
    Rejects bare ``assert`` and ``debug_assert`` in test files because
    their behaviour depends on the ASSERT define and they abort instead
    of raising.

path-source-in-published-manifest
    Reports ``[tool.uv.sources]`` entries with path overrides for
    declared published dependencies.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LintFinding:
    """A single lint finding with file, line, and message."""

    file: str
    line: int
    message: str


def lint_bare_assert(path: Path) -> list[LintFinding]:
    """Scan a Mojo test file for bare assert and debug_assert statements.

    Only operates on files named ``test_*.mojo``. Returns an empty list
    for non-test files.

    A bare ``assert`` compiles to ``debug_assert``, whose behaviour is
    governed by the ASSERT define. Under ``warn`` it prints and exits 0.
    Under ``all`` it aborts (not raises). The lint directs toward
    ``std.testing.assert_*`` which always raises.
    """
    if not path.name.startswith("test_") or not path.name.endswith(".mojo"):
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[LintFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()

        # Skip comments.
        if stripped.startswith("#"):
            continue

        # Match bare `assert <expr>` but not `assert_*` helpers.
        if re.match(r"\s*assert[\s(]", line) and not re.match(r"\s*assert_", line):
            findings.append(
                LintFinding(
                    file=str(path),
                    line=lineno,
                    message=(
                        "bare `assert` in test target compiles to `debug_assert`, "
                        "whose behaviour depends on the ASSERT define. "
                        "Use `std.testing.assert_true()` or "
                        "`std.testing.assert_equal()` instead."
                    ),
                )
            )
        elif re.match(r"\s*debug_assert\s*\(", line):
            findings.append(
                LintFinding(
                    file=str(path),
                    line=lineno,
                    message=(
                        "`debug_assert` in test target: behaviour depends on the "
                        "ASSERT define. Under `warn` it prints and exits 0; under "
                        "`all` it aborts (not raises). "
                        "Use `std.testing.assert_true()` instead."
                    ),
                )
            )

    return findings


def lint_path_source(pyproject_path: Path) -> list[LintFinding]:
    """Check pyproject.toml for ``[tool.uv.sources]`` with path entries.

    Only flags path overrides for packages that appear in the project's
    declared dependencies -- a path override for a dev-only tool that
    is not a published dependency is harmless.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []

    # Collect declared dependency names (normalised to lowercase).
    deps: set[str] = set()
    for dep_str in data.get("project", {}).get("dependencies", []):
        name = re.split(r"[><=!~\s\[;]", dep_str)[0].strip().lower()
        if name:
            deps.add(name)

    uv_sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    if not isinstance(uv_sources, dict):
        return []

    findings: list[LintFinding] = []
    for pkg_name, source_spec in uv_sources.items():
        if not isinstance(source_spec, dict):
            continue
        if "path" not in source_spec:
            continue
        if pkg_name.lower() not in deps:
            continue
        findings.append(
            LintFinding(
                file=str(pyproject_path),
                line=0,
                message=(
                    f"path-source-in-published-manifest: `{pkg_name}` is a "
                    f"declared dependency with a path override "
                    f"(`{source_spec['path']}`). This makes the project "
                    "unbuildable when consumed as a git dependency -- uv "
                    "resolves the path relative to its cache, where it "
                    "does not exist."
                ),
            )
        )

    return findings
