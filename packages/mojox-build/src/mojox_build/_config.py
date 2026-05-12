"""Parse pyproject.toml into typed dataclasses, validate required fields."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]


class BuildConfigError(RuntimeError):
    """User-facing configuration error (clean message, no Python stack)."""


@dataclass
class BackendConfig:
    package_root: str = "src"
    packages: list[str] | None = None
    native_libs: list[str] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    source_include: list[str] | None = None
    source_exclude: list[str] = field(default_factory=list)
    wheel_exclude: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "BackendConfig":
        return cls(
            package_root=d.get("package-root", "src"),
            packages=list(d["packages"]) if "packages" in d else None,
            native_libs=list(d.get("native-libs", [])),
            defines={str(k): str(v) for k, v in d.get("defines", {}).items()},
            flags=list(d.get("flags", [])),
            source_include=list(d["source-include"]) if "source-include" in d else None,
            source_exclude=list(d.get("source-exclude", [])),
            wheel_exclude=list(d.get("wheel-exclude", [])),
        )


@dataclass
class ProjectMetadata:
    name: str
    version: str
    description: str | None = None
    readme: str | None = None
    license: str | None = None
    license_files: list[str] = field(default_factory=list)
    requires_python: str | None = None
    keywords: list[str] = field(default_factory=list)
    authors: list[dict] = field(default_factory=list)
    maintainers: list[dict] = field(default_factory=list)
    urls: dict[str, str] = field(default_factory=dict)
    classifiers: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    optional_dependencies: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, project: dict) -> "ProjectMetadata":
        dynamic = set(project.get("dynamic", []))
        if "name" not in project:
            raise BuildConfigError("[project] is missing required field `name`.")
        if "version" not in project:
            if "version" in dynamic:
                raise BuildConfigError(
                    "[project] declares `version` as dynamic, which mojox-build does not "
                    "currently support — set project.version statically in pyproject.toml."
                )
            raise BuildConfigError("[project] is missing required field `version`.")

        license_str: str | None = None
        license_field = project.get("license")
        if isinstance(license_field, str):
            license_str = license_field
        elif isinstance(license_field, dict):
            license_str = license_field.get("text") or license_field.get("file")

        readme_field = project.get("readme")
        readme_str = readme_field if isinstance(readme_field, str) else None

        return cls(
            name=project["name"],
            version=project["version"],
            description=project.get("description"),
            readme=readme_str,
            license=license_str,
            license_files=list(project.get("license-files", [])),
            requires_python=project.get("requires-python"),
            keywords=list(project.get("keywords", [])),
            authors=list(project.get("authors", [])),
            maintainers=list(project.get("maintainers", [])),
            urls=dict(project.get("urls", {})),
            classifiers=list(project.get("classifiers", [])),
            dependencies=list(project.get("dependencies", [])),
            optional_dependencies={
                k: list(v) for k, v in project.get("optional-dependencies", {}).items()
            },
        )


def load(pyproject_path: Path) -> tuple[ProjectMetadata, BackendConfig]:
    if not pyproject_path.is_file():
        raise BuildConfigError(f"{pyproject_path} not found")
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    if "project" not in data:
        raise BuildConfigError(
            f"{pyproject_path}: missing [project] table (required by PEP 621)."
        )
    project = ProjectMetadata.from_dict(data["project"])
    backend = BackendConfig.from_dict(data.get("tool", {}).get("mojox-build", {}))
    return project, backend


def normalize_name(name: str) -> str:
    """PEP 503 / PEP 491 normalization for wheel filenames."""
    return name.lower().replace("-", "_").replace(".", "_")
