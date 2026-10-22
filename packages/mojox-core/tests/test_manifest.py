"""parse_manifest: pyproject.toml dict -> frozen Manifest."""

from __future__ import annotations

import pytest
from mojox_core.errors import ConfigError
from mojox_core.manifest import parse_manifest
from mojox_core.types import BinaryEntry, LintConfig


class TestHappyPath:
    def test_minimal_manifest(self, minimal_pyproject):
        m = parse_manifest(minimal_pyproject)
        assert m.name == "mylib"
        assert m.version == "1.0.0"
        assert m.packages is None
        assert m.package_root == "src"
        assert m.binaries == ()
        assert m.test_roots == ("tests",)
        assert m.values == {}
        assert m.warnings == ()
        assert m.profiles == {}

    def test_full_manifest(self, full_pyproject):
        m = parse_manifest(full_pyproject)
        assert m.name == "mylib"
        assert m.version == "2.0.0"
        assert m.packages == ("src/mylib",)
        assert m.binaries == (BinaryEntry(source="main.mojo", name="myapp"),)
        assert m.test_roots == ("tests", "integration")
        assert {key: v.value for key, v in m.values.items()} == {
            "test-parallel": True,
            "defines": {"FAST": "true"},
            "flags": ("-Xlinker", "-rpath"),
            "optimize": 2,
            "debug-level": "full",
            "lints": LintConfig(warnings_as_errors=True, check_doc_strings=True, missing_doc_strings=True),
        }
        assert "dist" in m.profiles
        assert m.profiles["dist"].values["optimize"].value == 3

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

    @pytest.mark.parametrize(
        ("project", "tool", "key_path"),
        [
            ({}, 1, "tool"),
            ({}, {"mojox": 1}, "tool.mojox"),
            ({"optional-dependencies": 1}, None, "project.optional-dependencies"),
            ({"optional-dependencies": {"cli": "rich"}}, None, "project.optional-dependencies.cli"),
            ({"optional-dependencies": {"cli": [1]}}, None, "project.optional-dependencies.cli[0]"),
            ({"urls": "x"}, None, "project.urls"),
            ({"urls": {"home": 1}}, None, "project.urls.home"),
            ({"keywords": "mojo"}, None, "project.keywords"),
            ({"classifiers": 1}, None, "project.classifiers"),
            ({"dependencies": [1]}, None, "project.dependencies[0]"),
            ({"license-files": 1}, None, "project.license-files"),
            ({"authors": 1}, None, "project.authors"),
            ({"authors": ["me"]}, None, "project.authors[0]"),
            ({"maintainers": 1}, None, "project.maintainers"),
            ({"version": None, "dynamic": 1}, None, "project.dynamic"),
            ({}, {"mojox": {"profile": 1}}, "tool.mojox.profile"),
            ({}, {"mojox": {"binaries": 1}}, "tool.mojox.binaries"),
            ({}, {"mojox": {"pre-build": 1}}, "tool.mojox.pre-build"),
        ],
    )
    def test_malformed_table_is_a_config_error(self, project, tool, key_path):
        table = {"name": "x", "version": "1.0.0", **project}
        if table["version"] is None:
            del table["version"]
        data: dict[str, object] = {"project": table}
        if tool is not None:
            data["tool"] = tool
        with pytest.raises(ConfigError) as exc:
            parse_manifest(data)
        assert exc.value.key_path == key_path

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

    def test_binary_absolute_source_rejected(self):
        d = {
            "project": {"name": "x", "version": "1.0.0"},
            "tool": {"mojox": {"binaries": [{"source": "/etc/passwd", "name": "evil"}]}},
        }
        with pytest.raises(ConfigError, match="absolute"):
            parse_manifest(d)


class TestBuildProfile:
    def test_default_is_release(self, base_project):
        data = {"project": base_project}
        m = parse_manifest(data)
        assert m.build_profile == "release"

    def test_custom_build_profile(self, base_project):
        data = {
            "project": base_project,
            "tool": {"mojox": {"build-profile": "dev"}},
        }
        m = parse_manifest(data)
        assert m.build_profile == "dev"

    def test_non_string_build_profile_raises(self, base_project):
        data = {
            "project": base_project,
            "tool": {"mojox": {"build-profile": 123}},
        }
        with pytest.raises(ConfigError):
            parse_manifest(data)

    def test_declared_build_profile(self, base_project):
        data = {
            "project": base_project,
            "tool": {"mojox": {"build-profile": "fast", "profile": {"fast": {"optimize": 2}}}},
        }
        assert parse_manifest(data).build_profile == "fast"

    def test_unknown_build_profile_names_the_key(self, base_project):
        data = {
            "project": base_project,
            "tool": {"mojox": {"build-profile": "nope", "profile": {"fast": {}}}},
        }
        with pytest.raises(ConfigError) as exc:
            parse_manifest(data)
        assert exc.value.key_path == "tool.mojox.build-profile"
        assert exc.value.message == "unknown profile 'nope'. Available: dev, fast, release"


class TestCompileThreads:
    @staticmethod
    def _data(**mojox) -> dict:
        return {"project": {"name": "x", "version": "1.0.0"}, "tool": {"mojox": mojox}}

    def test_absent_is_none(self):
        assert "compile-threads" not in parse_manifest(self._data()).values

    def test_integer_kept(self):
        assert parse_manifest(self._data(**{"compile-threads": 2})).values["compile-threads"].value == 2

    @pytest.mark.parametrize("raw", [0, -1, True, "2", 2.0])
    def test_invalid_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_manifest(self._data(**{"compile-threads": raw}))
        assert exc.value.key_path == "tool.mojox.compile-threads"


class TestProfileCompileThreads:
    @staticmethod
    def _data(profile: dict) -> dict:
        return {"project": {"name": "x", "version": "1.0.0"}, "tool": {"mojox": {"profile": profile}}}

    def test_absent_is_none(self):
        m = parse_manifest(self._data({"fast": {"optimize": 2}}))
        assert "compile-threads" not in m.profiles["fast"].values

    def test_integer_kept(self):
        m = parse_manifest(self._data({"release": {"compile-threads": 16}}))
        assert m.profiles["release"].values["compile-threads"].value == 16

    @pytest.mark.parametrize("raw", [0, -1, True, False, "8", 1.5, 8.0, [8]])
    def test_invalid_rejected_with_profile_key_path(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_manifest(self._data({"ci": {"compile-threads": raw}}))
        assert exc.value.key_path == "tool.mojox.profile.ci.compile-threads"


def _data(mojox: dict) -> dict:
    """A minimal pyproject dict with *mojox* as its [tool.mojox] table."""
    return {"project": {"name": "x", "version": "1.0.0"}, "tool": {"mojox": mojox}}


class TestUnknownKeys:
    @pytest.mark.parametrize(
        ("mojox", "origin"),
        [
            ({"compile-thread": 2}, "tool.mojox.compile-thread"),
            ({"profile": {"ci": {"compile-thread": 2}}}, "tool.mojox.profile.ci.compile-thread"),
        ],
    )
    def test_typo_is_an_error_with_a_suggestion(self, mojox, origin):
        with pytest.raises(ConfigError) as exc:
            parse_manifest(_data(mojox))
        assert exc.value.key_path == origin
        assert exc.value.message == "unknown key `compile-thread`; did you mean `compile-threads`?"

    def test_warn_mode_collects_instead_of_raising(self):
        m = parse_manifest(_data({"compile-thread": 2, "profile": {"ci": {"optimise": 2}}}), unknown_keys="warn")
        assert m.warnings == (
            "tool.mojox.compile-thread: unknown key `compile-thread`; did you mean `compile-threads`?",
            "tool.mojox.profile.ci.optimise: unknown key `optimise`; did you mean `optimize`?",
        )
        assert m.values == {}

    def test_warn_mode_still_rejects_invalid_values(self):
        with pytest.raises(ConfigError, match="0–3"):
            parse_manifest(_data({"optimize": 9}), unknown_keys="warn")

    def test_every_known_key_is_accepted(self, full_pyproject):
        assert parse_manifest(full_pyproject).warnings == ()

    def test_lints_sub_keys_and_binary_entries_are_not_checked(self):
        mojox = {
            "lints": {"warnings": "error", "typo": 1},
            "binaries": [{"source": "a.mojo", "name": "a", "extra": 1}],
        }
        assert parse_manifest(_data(mojox)).warnings == ()


class TestAllowList:
    def test_settings_only_option_in_the_manifest(self):
        with pytest.raises(ConfigError) as exc:
            parse_manifest(_data({"jobs": 2}))
        assert str(exc.value) == (
            "tool.mojox.jobs: `jobs` cannot be set in tool.mojox; allowed in: local settings, MOJOX_JOBS, --jobs/-j"
        )

    def test_top_only_option_in_a_profile(self):
        with pytest.raises(ConfigError) as exc:
            parse_manifest(_data({"profile": {"ci": {"test-parallel": True}}}))
        assert exc.value.key_path == "tool.mojox.profile.ci.test-parallel"
        assert exc.value.message == "`test-parallel` cannot be set in tool.mojox.profile.<name>; allowed in: tool.mojox"

    def test_structural_key_in_a_profile(self):
        with pytest.raises(ConfigError) as exc:
            parse_manifest(_data({"profile": {"ci": {"pre-build": ["make"]}}}))
        assert exc.value.message == "`pre-build` cannot be set in tool.mojox.profile.<name>; allowed in: tool.mojox"


class TestStrictValues:
    @pytest.mark.parametrize(
        ("mojox", "origin"),
        [
            ({"test-parallel": "yes"}, "tool.mojox.test-parallel"),
            ({"optimize": 2.7}, "tool.mojox.optimize"),
            ({"optimize": "2"}, "tool.mojox.optimize"),
            ({"optimize": True}, "tool.mojox.optimize"),
            ({"profile": {"ci": {"optimize": "2"}}}, "tool.mojox.profile.ci.optimize"),
        ],
    )
    def test_rejected_naming_the_origin(self, mojox, origin):
        with pytest.raises(ConfigError) as exc:
            parse_manifest(_data(mojox))
        assert exc.value.key_path == origin


class TestTimeoutKey:
    def test_top_level_and_profile(self):
        m = parse_manifest(_data({"timeout": 60, "profile": {"ci": {"timeout": 90}}}))
        assert m.values["timeout"].value == 60
        assert m.profiles["ci"].values["timeout"].value == 90
