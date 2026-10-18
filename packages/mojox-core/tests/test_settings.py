"""Settings parser: TOML bytes + env dict -> LocalSettings."""

from __future__ import annotations

import pytest
from mojox_core.errors import ConfigError
from mojox_core.settings import parse_settings
from mojox_core.types import LocalSettings

_FORBIDDEN_KEYS = [
    "packages",
    "package-root",
    "binaries",
    "native-libs",
    "source-include",
    "source-exclude",
    "wheel-include",
    "wheel-exclude",
    "test-roots",
    "test-parallel",
    "defines",
    "lints",
    "pre-build",
]


class TestHappyPath:
    def test_empty_settings(self):
        s = parse_settings(None, None, {})
        assert s == LocalSettings.EMPTY

    def test_jobs_from_project(self):
        s = parse_settings(None, {"jobs": 4}, {})
        assert s.jobs == 4

    def test_env_var_overrides_file(self):
        s = parse_settings(None, {"jobs": 4}, {"MOJOX_JOBS": "8"})
        assert s.jobs == 8

    def test_timeout_from_settings(self):
        s = parse_settings(None, {"timeout": 600}, {})
        assert s.timeout_s == 600


class TestForbiddenKeys:
    @pytest.mark.parametrize("key", _FORBIDDEN_KEYS)
    def test_forbidden_key_in_settings(self, key):
        with pytest.raises(ConfigError, match=key):
            parse_settings(None, {key: "value"}, {})


class TestUserProjectMerge:
    def test_project_wins_over_user(self):
        s = parse_settings({"jobs": 2}, {"jobs": 8}, {})
        assert s.jobs == 8

    def test_user_used_when_no_project(self):
        s = parse_settings({"jobs": 2}, None, {})
        assert s.jobs == 2


class TestJobsSpec:
    @pytest.mark.parametrize(("raw", "expected"), [("num-cpus", "num-cpus"), (-1, -1), ("4", 4)])
    def test_toml_spec_accepted(self, raw, expected):
        assert parse_settings(None, {"jobs": raw}, {}).jobs == expected

    @pytest.mark.parametrize("raw", [0, True, "x", 2.5])
    def test_toml_spec_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_settings(None, {"jobs": raw}, {})
        assert exc.value.key_path == "settings.jobs"

    def test_invalid_user_jobs_not_masked_by_project(self):
        with pytest.raises(ConfigError, match="settings.jobs"):
            parse_settings({"jobs": 0}, {"jobs": 4}, {})

    @pytest.mark.parametrize(("raw", "expected"), [("num-cpus", "num-cpus"), ("-2", -2), (" 3 ", 3)])
    def test_env_spec_accepted(self, raw, expected):
        assert parse_settings(None, {"jobs": 4}, {"MOJOX_JOBS": raw}).jobs == expected

    @pytest.mark.parametrize("raw", ["0", "two", "", "1.5"])
    def test_env_spec_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_settings(None, None, {"MOJOX_JOBS": raw})
        assert exc.value.key_path == "MOJOX_JOBS"


class TestCompileThreads:
    def test_absent_is_none(self):
        assert parse_settings(None, {"jobs": 2}, {}).compile_threads is None

    def test_from_project(self):
        assert parse_settings(None, {"compile-threads": 2}, {}).compile_threads == 2

    @pytest.mark.parametrize("raw", [0, -1, True, "2", 1.5])
    def test_toml_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_settings(None, {"compile-threads": raw}, {})
        assert exc.value.key_path == "settings.compile-threads"

    def test_project_beats_user(self):
        assert parse_settings({"compile-threads": 2}, {"compile-threads": 5}, {}).compile_threads == 5

    def test_env_beats_project(self):
        s = parse_settings(None, {"compile-threads": 2}, {"MOJOX_COMPILE_THREADS": "3"})
        assert s.compile_threads == 3

    def test_env_alone(self):
        assert parse_settings(None, None, {"MOJOX_COMPILE_THREADS": "3"}).compile_threads == 3

    @pytest.mark.parametrize("raw", ["0", "x", "-1", ""])
    def test_env_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_settings(None, None, {"MOJOX_COMPILE_THREADS": raw})
        assert exc.value.key_path == "MOJOX_COMPILE_THREADS"

    def test_compile_threads_is_not_forbidden(self):
        assert "compile-threads" not in _FORBIDDEN_KEYS


class TestTimeout:
    """A timeout of 0 or less would kill every command at once, so it is rejected."""

    @pytest.mark.parametrize("raw", [0, -5, True, "600", 1.5])
    def test_toml_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_settings(None, {"timeout": raw}, {})
        assert exc.value.key_path == "settings.timeout"

    def test_env_accepted(self):
        assert parse_settings(None, {"timeout": 600}, {"MOJOX_TIMEOUT": "60"}).timeout_s == 60

    @pytest.mark.parametrize("raw", ["0", "-3", "x", "", "1.5"])
    def test_env_rejected(self, raw):
        with pytest.raises(ConfigError) as exc:
            parse_settings(None, None, {"MOJOX_TIMEOUT": raw})
        assert exc.value.key_path == "MOJOX_TIMEOUT"
