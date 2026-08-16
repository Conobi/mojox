"""Parse a pyproject.toml dict into a frozen Manifest.

This module is pure -- it receives an already-parsed dict and returns data.
The IO reader that loads the TOML bytes lives in mojox_core.io.manifest.
"""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from typing import Any

from .errors import ConfigError
from .types import BinaryEntry, LintConfig, Manifest, Profile

_VALID_OPTIMIZE = frozenset({0, 1, 2, 3})
_VALID_DEBUG_LEVELS = frozenset({"none", "line-tables", "full"})


def parse_manifest(data: dict[str, Any]) -> Manifest:
    """Parse a pyproject.toml dict into a frozen Manifest.

    Every rejection is a ConfigError with key path and remediation.
    No KeyError, no TypeError, no stack trace reaches the user.
    """
    project = _require_table(data, "project")
    mojox = data.get("tool", {}).get("mojox", {})

    name = _require_str(project, "project", "name")
    version = _parse_version(project)

    return Manifest(
        name=name,
        version=version,
        description=project.get("description"),
        readme=project.get("readme") if isinstance(project.get("readme"), str) else None,
        license_expr=_parse_license(project),
        license_files=tuple(project.get("license-files", ())),
        requires_python=project.get("requires-python"),
        dependencies=tuple(project.get("dependencies", ())),
        optional_dependencies={k: tuple(v) for k, v in project.get("optional-dependencies", {}).items()},
        keywords=tuple(project.get("keywords", ())),
        authors=tuple(project.get("authors", ())),
        maintainers=tuple(project.get("maintainers", ())),
        urls=dict(project.get("urls", {})),
        classifiers=tuple(project.get("classifiers", ())),
        packages=_parse_packages(mojox),
        package_root=_normalise_path(
            str(mojox.get("package-root", "src")),
            "tool.mojox.package-root",
        ),
        binaries=_parse_binaries(mojox.get("binaries", [])),
        test_roots=_parse_path_list(mojox, "test-roots", ("tests",)),
        test_parallel=bool(mojox.get("test-parallel", False)),
        defines=_parse_defines(mojox),
        flags=_parse_str_list(mojox, "flags"),
        lints=_parse_lints(mojox.get("lints", {})),
        optimize=_parse_optimize(mojox.get("optimize"), "tool.mojox.optimize"),
        debug_level=_parse_debug_level(mojox.get("debug-level"), "tool.mojox.debug-level"),
        pre_build=_parse_pre_build(mojox.get("pre-build", [])),
        native_libs=_parse_path_list(mojox, "native-libs", ()),
        source_include=_parse_path_list_optional(mojox, "source-include"),
        source_exclude=_parse_path_list(mojox, "source-exclude", ()),
        wheel_exclude=_parse_str_list(mojox, "wheel-exclude"),
        profiles=_parse_profiles(mojox.get("profile", {})),
        build_profile=_parse_build_profile(mojox),
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
        dynamic = set(project.get("dynamic", []))
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


def _parse_optional_path(mojox: dict[str, Any], key: str) -> str | None:
    """Parse an optional single path string."""
    raw = mojox.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ConfigError(f"tool.mojox.{key}", f"expected a string, got {type(raw).__name__}")
    return _normalise_path(raw, f"tool.mojox.{key}")


def _parse_defines(mojox: dict[str, Any]) -> dict[str, str]:
    """Parse defines, validating the value is a table."""
    raw = mojox.get("defines", {})
    if not isinstance(raw, dict):
        raise ConfigError("tool.mojox.defines", f"expected a table, got {type(raw).__name__}")
    return {str(k): str(v) for k, v in raw.items()}


def _parse_packages(mojox: dict[str, Any]) -> tuple[str, ...] | None:
    """Parse the packages list, normalising each path."""
    if "packages" not in mojox:
        return None
    raw = mojox["packages"]
    if not isinstance(raw, list):
        raise ConfigError("tool.mojox.packages", f"expected a list, got {type(raw).__name__}")
    return tuple(_normalise_path(str(p), "tool.mojox.packages") for p in raw)


def _parse_optimize(value: object, key_path: str) -> int | None:
    """Parse and validate the optimisation level (0--3)."""
    if value is None:
        return None
    if isinstance(value, int):
        level = value
    elif isinstance(value, (str, float)):
        try:
            level = int(value)
        except (ValueError, TypeError):
            raise ConfigError(key_path, f"must be 0–3, got {value!r}")
    else:
        raise ConfigError(key_path, f"must be 0–3, got {value!r}")
    if level not in _VALID_OPTIMIZE:
        raise ConfigError(key_path, f"must be 0–3, got {value!r}")
    return level


def _parse_debug_level(value: object, key_path: str) -> str | None:
    """Parse and validate the debug level."""
    if value is None:
        return None
    s = str(value)
    if s not in _VALID_DEBUG_LEVELS:
        raise ConfigError(
            key_path,
            f"must be one of {sorted(_VALID_DEBUG_LEVELS)}, got {value!r}",
        )
    return s


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


def _parse_lints(raw: object) -> LintConfig:
    """Parse the lints table into a LintConfig."""
    if not isinstance(raw, dict):
        raise ConfigError("tool.mojox.lints", f"expected a table, got {type(raw).__name__}")
    _VALID_WARNINGS = frozenset({"error"})
    _VALID_LINT_LEVELS = frozenset({"warn"})
    if "warnings" in raw and raw["warnings"] not in _VALID_WARNINGS:
        raise ConfigError(
            "tool.mojox.lints.warnings",
            f"must be 'error', got {raw['warnings']!r}",
        )
    _VALID_CHECK_LEVELS = frozenset({"check"})
    if "check-doc-strings" in raw and raw["check-doc-strings"] not in _VALID_CHECK_LEVELS:
        raise ConfigError(
            "tool.mojox.lints.check-doc-strings",
            f"must be 'check', got {raw['check-doc-strings']!r}",
        )
    if "missing-doc-strings" in raw and raw["missing-doc-strings"] not in _VALID_LINT_LEVELS:
        raise ConfigError(
            "tool.mojox.lints.missing-doc-strings",
            f"must be 'warn', got {raw['missing-doc-strings']!r}",
        )
    if "unstable-apis" in raw and raw["unstable-apis"] not in _VALID_LINT_LEVELS:
        raise ConfigError(
            "tool.mojox.lints.unstable-apis",
            f"must be 'warn', got {raw['unstable-apis']!r}",
        )
    return LintConfig(
        warnings_as_errors=raw.get("warnings") == "error",
        check_doc_strings=raw.get("check-doc-strings") == "check",
        missing_doc_strings=raw.get("missing-doc-strings") == "warn",
        unstable_apis=raw.get("unstable-apis") == "warn",
    )


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


def _parse_defines_for_key(table: dict[str, Any], key_path: str) -> dict[str, str]:
    """Parse a defines sub-table, validating it is a dict of strings."""
    raw = table.get("defines", {})
    if not isinstance(raw, dict):
        raise ConfigError(key_path, f"expected a table, got {type(raw).__name__}")
    return {str(k): str(v) for k, v in raw.items()}


def _parse_str_list_for_key(table: dict[str, Any], key: str, key_path: str) -> tuple[str, ...]:
    """Parse a string-list value from *table[key]*, raising on bad types."""
    raw = table.get(key, ())
    if isinstance(raw, str):
        raise ConfigError(key_path, "expected a list, got string")
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(key_path, f"expected a list, got {type(raw).__name__}")
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            raise ConfigError(f"{key_path}[{i}]", f"expected a string, got {type(item).__name__}")
    return tuple(raw)


def _parse_build_profile(mojox: dict[str, Any]) -> str:
    """Parse the build-profile key, defaulting to 'release'."""
    raw = mojox.get("build-profile", "release")
    if not isinstance(raw, str):
        raise ConfigError(
            "tool.mojox.build-profile",
            f"expected a string, got {type(raw).__name__}",
        )
    return raw


def _parse_profiles(raw: dict[str, Any]) -> dict[str, Profile]:
    """Parse profile tables into Profile objects."""
    profiles: dict[str, Profile] = {}
    for name, table in raw.items():
        if not isinstance(table, dict):
            raise ConfigError(
                f"tool.mojox.profile.{name}",
                f"expected a table, got {type(table).__name__}",
            )
        profiles[name] = Profile(
            optimize=_parse_optimize(table.get("optimize"), f"tool.mojox.profile.{name}.optimize"),
            debug_level=_parse_debug_level(table.get("debug-level"), f"tool.mojox.profile.{name}.debug-level"),
            defines=_parse_defines_for_key(table, f"tool.mojox.profile.{name}.defines"),
            flags=_parse_str_list_for_key(table, "flags", f"tool.mojox.profile.{name}.flags"),
        )
    return profiles
