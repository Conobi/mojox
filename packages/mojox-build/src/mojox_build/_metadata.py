"""Render PEP 621 / 643 METADATA and PEP 427 WHEEL files."""

from __future__ import annotations

from pathlib import Path

from ._config import ProjectMetadata


def _person(p: dict) -> tuple[str | None, str | None]:
    name = p.get("name", "").strip() or None
    email = p.get("email", "").strip() or None
    return name, email


def _render_person_line(p: dict, *, kind: str) -> str | None:
    name, email = _person(p)
    if email:
        rendered = f"{name} <{email}>" if name else email
        return f"{kind}-email: {rendered}"
    if name:
        return f"{kind}: {name}"
    return None


def render_metadata(
    project: ProjectMetadata,
    root: Path,
    license_relpaths: list[str],
) -> str:
    lines: list[str] = [
        "Metadata-Version: 2.4",
        f"Name: {project.name}",
        f"Version: {project.version}",
    ]
    if project.description:
        lines.append(f"Summary: {project.description}")
    if project.requires_python:
        lines.append(f"Requires-Python: {project.requires_python}")
    if project.license:
        lines.append(f"License-Expression: {project.license}")
    for rel in license_relpaths:
        lines.append(f"License-File: {rel}")

    for kw in project.keywords:
        lines.append(f"Keywords: {kw}")
    for cls in project.classifiers:
        lines.append(f"Classifier: {cls}")

    for person in project.authors:
        rendered = _render_person_line(person, kind="Author")
        if rendered:
            lines.append(rendered)
    for person in project.maintainers:
        rendered = _render_person_line(person, kind="Maintainer")
        if rendered:
            lines.append(rendered)

    for label, url in project.urls.items():
        lines.append(f"Project-URL: {label}, {url}")

    for dep in project.dependencies:
        lines.append(f"Requires-Dist: {dep}")
    for extra, deps in project.optional_dependencies.items():
        lines.append(f"Provides-Extra: {extra}")
        for dep in deps:
            lines.append(f"Requires-Dist: {dep} ; extra == '{extra}'")

    body = ""
    if project.readme:
        readme_path = root / project.readme
        if readme_path.is_file():
            body = readme_path.read_text(encoding="utf-8")
            lower = project.readme.lower()
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
    return (
        "Wheel-Version: 1.0\n"
        f"Generator: mojox-build {generator_version}\n"
        f"Root-Is-Purelib: {'true' if root_is_purelib else 'false'}\n"
        f"Tag: {tag}\n"
    )
