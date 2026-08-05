"""parse_manifest: pyproject.toml dict -> frozen Manifest."""

from __future__ import annotations

import pytest

from mojox_core._errors import ConfigError
from mojox_core._types import Manifest, BinaryEntry, LintConfig
from mojox_core.manifest import parse_manifest


class TestHappyPath:
    def test_minimal_manifest(self, minimal_pyproject):
        m = parse_manifest(minimal_pyproject)
        assert m.name == "mylib"
        assert m.version == "1.0.0"
        assert m.packages is None
        assert m.package_root == "src"
        assert m.binaries == ()
        assert m.test_roots == ("tests",)
        assert m.test_parallel is False
        assert m.defines == {}
        assert m.flags == ()
        assert m.optimize is None
        assert m.profiles == {}
        assert m.rlib_seed is None

    def test_full_manifest(self, full_pyproject):
        m = parse_manifest(full_pyproject)
        assert m.name == "mylib"
        assert m.version == "2.0.0"
        assert m.packages == ("src/mylib",)
        assert m.binaries == (BinaryEntry(source="main.mojo", name="myapp"),)
        assert m.test_roots == ("tests", "integration")
        assert m.test_parallel is True
        assert m.defines == {"FAST": "true"}
        assert m.flags == ("-Xlinker", "-rpath")
        assert m.optimize == 2
        assert m.debug_level == "full"
        assert m.rlib_seed == "examples/seed.mojo"
        assert m.lints.warnings_as_errors is True
        assert m.lints.missing_doc_strings is True
        assert "dist" in m.profiles
        assert m.profiles["dist"].optimize == 3

    def test_paths_are_lexically_normalised(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"packages": ["./src/../src/mylib"]}},
        }
        m = parse_manifest(d)
        assert m.packages == ("src/mylib",)

    def test_no_absolute_paths_in_manifest(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"packages": ["/absolute/path"]}},
        }
        with pytest.raises(ConfigError, match="absolute"):
            parse_manifest(d)

    def test_result_is_frozen(self, minimal_pyproject):
        m = parse_manifest(minimal_pyproject)
        with pytest.raises(AttributeError):
            m.name = "other"  # type: ignore[misc]


class TestErrors:
    def test_missing_project_table(self):
        with pytest.raises(ConfigError, match="project"):
            parse_manifest({})

    def test_missing_name(self):
        with pytest.raises(ConfigError, match="name"):
            parse_manifest({"project": {"version": "1.0.0"}})

    def test_missing_version(self):
        with pytest.raises(ConfigError, match="version"):
            parse_manifest({"project": {"name": "x"}})

    def test_dynamic_version_rejected(self):
        with pytest.raises(ConfigError, match="dynamic"):
            parse_manifest({"project": {"name": "x", "dynamic": ["version"]}})

    def test_optimize_out_of_range(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"optimize": 5}},
        }
        with pytest.raises(ConfigError, match="0–3"):
            parse_manifest(d)

    def test_invalid_binary_entry(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"binaries": [42]}},
        }
        with pytest.raises(ConfigError, match="binaries"):
            parse_manifest(d)

    def test_duplicate_binary_name(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {
                "mojox": {
                    "binaries": [
                        {"source": "a.mojo", "name": "dup"},
                        {"source": "b.mojo", "name": "dup"},
                    ],
                },
            },
        }
        with pytest.raises(ConfigError, match="duplicate"):
            parse_manifest(d)

    def test_flags_string_instead_of_list(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"flags": "-Xlinker"}},
        }
        with pytest.raises(ConfigError, match="flags"):
            parse_manifest(d)

    def test_defines_list_instead_of_dict(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"defines": ["FAST=true"]}},
        }
        with pytest.raises(ConfigError, match="defines"):
            parse_manifest(d)

    def test_test_roots_not_a_list(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"test-roots": 5}},
        }
        with pytest.raises(ConfigError, match="test-roots"):
            parse_manifest(d)

    def test_absolute_test_root_rejected(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"test-roots": ["/etc"]}},
        }
        with pytest.raises(ConfigError, match="absolute"):
            parse_manifest(d)

    def test_absolute_package_root_rejected(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"package-root": "/etc"}},
        }
        with pytest.raises(ConfigError, match="absolute"):
            parse_manifest(d)

    def test_absolute_rlib_seed_rejected(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"rlib-seed": "/etc/passwd"}},
        }
        with pytest.raises(ConfigError, match="absolute"):
            parse_manifest(d)

    def test_lints_string_instead_of_table(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"lints": "error"}},
        }
        with pytest.raises(ConfigError, match="lints"):
            parse_manifest(d)

    def test_lints_invalid_warnings_value(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"lints": {"warnings": "warn"}}},
        }
        with pytest.raises(ConfigError, match="warnings"):
            parse_manifest(d)

    def test_profile_defines_list_instead_of_dict(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"profile": {"dev": {"defines": [1, 2]}}}},
        }
        with pytest.raises(ConfigError, match="defines"):
            parse_manifest(d)

    def test_profile_flags_not_a_list(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"profile": {"dev": {"flags": 42}}}},
        }
        with pytest.raises(ConfigError, match="flags"):
            parse_manifest(d)

    def test_test_roots_element_not_string(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"test-roots": [42]}},
        }
        with pytest.raises(ConfigError, match="test-roots"):
            parse_manifest(d)
