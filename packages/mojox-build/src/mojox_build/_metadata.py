"""Render PEP 621 / 643 METADATA and PEP 427 WHEEL files."""

from __future__ import annotations

from pathlib import Path

from mojox_core import Manifest


def _person(p: dict) -> tuple[str | None, str | None]:
    """Extract name and email from a person dict."""
    name = p.get("name", "").strip() or None
    email = p.get("email", "").strip() or None
    return name, email


def _render_person_line(p: dict, *, kind: str) -> str | None:
    """Render one Author/Maintainer metadata line."""
    name, email = _person(p)
    if email:
        rendered = f"{name} <{email}>" if name else email
        return f"{kind}-email: {rendered}"
    if name:
        return f"{kind}: {name}"
    return None


def render_metadata(
    manifest: Manifest,
    root: Path,
    license_relpaths: list[str],
    *,
    compiler_version: str | None = None,
) -> str:
    """Render PEP 621 / 643 METADATA content.

    When *compiler_version* is not None, a ``Requires-Dist: mojo-compiler==<version>``
    line is emitted so that uv's resolver enforces compiler compatibility.
    """
    lines: list[str] = [
        "Metadata-Version: 2.4",
        f"Name: {manifest.name}",
        f"Version: {manifest.version}",
    ]
    if manifest.description:
        lines.append(f"Summary: {manifest.description}")
    if manifest.requires_python:
        lines.append(f"Requires-Python: {manifest.requires_python}")
    if manifest.license_expr:
        lines.append(f"License-Expression: {manifest.license_expr}")
    for rel in license_relpaths:
        lines.append(f"License-File: {rel}")

    for kw in manifest.keywords:
        lines.append(f"Keywords: {kw}")
    for cls in manifest.classifiers:
        lines.append(f"Classifier: {cls}")

    for person in manifest.authors:
        rendered = _render_person_line(person, kind="Author")
        if rendered:
            lines.append(rendered)
    for person in manifest.maintainers:
        rendered = _render_person_line(person, kind="Maintainer")
        if rendered:
            lines.append(rendered)

    for label, url in manifest.urls.items():
        lines.append(f"Project-URL: {label}, {url}")

    for dep in manifest.dependencies:
        lines.append(f"Requires-Dist: {dep}")
    for extra, deps in manifest.optional_dependencies.items():
        lines.append(f"Provides-Extra: {extra}")
        for dep in deps:
            lines.append(f"Requires-Dist: {dep} ; extra == '{extra}'")

    if compiler_version is not None:
        has_mojo_compiler = any("mojo-compiler" in d for d in manifest.dependencies)
        if not has_mojo_compiler:
            lines.append(f"Requires-Dist: mojo-compiler=={compiler_version}")

    body = ""
    if manifest.readme:
        readme_path = root / manifest.readme
        if readme_path.is_file():
            body = readme_path.read_text(encoding="utf-8")
            lower = manifest.readme.lower()
            content_type = (
                "text/markdown"
                if lower.endswith(".md")
                else "text/x-rst"
                if lower.endswith(".rst")
                else "text/plain"
            )
            lines.append(f"Description-Content-Type: {content_type}")

    return "\n".join(lines) + "\n\n" + body


def render_wheel_file(
    *,
    tag: str,
    root_is_purelib: bool,
    generator_version: str,
) -> str:
    """Render PEP 427 WHEEL file content."""
    return (
        "Wheel-Version: 1.0\n"
        f"Generator: mojox-build {generator_version}\n"
        f"Root-Is-Purelib: {'true' if root_is_purelib else 'false'}\n"
        f"Tag: {tag}\n"
    )
