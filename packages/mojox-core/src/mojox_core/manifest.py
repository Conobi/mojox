"""Parse a pyproject.toml dict into a frozen Manifest.

This module is pure -- it receives an already-parsed dict and returns data.
The IO reader that loads the TOML bytes lives in mojox_core.io.manifest.
"""

from __future__ import annotations

import posixpath
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any, Literal

from .errors import ConfigError
from .options import OPTIONS, Layer, Option, origin_under, parse_table
from .policy import check_profile_name
from .types import BinaryEntry, Manifest, Profile


def parse_manifest(
    data: dict[str, Any],
    *,
    unknown_keys: Literal["error", "warn"] = "error",
    options: Sequence[Option] = OPTIONS,
) -> Manifest:
    """Parse a pyproject.toml dict into a frozen Manifest.

    Option keys of ``[tool.mojox]`` and its profiles go through the registry.
    A key that is neither an option nor structural raises in ``"error"`` mode;
    in ``"warn"`` mode it lands in ``Manifest.warnings`` so an older build
    backend can still build a project that uses a newer key. Every rejection
    is a ConfigError naming the key's origin.
    """
    project = _require_table(data, "project")
    mojox = _optional_table(_optional_table(data, "tool", "tool"), "mojox", "tool.mojox")

    name = _require_str(project, "project", "name")
    version = _parse_version(project)
    unknown: list[ConfigError] = []
    values = parse_table(mojox, Layer.TOP, origin_under("tool.mojox"), unknown=unknown, options=options)
    profiles = _parse_profiles(_optional_table(mojox, "profile", "tool.mojox.profile"), options, unknown)
    if unknown and unknown_keys == "error":
        raise unknown[0]

    return Manifest(
        name=name,
        version=version,
        description=project.get("description"),
        readme=project.get("readme") if isinstance(project.get("readme"), str) else None,
        license_expr=_parse_license(project),
        license_files=_project_str_list(project, "license-files"),
        requires_python=project.get("requires-python"),
        dependencies=_project_str_list(project, "dependencies"),
        optional_dependencies=_parse_optional_dependencies(project),
        keywords=_project_str_list(project, "keywords"),
        authors=_parse_people(project, "authors"),
        maintainers=_parse_people(project, "maintainers"),
        urls=_parse_urls(project),
        classifiers=_project_str_list(project, "classifiers"),
        packages=_parse_packages(mojox),
        package_root=_normalise_path(
            str(mojox.get("package-root", "src")),
            "tool.mojox.package-root",
        ),
        binaries=_parse_binaries(_optional_list(mojox, "binaries", "tool.mojox.binaries")),
        test_roots=_parse_path_list(mojox, "test-roots", ("tests",)),
        pre_build=_parse_pre_build(_optional_list(mojox, "pre-build", "tool.mojox.pre-build")),
        native_libs=_parse_path_list(mojox, "native-libs", ()),
        source_include=_parse_path_list_optional(mojox, "source-include"),
        source_exclude=_parse_path_list(mojox, "source-exclude", ()),
        wheel_exclude=_parse_str_list(mojox, "wheel-exclude"),
        profiles=profiles,
        build_profile=_parse_build_profile(mojox, profiles),
        values=values,
        warnings=tuple(str(err) for err in unknown),
    )


# -- Helpers ---------------------------------------------------------------


def _require_table(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Require *key* to exist in *data* and be a dict (TOML table)."""
    if key not in data:
        raise ConfigError(key, f"missing [{key}] table")
    val = data[key]
    if not isinstance(val, dict):
        raise ConfigError(key, f"expected a table, got {type(val).__name__}")
    return val


def _optional_table(data: dict[str, Any], key: str, key_path: str) -> dict[str, Any]:
    """Return the table at *key*, empty when absent; any non-table value is rejected."""
    val = data.get(key, {})
    if not isinstance(val, dict):
        raise ConfigError(key_path, f"expected a table, got {type(val).__name__}")
    return val


def _optional_list(data: dict[str, Any], key: str, key_path: str) -> list[Any]:
    """Return the array at *key*, empty when absent; items are left to the caller."""
    val = data.get(key, [])
    if not isinstance(val, list):
        raise ConfigError(key_path, f"expected a list, got {type(val).__name__}")
    return val


def _require_str_list(raw: object, key_path: str) -> tuple[str, ...]:
    """Require *raw* to be an array of strings; a bare string is not accepted."""
    if not isinstance(raw, list):
        raise ConfigError(key_path, f"expected a list, got {type(raw).__name__}")
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            raise ConfigError(f"{key_path}[{i}]", f"expected a string, got {type(item).__name__}")
    return tuple(raw)


def _project_str_list(project: dict[str, Any], key: str) -> tuple[str, ...]:
    """Parse an optional ``[project]`` array of strings."""
    return _require_str_list(project.get(key, []), f"project.{key}")


def _parse_optional_dependencies(project: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Parse ``project.optional-dependencies``: a table of extra name to requirement strings."""
    table = _optional_table(project, "optional-dependencies", "project.optional-dependencies")
    return {k: _require_str_list(v, f"project.optional-dependencies.{k}") for k, v in table.items()}


def _parse_people(project: dict[str, Any], key: str) -> tuple[dict[str, str], ...]:
    """Parse ``project.authors``/``maintainers``: an array of tables (``name``, ``email``)."""
    people = _optional_list(project, key, f"project.{key}")
    for i, person in enumerate(people):
        if not isinstance(person, dict):
            raise ConfigError(f"project.{key}[{i}]", f"expected a table, got {type(person).__name__}")
    return tuple(people)


def _parse_urls(project: dict[str, Any]) -> dict[str, str]:
    """Parse ``project.urls``: a table of label to URL string."""
    table = _optional_table(project, "urls", "project.urls")
    for label, url in table.items():
        if not isinstance(url, str):
            raise ConfigError(f"project.urls.{label}", f"expected a string, got {type(url).__name__}")
    return dict(table)


def _require_str(table: dict[str, Any], prefix: str, key: str) -> str:
    """Require *key* to exist in *table* and be a string."""
    if key not in table:
        raise ConfigError(f"{prefix}.{key}", "required field is missing")
    val = table[key]
    if not isinstance(val, str):
        raise ConfigError(f"{prefix}.{key}", f"expected a string, got {type(val).__name__}")
    return val


def _parse_version(project: dict[str, Any]) -> str:
    """Extract and validate the project version.

    Rejects dynamic versions since mojox requires a static version string.
    """
    if "version" not in project:
        dynamic = set(_project_str_list(project, "dynamic"))
        if "version" in dynamic:
            raise ConfigError(
                "project.version",
                "declares `version` as dynamic, which mojox does not support -- "
                "set project.version statically in pyproject.toml",
            )
        raise ConfigError("project.version", "required field is missing")
    return str(project["version"])


def _parse_license(project: dict[str, Any]) -> str | None:
    """Parse the license field, handling both string and table forms."""
    lic = project.get("license")
    if lic is None:
        return None
    if isinstance(lic, str):
        return lic
    if isinstance(lic, dict):
        return lic.get("text") or lic.get("file")
    return None


def _normalise_path(raw: str, key_path: str) -> str:
    """Normalise a manifest path: must be relative, lexically cleaned."""
    normalised = posixpath.normpath(raw)
    if PurePosixPath(normalised).is_absolute():
        raise ConfigError(
            key_path,
            f"absolute paths are not allowed in manifests, got {raw!r}. Use a path relative to the project root.",
        )
    return normalised


def _parse_str_list(mojox: dict[str, Any], key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Parse a key that must be a list of strings."""
    if key not in mojox:
        return default
    raw = mojox[key]
    if not isinstance(raw, list):
        raise ConfigError(f"tool.mojox.{key}", f"expected a list, got {type(raw).__name__}")
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            raise ConfigError(f"tool.mojox.{key}[{i}]", f"expected a string, got {type(item).__name__}")
    return tuple(raw)


def _parse_path_list(mojox: dict[str, Any], key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Parse a key that must be a list of relative path strings."""
    if key not in mojox:
        return default
    raw = mojox[key]
    if not isinstance(raw, list):
        raise ConfigError(f"tool.mojox.{key}", f"expected a list, got {type(raw).__name__}")
    for i, p in enumerate(raw):
        if not isinstance(p, str):
            raise ConfigError(f"tool.mojox.{key}[{i}]", f"expected a string, got {type(p).__name__}")
    return tuple(_normalise_path(p, f"tool.mojox.{key}") for p in raw)


def _parse_path_list_optional(mojox: dict[str, Any], key: str) -> tuple[str, ...] | None:
    """Parse an optional path list — None if absent, validated if present."""
    if key not in mojox:
        return None
    return _parse_path_list(mojox, key)


def _parse_packages(mojox: dict[str, Any]) -> tuple[str, ...] | None:
    """Parse the packages list, normalising each path."""
    if "packages" not in mojox:
        return None
    raw = mojox["packages"]
    if not isinstance(raw, list):
        raise ConfigError("tool.mojox.packages", f"expected a list, got {type(raw).__name__}")
    return tuple(_normalise_path(str(p), "tool.mojox.packages") for p in raw)


def _parse_binaries(items: list[Any]) -> tuple[BinaryEntry, ...]:
    """Parse the binaries list, validating each entry for uniqueness."""
    out: list[BinaryEntry] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        key = f"tool.mojox.binaries[{i}]"
        if isinstance(item, str):
            source = item
            name = PurePosixPath(item).stem
        elif isinstance(item, dict):
            if "source" not in item or "name" not in item:
                raise ConfigError(key, "table form requires both `source` and `name` keys")
            source = str(item["source"])
            name = str(item["name"])
        else:
            raise ConfigError(key, f"expected a string or table, got {type(item).__name__}")
        source = _normalise_path(source, key)
        if not source:
            raise ConfigError(key, "empty `source`")
        if not name:
            raise ConfigError(key, "empty `name`")
        if "/" in name or "\\" in name:
            raise ConfigError(key, f"`name` must be a bare filename, got {name!r}")
        if name in seen:
            raise ConfigError(key, f"duplicate binary name {name!r}")
        seen.add(name)
        out.append(BinaryEntry(source=source, name=name))
    return tuple(out)


def _parse_pre_build(items: list[Any]) -> tuple[tuple[str, ...], ...]:
    """Parse pre-build commands (shell strings or argv lists)."""
    out: list[tuple[str, ...]] = []
    for i, item in enumerate(items):
        key = f"tool.mojox.pre-build[{i}]"
        if isinstance(item, str):
            out.append(("sh", "-c", item))
        elif isinstance(item, list) and all(isinstance(x, str) for x in item):
            out.append(tuple(item))
        else:
            raise ConfigError(
                key,
                "each entry must be a string (shell command) or a list of strings (argv)",
            )
    return tuple(out)


def _parse_build_profile(mojox: dict[str, Any], profiles: dict[str, Profile]) -> str:
    """Parse the build-profile key, defaulting to 'release'.

    The name must be a built-in or one of *profiles*, so a bad value is
    reported against this key rather than as an unknown ``--profile``.
    """
    raw = mojox.get("build-profile", "release")
    if not isinstance(raw, str):
        raise ConfigError(
            "tool.mojox.build-profile",
            f"expected a string, got {type(raw).__name__}",
        )
    check_profile_name(raw, profiles, "tool.mojox.build-profile")
    return raw


def _parse_profiles(raw: dict[str, Any], options: Sequence[Option], unknown: list[ConfigError]) -> dict[str, Profile]:
    """Parse each ``[tool.mojox.profile.<name>]`` table into the option values it sets."""
    profiles: dict[str, Profile] = {}
    for name, table in raw.items():
        prefix = f"tool.mojox.profile.{name}"
        if not isinstance(table, dict):
            raise ConfigError(prefix, f"expected a table, got {type(table).__name__}")
        values = parse_table(table, Layer.PROFILE, origin_under(prefix), unknown=unknown, options=options)
        profiles[name] = Profile(values=values)
    return profiles
