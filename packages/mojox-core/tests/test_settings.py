"""Settings parser: TOML bytes + env dict -> LocalSettings."""

from __future__ import annotations

import pytest

from mojox_core._errors import ConfigError
from mojox_core._types import LocalSettings
from mojox_core.settings import parse_settings

_FORBIDDEN_KEYS = [
    "packages", "package-root", "binaries", "native-libs",
    "source-include", "source-exclude", "wheel-include", "wheel-exclude",
    "test-roots", "test-parallel", "defines", "lints", "pre-build",
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
