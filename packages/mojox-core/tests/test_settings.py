"""Settings parser: one source per settings file, checked against the option registry."""

from __future__ import annotations

import pytest
from mojox_core import settings as settings_module
from mojox_core.errors import ConfigError
from mojox_core.settings import parse_settings
from mojox_core.types import LocalSettings

USER = "/xdg/mojox/config.toml"
PROJECT = "/work/p/.mojox/config.toml"


def _values(settings: LocalSettings) -> list[tuple[str, dict]]:
    """Each file's parsed values without origins, in precedence order."""
    return [(path, {key: v.value for key, v in values.items()}) for path, values in settings.sources]


class TestHappyPath:
    def test_no_files_is_empty(self):
        assert parse_settings([]) == LocalSettings.EMPTY

    def test_one_source_per_file_in_order(self):
        s = parse_settings([(USER, {"jobs": 2}), (PROJECT, {"jobs": 8, "timeout": 600})])
        assert _values(s) == [(USER, {"jobs": 2}), (PROJECT, {"jobs": 8, "timeout": 600})]
        assert s.config_paths == (USER, PROJECT)

    def test_origin_is_the_file_and_key(self):
        s = parse_settings([(PROJECT, {"compile-threads": 2})])
        assert s.sources[0][1]["compile-threads"].origin == f"{PROJECT}:compile-threads"

    def test_env_table(self):
        assert _values(parse_settings([(PROJECT, {"env": {"CC": "clang"}})])) == [(PROJECT, {"env": {"CC": "clang"}})]

    def test_empty_file_is_still_listed(self):
        assert parse_settings([(PROJECT, {})]).config_paths == (PROJECT,)


class TestAllowList:
    @pytest.mark.parametrize(
        ("key", "value", "allowed"),
        [
            ("defines", {"A": "1"}, "tool.mojox, tool.mojox.profile.<name>, -D/--define"),
            ("optimize", 2, "tool.mojox, tool.mojox.profile.<name>"),
            ("debug-level", "full", "tool.mojox, tool.mojox.profile.<name>"),
            ("flags", ["-x"], "tool.mojox, tool.mojox.profile.<name>, --flag"),
            ("test-parallel", True, "tool.mojox"),
            ("lints", {}, "tool.mojox"),
        ],
    )
    def test_option_not_settable_here(self, key, value, allowed):
        with pytest.raises(ConfigError) as exc:
            parse_settings([(PROJECT, {key: value})])
        assert exc.value.key_path == f"{PROJECT}:{key}"
        assert exc.value.message == f"`{key}` cannot be set in local settings; allowed in: {allowed}"

    @pytest.mark.parametrize(
        "key",
        [
            "packages",
            "package-root",
            "binaries",
            "native-libs",
            "source-include",
            "source-exclude",
            "wheel-exclude",
            "test-roots",
            "pre-build",
            "build-profile",
            "profile",
        ],
    )
    def test_structural_key_is_manifest_only(self, key):
        with pytest.raises(ConfigError) as exc:
            parse_settings([(PROJECT, {key: "x"})])
        assert exc.value.message == f"`{key}` cannot be set in local settings; allowed in: tool.mojox"

    def test_the_deny_list_is_gone(self):
        assert not hasattr(settings_module, "_FORBIDDEN_KEYS")


class TestUnknownKeys:
    def test_typo_names_the_file_and_suggests(self):
        with pytest.raises(ConfigError) as exc:
            parse_settings([(PROJECT, {"compile-thread": 2})])
        assert (
            str(exc.value) == f"{PROJECT}:compile-thread: unknown key `compile-thread`; did you mean `compile-threads`?"
        )

    def test_former_deny_list_entry_is_unknown(self):
        with pytest.raises(ConfigError, match="unknown key `wheel-include`"):
            parse_settings([(PROJECT, {"wheel-include": ["x"]})])


class TestValues:
    @pytest.mark.parametrize(("raw", "expected"), [("num-cpus", "num-cpus"), (-1, -1), ("4", 4)])
    def test_jobs_spec_accepted(self, raw, expected):
        assert _values(parse_settings([(PROJECT, {"jobs": raw})])) == [(PROJECT, {"jobs": expected})]

    @pytest.mark.parametrize("raw", [0, True, "x", 2.5])
    def test_jobs_spec_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_settings([(PROJECT, {"jobs": raw})])
        assert exc.value.key_path == f"{PROJECT}:jobs"

    def test_invalid_user_value_not_masked_by_project(self):
        with pytest.raises(ConfigError) as exc:
            parse_settings([(USER, {"jobs": 0}), (PROJECT, {"jobs": 4})])
        assert exc.value.key_path == f"{USER}:jobs"

    @pytest.mark.parametrize("raw", [0, -1, True, "2", 1.5])
    def test_compile_threads_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_settings([(PROJECT, {"compile-threads": raw})])
        assert exc.value.key_path == f"{PROJECT}:compile-threads"

    @pytest.mark.parametrize("raw", [0, -5, True, "600", 1.5])
    def test_timeout_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_settings([(PROJECT, {"timeout": raw})])
        assert exc.value.key_path == f"{PROJECT}:timeout"

    def test_env_must_be_a_table(self):
        with pytest.raises(ConfigError) as exc:
            parse_settings([(PROJECT, {"env": "x"})])
        assert str(exc.value) == f"{PROJECT}:env: expected a table of strings, got str"

    def test_env_values_must_be_strings(self):
        with pytest.raises(ConfigError) as exc:
            parse_settings([(PROJECT, {"env": {"CC": 1}})])
        assert exc.value.key_path == f"{PROJECT}:env.CC"
