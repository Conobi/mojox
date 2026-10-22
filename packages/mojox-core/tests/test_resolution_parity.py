"""Policy resolution across every layer, pinned before the option registry replaces the resolver."""

from __future__ import annotations

from mojox_core.manifest import parse_manifest
from mojox_core.policy import resolve, resolve_without_manifest
from mojox_core.settings import parse_settings
from mojox_core.types import LintConfig

_FIELDS = (
    "optimize",
    "debug_level",
    "defines",
    "flags",
    "include_paths",
    "lints",
    "jobs",
    "jobs_compile",
    "jobs_tests",
    "timeout_s",
    "compile_threads",
    "jobs_spec",
)

_DEV = {
    "optimize": 0,
    "debug_level": "line-tables",
    "defines": {"ASSERT": "all"},
    "flags": (),
    "include_paths": (),
    "lints": LintConfig(),
    "jobs": 1,
    "jobs_compile": 1,
    "jobs_tests": 1,
    "timeout_s": 300,
    "compile_threads": 1,
    "jobs_spec": "1",
}
_RELEASE = {**_DEV, "optimize": 3, "debug_level": "none", "defines": {"ASSERT": "safe"}, "compile_threads": 8}
_USER_PROFILE = {**_DEV, "debug_level": "none", "defines": {}}

_TOP = {
    "optimize": 2,
    "debug-level": "full",
    "defines": {"A": "1", "ASSERT": "none"},
    "flags": ["--t"],
    "compile-threads": 3,
    "test-parallel": True,
}
_CI = {"optimize": 1, "debug-level": "none", "defines": {"A": "2", "B": "3"}, "flags": ["--p"], "compile-threads": 4}
_USER_FILE = {"jobs": 2, "compile-threads": 5, "timeout": 60}
_PROJECT_FILE = {"jobs": 3, "timeout": 70}
_ENV = {"MOJOX_JOBS": "4", "MOJOX_COMPILE_THREADS": "6", "MOJOX_TIMEOUT": "80"}
_CLI = {"jobs": 5, "compile-threads": 7, "timeout": 90, "defines": {"C": "x"}, "flags": ("--c",)}


def _policy(mojox, profile, *, user=None, project=None, env=None, cli=None, cpu_count=None) -> dict:
    """Resolve through the public parsers and keep only the fields pinned here."""
    manifest = parse_manifest({"project": {"name": "x", "version": "1.0.0"}, "tool": {"mojox": mojox}})
    files = [(name, table) for name, table in (("user.toml", user), ("project.toml", project)) if table is not None]
    policy = resolve(manifest, profile, cli or {}, parse_settings(files), env or {}, cpu_count=cpu_count)
    return {name: getattr(policy, name) for name in _FIELDS}


def test_builtin_dev():
    assert _policy({}, "dev") == _DEV


def test_builtin_release():
    assert _policy({}, "release") == _RELEASE


def test_user_profile_setting_nothing():
    assert _policy({"profile": {"bench": {}}}, "bench") == _USER_PROFILE


def test_top_level():
    assert _policy(_TOP, "dev") == {
        **_DEV,
        "optimize": 2,
        "debug_level": "full",
        "defines": {"ASSERT": "none", "A": "1"},
        "flags": ("--t",),
        "compile_threads": 3,
    }


def test_profile_over_top_level():
    assert _policy({**_TOP, "profile": {"ci": _CI}}, "ci") == {
        **_USER_PROFILE,
        "optimize": 1,
        "debug_level": "none",
        "defines": {"A": "2", "ASSERT": "none", "B": "3"},
        "flags": ("--t", "--p"),
        "compile_threads": 4,
    }


def test_defines_keep_first_definition_order():
    assert list(_policy({**_TOP, "profile": {"ci": _CI}}, "ci")["defines"]) == ["A", "ASSERT", "B"]
    dev = _policy({"profile": {"dev": {"defines": {"B": "3", "ASSERT": "none"}}}}, "dev")
    assert list(dev["defines"].items()) == [("ASSERT", "none"), ("B", "3")]


def test_user_file():
    assert _policy({}, "dev", user=_USER_FILE) == {
        **_DEV,
        "jobs": 2,
        "jobs_compile": 2,
        "jobs_spec": "2",
        "timeout_s": 60,
        "compile_threads": 5,
    }


def test_project_file_over_user_file():
    assert _policy({}, "dev", user=_USER_FILE, project=_PROJECT_FILE) == {
        **_DEV,
        "jobs": 3,
        "jobs_compile": 3,
        "jobs_spec": "3",
        "timeout_s": 70,
        "compile_threads": 5,
    }


def test_env_over_files():
    assert _policy({}, "dev", user=_USER_FILE, project=_PROJECT_FILE, env=_ENV) == {
        **_DEV,
        "jobs": 4,
        "jobs_compile": 4,
        "jobs_spec": "4",
        "timeout_s": 80,
        "compile_threads": 6,
    }


def test_cli_over_env():
    assert _policy({}, "dev", user=_USER_FILE, project=_PROJECT_FILE, env=_ENV, cli=_CLI) == {
        **_DEV,
        "jobs": 5,
        "jobs_compile": 5,
        "jobs_spec": "5",
        "timeout_s": 90,
        "compile_threads": 7,
        "defines": {"ASSERT": "all", "C": "x"},
        "flags": ("--c",),
    }


def test_cli_defines_and_flags_come_last():
    result = _policy({**_TOP, "profile": {"ci": _CI}}, "ci", cli={"defines": {"A": "9"}, "flags": ("--c",)})
    assert result["flags"] == ("--t", "--p", "--c")
    assert result["defines"] == {"A": "9", "ASSERT": "none", "B": "3"}


def test_test_parallel_lets_tests_use_jobs():
    result = _policy({"test-parallel": True}, "dev", cli={"jobs": "num-cpus"}, cpu_count=6)
    assert (result["jobs"], result["jobs_compile"], result["jobs_tests"], result["jobs_spec"]) == (6, 6, 6, "num-cpus")


def test_tests_run_serially_without_test_parallel():
    result = _policy({}, "dev", cli={"jobs": 4})
    assert (result["jobs"], result["jobs_tests"]) == (4, 1)


def test_without_manifest():
    settings = parse_settings([("project.toml", {"compile-threads": 2})])
    policy = resolve_without_manifest(
        {"jobs": 3, "defines": {"X": "1"}, "flags": ("--f",)}, settings, {"MOJOX_TIMEOUT": "9"}
    )
    assert {name: getattr(policy, name) for name in _FIELDS} == {
        **_DEV,
        "jobs": 3,
        "jobs_compile": 3,
        "jobs_tests": 3,
        "jobs_spec": "3",
        "timeout_s": 9,
        "compile_threads": 2,
        "defines": {"ASSERT": "all", "X": "1"},
        "flags": ("--f",),
    }


def test_negative_cli_jobs_count_back_from_cpu_count():
    assert _policy({}, "dev", cli={"jobs": -2}, cpu_count=6) == {
        **_DEV,
        "jobs": 4,
        "jobs_compile": 4,
        "jobs_spec": "-2",
    }


def test_negative_jobs_clamp_to_one():
    assert _policy({}, "dev", cli={"jobs": -5}, cpu_count=2) == {**_DEV, "jobs_spec": "-5"}


def test_negative_jobs_from_settings_file_and_env():
    expected = {**_DEV, "jobs": 5, "jobs_compile": 5, "jobs_spec": "-1"}
    assert _policy({}, "dev", project={"jobs": -1}, cpu_count=6) == expected
    assert _policy({}, "dev", env={"MOJOX_JOBS": "-1"}, cpu_count=6) == expected


def test_num_cpus_jobs_from_env_and_settings_file():
    expected = {**_DEV, "jobs": 6, "jobs_compile": 6, "jobs_spec": "num-cpus"}
    assert _policy({}, "dev", env={"MOJOX_JOBS": "num-cpus"}, cpu_count=6) == expected
    assert _policy({}, "dev", user={"jobs": "num-cpus"}, cpu_count=6) == expected


def test_non_default_lints():
    lints = {"warnings": "error", "check-doc-strings": "check", "missing-doc-strings": "warn", "unstable-apis": "warn"}
    assert _policy({"lints": lints}, "dev") == {
        **_DEV,
        "lints": LintConfig(
            warnings_as_errors=True,
            check_doc_strings=True,
            missing_doc_strings=True,
            unstable_apis=True,
        ),
    }


def test_settings_file_compile_threads_over_top_level():
    assert _policy(_TOP, "dev", user={"compile-threads": 5}) == {
        **_DEV,
        "optimize": 2,
        "debug_level": "full",
        "defines": {"ASSERT": "none", "A": "1"},
        "flags": ("--t",),
        "compile_threads": 5,
    }


def test_settings_file_compile_threads_over_profile():
    assert _policy({**_TOP, "profile": {"ci": _CI}}, "ci", user={"compile-threads": 5}) == {
        **_USER_PROFILE,
        "optimize": 1,
        "debug_level": "none",
        "defines": {"A": "2", "ASSERT": "none", "B": "3"},
        "flags": ("--t", "--p"),
        "compile_threads": 5,
    }


def test_empty_profile_inherits_top_level():
    assert _policy({**_TOP, "profile": {"ci": {}}}, "ci") == {
        **_USER_PROFILE,
        "optimize": 2,
        "debug_level": "full",
        "defines": {"A": "1", "ASSERT": "none"},
        "flags": ("--t",),
        "compile_threads": 3,
    }


def test_profile_setting_one_key_inherits_the_rest_from_top_level():
    assert _policy({**_TOP, "profile": {"ci": {"optimize": 1}}}, "ci") == {
        **_USER_PROFILE,
        "optimize": 1,
        "debug_level": "full",
        "defines": {"A": "1", "ASSERT": "none"},
        "flags": ("--t",),
        "compile_threads": 3,
    }


def test_top_level_on_release():
    assert _policy(_TOP, "release") == {
        **_RELEASE,
        "optimize": 2,
        "debug_level": "full",
        "defines": {"ASSERT": "none", "A": "1"},
        "flags": ("--t",),
        "compile_threads": 3,
    }


def test_partial_top_level_keeps_release_builtins():
    assert _policy({"optimize": 2}, "release") == {**_RELEASE, "optimize": 2}
