"""Policy resolution: merge profiles, lints, and CLI into the flag set."""

from __future__ import annotations

import pytest
from mojox_core import policy as policy_module
from mojox_core.errors import ConfigError
from mojox_core.manifest import parse_manifest
from mojox_core.options import OPTIONS, by_key
from mojox_core.policy import (
    parse_count,
    parse_jobs,
    resolve_jobs,
)
from mojox_core.settings import parse_settings
from mojox_core.types import LintConfig, LocalSettings, Manifest, Profile, Value


def resolve(manifest, profile_name, cli_overrides, settings, env=None, *, cpu_count=None):
    """The real resolver, with no MOJOX_* variables unless *env* is given."""
    return policy_module.resolve(manifest, profile_name, cli_overrides, settings, env or {}, cpu_count=cpu_count)


def resolve_without_manifest(cli_overrides, settings, env=None, *, cpu_count=None):
    """The real lone-file resolver, with no MOJOX_* variables unless *env* is given."""
    return policy_module.resolve_without_manifest(cli_overrides, settings, env or {}, cpu_count=cpu_count)


# Former Manifest/Profile option fields, by the option key that now holds them.
_OPTION_FIELDS = {
    "optimize": "optimize",
    "debug_level": "debug-level",
    "defines": "defines",
    "flags": "flags",
    "compile_threads": "compile-threads",
    "test_parallel": "test-parallel",
    "lints": "lints",
}


def _values(prefix: str, fields: dict) -> dict[str, Value]:
    """Wrap option fields, named as the former dataclass fields, as one source's values; None means unset."""
    return {
        _OPTION_FIELDS[name]: Value(value, f"{prefix}.{_OPTION_FIELDS[name]}")
        for name, value in fields.items()
        if value is not None
    }


def _profile(**fields) -> Profile:
    """A profile table setting *fields* (former Profile field names)."""
    return Profile(values=_values("tool.mojox.profile.p", fields))


def _builtin(key: str, profile: str):
    """The registry's built-in value of *key* for *profile*."""
    return by_key(OPTIONS)[key].builtin[profile]


def _minimal_manifest(**overrides) -> Manifest:
    """Build a minimal Manifest; option fields (former names) become its values."""
    fields = {name: overrides.pop(name) for name in list(overrides) if name in _OPTION_FIELDS}
    defaults = {
        "name": "x",
        "version": "1.0.0",
        "description": None,
        "readme": None,
        "license_expr": None,
        "license_files": (),
        "requires_python": None,
        "dependencies": (),
        "optional_dependencies": {},
        "keywords": (),
        "authors": (),
        "maintainers": (),
        "urls": {},
        "classifiers": (),
        "packages": None,
        "package_root": "src",
        "binaries": (),
        "test_roots": ("tests",),
        "pre_build": (),
        "native_libs": (),
        "source_include": None,
        "source_exclude": (),
        "wheel_exclude": (),
        "profiles": {},
        "build_profile": "release",
    }
    defaults.update(overrides)
    return Manifest(**defaults, values=_values("tool.mojox", fields))


# Former LocalSettings fields, by the settings key that now holds them.
_SETTINGS_KEYS = {"jobs": "jobs", "compile_threads": "compile-threads", "timeout_s": "timeout", "env": "env"}


def _settings(**fields) -> LocalSettings:
    """One parsed settings file holding *fields* (former LocalSettings field names)."""
    return parse_settings([("config.toml", {_SETTINGS_KEYS[name]: value for name, value in fields.items()})])


class TestBuiltinDefaults:
    def test_dev_assert_is_all(self):
        assert _builtin("defines", "dev")["ASSERT"] == "all"

    def test_dev_optimize_is_zero(self):
        assert _builtin("optimize", "dev") == 0

    def test_dev_debug_is_line_tables(self):
        assert _builtin("debug-level", "dev") == "line-tables"

    def test_release_assert_is_safe(self):
        assert _builtin("defines", "release")["ASSERT"] == "safe"

    def test_release_optimize_is_three(self):
        assert _builtin("optimize", "release") == 3

    def test_release_debug_is_none(self):
        assert _builtin("debug-level", "release") == "none"


class TestPrecedence:
    def test_builtin_defaults_when_nothing_configured(self):
        m = _minimal_manifest()
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.optimize == 0
        assert p.defines["ASSERT"] == "all"
        assert p.debug_level == "line-tables"

    def test_manifest_toplevel_overrides_builtin(self):
        m = _minimal_manifest(optimize=2)
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.optimize == 2

    def test_manifest_profile_overrides_toplevel(self):
        m = _minimal_manifest(
            optimize=2,
            profiles={"dev": _profile(optimize=1, defines={}, flags=())},
        )
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.optimize == 1

    def test_cli_overrides_everything(self):
        m = _minimal_manifest(compile_threads=2, profiles={"dev": _profile(compile_threads=3)})
        p = resolve(m, "dev", {"compile-threads": 4}, _settings(compile_threads=5), {"MOJOX_COMPILE_THREADS": "6"})
        assert p.compile_threads == 4

    def test_optimize_has_no_cli_layer(self):
        with pytest.raises(ValueError, match="not a command-line option"):
            resolve(_minimal_manifest(), "dev", {"optimize": 0}, LocalSettings.EMPTY)

    def test_release_is_default_for_build(self):
        m = _minimal_manifest()
        p = resolve(m, "release", {}, LocalSettings.EMPTY)
        assert p.optimize == 3
        assert p.defines["ASSERT"] == "safe"


class TestMergeSemantics:
    def test_optimize_replaces(self):
        m = _minimal_manifest(optimize=1)
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.optimize == 1

    def test_defines_map_replace_by_key(self):
        m = _minimal_manifest(defines={"FAST": "true", "ASSERT": "none"})
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.defines["FAST"] == "true"
        assert p.defines["ASSERT"] == "none"

    def test_flags_append_higher_layer_last(self):
        m = _minimal_manifest(
            flags=("-Xlinker", "-rpath"),
            profiles={"dev": _profile(flags=("--extra",), defines={})},
        )
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.flags == ("-Xlinker", "-rpath", "--extra")

    def test_assert_follows_normal_define_precedence(self):
        m = _minimal_manifest(defines={"ASSERT": "none"})
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.defines["ASSERT"] == "none"

    def test_no_profile_table_is_byte_identical_to_builtin(self):
        m = _minimal_manifest()
        p1 = resolve(m, "dev", {}, LocalSettings.EMPTY)
        p2 = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p1 == p2


class TestTestParallel:
    def test_test_parallel_false_clamps_jobs_to_one(self):
        m = _minimal_manifest(test_parallel=False)
        p = resolve(m, "dev", {"jobs": 8}, LocalSettings.EMPTY)
        assert p.jobs_tests == 1
        assert p.jobs_compile == 8

    def test_test_parallel_true_allows_jobs(self):
        m = _minimal_manifest(test_parallel=True)
        p = resolve(m, "dev", {"jobs": 4}, LocalSettings.EMPTY)
        assert p.jobs_tests == 4


class TestErrors:
    def test_unknown_profile_name(self):
        m = _minimal_manifest()
        with pytest.raises(ConfigError) as exc:
            resolve(m, "nonexistent", {}, LocalSettings.EMPTY)
        assert exc.value.key_path == "profile.nonexistent"
        assert exc.value.message == "unknown profile 'nonexistent'. Available: dev, release"


class TestJobsSpec:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (4, 4),
            ("4", 4),
            (" 4 ", 4),
            ("num-cpus", "num-cpus"),
            (" num-cpus ", "num-cpus"),
            (-2, -2),
            ("-2", -2),
            (10**30, 10**30),
        ],
    )
    def test_parse_accepts(self, value, expected):
        assert parse_jobs(value, "settings.jobs") == expected

    @pytest.mark.parametrize(
        "value",
        [
            0,
            "0",
            "-0",
            "+0",
            " 0 ",
            True,
            False,
            2.5,
            2.0,
            "abc",
            "num_cpus",
            "NUM-CPUS",
            "",
            "   ",
            "4.0",
            "1_000",
            "\u0663",
            "4 4",
            None,
            [4],
            {"n": 4},
        ],
    )
    def test_parse_rejects(self, value):
        with pytest.raises(ConfigError) as exc:
            parse_jobs(value, "settings.jobs")
        assert exc.value.key_path == "settings.jobs"

    def test_zero_message_suggests_alternatives(self):
        with pytest.raises(ConfigError, match="num-cpus"):
            parse_jobs(0, "MOJOX_JOBS")

    def test_overlong_digit_string_is_a_config_error(self):
        with pytest.raises(ConfigError):
            parse_jobs("9" * 5000, "MOJOX_JOBS")

    @pytest.mark.parametrize(
        ("spec", "cpu_count", "expected"),
        [
            ("num-cpus", 8, 8),
            (-1, 8, 7),
            (-100, 8, 1),
            (-8, 8, 1),
            (3, 1, 3),
            (16, 4, 16),
            ("num-cpus", 1, 1),
        ],
    )
    def test_resolve(self, spec, cpu_count, expected):
        assert resolve_jobs(spec, cpu_count) == expected


class TestCompileThreadsParse:
    @pytest.mark.parametrize("value", [1, 2, 64])
    def test_toml_int_accepted(self, value):
        assert parse_count(value, "k", from_text=False) == value

    @pytest.mark.parametrize("value", [0, -1, True, False, "2", 1.5, 2.0, None, [2]])
    def test_toml_rejects(self, value):
        with pytest.raises(ConfigError) as exc:
            parse_count(value, "tool.mojox.compile-threads", from_text=False)
        assert exc.value.key_path == "tool.mojox.compile-threads"

    @pytest.mark.parametrize(("value", "expected"), [("3", 3), (" 3 ", 3), ("+3", 3)])
    def test_text_accepted(self, value, expected):
        assert parse_count(value, "MOJOX_COMPILE_THREADS", from_text=True) == expected

    @pytest.mark.parametrize("value", ["0", "-0", "-1", "x", "", "1.5", "num-cpus", "1_0", "9" * 5000])
    def test_text_rejects(self, value):
        with pytest.raises(ConfigError) as exc:
            parse_count(value, "MOJOX_COMPILE_THREADS", from_text=True)
        assert exc.value.key_path == "MOJOX_COMPILE_THREADS"


class TestCompileThreadsPrecedence:
    def test_dev_default_is_one(self):
        assert resolve(_minimal_manifest(), "dev", {}, LocalSettings.EMPTY).compile_threads == 1

    def test_release_default_is_eight(self):
        assert resolve(_minimal_manifest(), "release", {}, LocalSettings.EMPTY).compile_threads == 8

    def test_builtin_values(self):
        assert (_builtin("compile-threads", "dev"), _builtin("compile-threads", "release")) == (1, 8)

    @pytest.mark.parametrize("profile", ["dev", "release"])
    def test_top_level_overrides_every_builtin(self, profile):
        m = _minimal_manifest(compile_threads=3)
        assert resolve(m, profile, {}, LocalSettings.EMPTY).compile_threads == 3

    def test_named_profile_beats_top_level(self):
        m = _minimal_manifest(compile_threads=3, profiles={"release": _profile(compile_threads=12)})
        assert resolve(m, "release", {}, LocalSettings.EMPTY).compile_threads == 12

    def test_named_profile_only_affects_itself(self):
        m = _minimal_manifest(compile_threads=3, profiles={"release": _profile(compile_threads=12)})
        assert resolve(m, "dev", {}, LocalSettings.EMPTY).compile_threads == 3

    def test_profile_without_key_keeps_builtin(self):
        m = _minimal_manifest(profiles={"release": _profile(optimize=2)})
        assert resolve(m, "release", {}, LocalSettings.EMPTY).compile_threads == 8

    def test_user_profile_without_key_inherits_top_level(self):
        m = _minimal_manifest(compile_threads=5, profiles={"bench": _profile(optimize=3)})
        assert resolve(m, "bench", {}, LocalSettings.EMPTY).compile_threads == 5

    def test_user_profile_without_key_or_top_level_is_one(self):
        m = _minimal_manifest(profiles={"bench": _profile(optimize=3)})
        assert resolve(m, "bench", {}, LocalSettings.EMPTY).compile_threads == 1

    def test_user_profile_value_used(self):
        m = _minimal_manifest(profiles={"bench": _profile(compile_threads=6)})
        assert resolve(m, "bench", {}, LocalSettings.EMPTY).compile_threads == 6

    def test_settings_beat_named_profile(self):
        m = _minimal_manifest(compile_threads=3, profiles={"release": _profile(compile_threads=12)})
        assert resolve(m, "release", {}, _settings(compile_threads=2)).compile_threads == 2

    def test_cli_beats_settings_and_profile(self):
        m = _minimal_manifest(profiles={"release": _profile(compile_threads=12)})
        p = resolve(m, "release", {"compile-threads": 4}, _settings(compile_threads=2))
        assert p.compile_threads == 4

    def test_env_beats_named_profile(self):
        m = _minimal_manifest(profiles={"release": _profile(compile_threads=12)})
        assert resolve(m, "release", {}, LocalSettings.EMPTY, {"MOJOX_COMPILE_THREADS": "2"}).compile_threads == 2

    def test_manifest_beats_default(self):
        m = _minimal_manifest(compile_threads=2)
        assert resolve(m, "dev", {}, LocalSettings.EMPTY).compile_threads == 2

    def test_settings_beat_manifest(self):
        m = _minimal_manifest(compile_threads=2)
        assert resolve(m, "dev", {}, _settings(compile_threads=3)).compile_threads == 3

    def test_cli_beats_settings(self):
        m = _minimal_manifest(compile_threads=2)
        p = resolve(m, "dev", {"compile-threads": 4}, _settings(compile_threads=3))
        assert p.compile_threads == 4

    def test_none_falls_through(self):
        m = _minimal_manifest(compile_threads=2)
        assert resolve(m, "dev", {"compile-threads": None}, LocalSettings.EMPTY).compile_threads == 2

    @pytest.mark.parametrize("raw", [0, -1, True, "4"])
    def test_invalid_cli_value_rejected(self, raw):
        with pytest.raises(ConfigError):
            resolve(_minimal_manifest(), "dev", {"compile-threads": raw}, LocalSettings.EMPTY)

    def test_independent_of_jobs_and_cpus(self):
        p = resolve(_minimal_manifest(), "dev", {"jobs": "num-cpus"}, LocalSettings.EMPTY, cpu_count=64)
        assert p.compile_threads == 1


class TestJobsResolution:
    def test_default_needs_no_cpu_count(self):
        p = resolve(_minimal_manifest(), "dev", {}, LocalSettings.EMPTY)
        assert (p.jobs, p.jobs_compile, p.jobs_tests, p.jobs_spec) == (1, 1, 1, "1")

    @pytest.mark.parametrize(("test_parallel", "jobs_tests"), [(False, 1), (True, 6)])
    def test_cli_num_cpus(self, test_parallel, jobs_tests):
        m = _minimal_manifest(test_parallel=test_parallel)
        p = resolve(m, "dev", {"jobs": "num-cpus"}, LocalSettings.EMPTY, cpu_count=6)
        assert (p.jobs, p.jobs_compile, p.jobs_tests) == (6, 6, jobs_tests)
        assert p.jobs_spec == "num-cpus"

    def test_cli_negative_floors_at_one(self):
        p = resolve(_minimal_manifest(), "dev", {"jobs": -3}, LocalSettings.EMPTY, cpu_count=2)
        assert p.jobs == 1
        assert p.jobs_spec == "-3"

    def test_cli_beats_settings(self):
        p = resolve(_minimal_manifest(), "dev", {"jobs": 2}, _settings(jobs="num-cpus"))
        assert p.jobs == 2
        assert p.jobs_spec == "2"

    def test_settings_spec_used(self):
        p = resolve(_minimal_manifest(), "dev", {}, _settings(jobs=-1), cpu_count=8)
        assert p.jobs == 7

    @pytest.mark.parametrize("spec", ["num-cpus", -1])
    def test_cpu_spec_without_cpu_count_is_programmer_error(self, spec):
        with pytest.raises(ValueError):
            resolve(_minimal_manifest(), "dev", {}, _settings(jobs=spec))

    @pytest.mark.parametrize("raw", [0, True, "abc"])
    def test_invalid_cli_jobs_rejected(self, raw):
        with pytest.raises(ConfigError):
            resolve(_minimal_manifest(), "dev", {"jobs": raw}, LocalSettings.EMPTY, cpu_count=4)

    def test_positive_jobs_not_capped(self):
        p = resolve(_minimal_manifest(), "dev", {"jobs": 16}, LocalSettings.EMPTY, cpu_count=2)
        assert p.jobs == 16


class TestResolveWithoutManifest:
    @pytest.mark.parametrize(
        ("cli", "settings"),
        [
            ({}, LocalSettings.EMPTY),
            ({"jobs": "num-cpus"}, LocalSettings.EMPTY),
            ({"jobs": -2}, _settings(jobs=4)),
            ({}, _settings(jobs="num-cpus", compile_threads=3)),
            ({"compile-threads": 5}, _settings(compile_threads=3)),
        ],
    )
    def test_matches_resolve(self, cli, settings):
        with_manifest = resolve(_minimal_manifest(), "dev", cli, settings, cpu_count=6)
        without = resolve_without_manifest(cli, settings, cpu_count=6)
        assert (without.jobs, without.jobs_compile, without.jobs_spec, without.compile_threads) == (
            with_manifest.jobs,
            with_manifest.jobs_compile,
            with_manifest.jobs_spec,
            with_manifest.compile_threads,
        )

    def test_dev_defaults(self):
        p = resolve_without_manifest({}, LocalSettings.EMPTY)
        assert p.timeout_s == 300
        assert p.optimize == 0
        assert p.debug_level == "line-tables"
        assert p.defines == {"ASSERT": "all"}
        assert p.lints == LintConfig()
        assert p.compile_threads == 1

    def test_single_file_runs_with_full_jobs(self):
        p = resolve_without_manifest({"jobs": 3}, LocalSettings.EMPTY)
        assert p.jobs_tests == 3

    def test_cli_and_settings_layers(self):
        p = resolve_without_manifest(
            {"defines": {"X": "1"}, "flags": ("--f",), "timeout": 9},
            _settings(timeout_s=50),
        )
        assert p.optimize == 0
        assert p.defines["X"] == "1"
        assert p.flags == ("--f",)
        assert p.timeout_s == 9

    def test_invalid_cli_jobs_rejected(self):
        with pytest.raises(ConfigError):
            resolve_without_manifest({"jobs": 0}, LocalSettings.EMPTY)


class TestTimeoutValidation:
    @pytest.mark.parametrize("resolver", ["resolve", "without_manifest"])
    @pytest.mark.parametrize("cli", [{"timeout": 0}, {"timeout": -1}])
    def test_non_positive_cli_value_rejected(self, resolver, cli):
        with pytest.raises(ConfigError) as exc:
            if resolver == "resolve":
                resolve(_minimal_manifest(), "dev", cli, LocalSettings.EMPTY)
            else:
                resolve_without_manifest(cli, LocalSettings.EMPTY)
        assert exc.value.key_path == "--timeout"

    def test_non_positive_setting_rejected(self):
        with pytest.raises(ConfigError) as exc:
            _settings(timeout_s=0)
        assert exc.value.key_path == "config.toml:timeout"

    def test_cli_beats_settings(self):
        assert resolve(_minimal_manifest(), "dev", {"timeout": 7}, _settings(timeout_s=50)).timeout_s == 7


class TestFoldBackedResolver:
    @pytest.mark.parametrize("name", ["_resolve_parallelism", "_resolve_timeout", "_resolve_scalar"])
    def test_hand_written_resolvers_are_gone(self, name):
        assert not hasattr(policy_module, name)


class TestProvenanceAndEnv:
    def test_origins_name_each_winning_layer(self):
        p = resolve(_minimal_manifest(compile_threads=3), "dev", {"jobs": 2}, LocalSettings.EMPTY)
        assert p.origins["compile-threads"] == "tool.mojox.compile-threads"
        assert p.origins["jobs"] == "--jobs"
        assert p.origins["timeout"] == "default"
        assert p.origins["defines.ASSERT"] == "builtin:dev"
        assert p.origins["flags"] == ()

    def test_settings_env_reaches_the_policy(self):
        p = resolve(_minimal_manifest(), "dev", {}, _settings(env={"CC": "clang"}))
        assert p.env == {"CC": "clang"}

    def test_settings_env_tables_merge_per_key(self):
        user, project = "/u/.config/mojox/config.toml", "/w/proj/.mojox/config.toml"
        settings = parse_settings(
            [
                (user, {"env": {"CC": "u", "SAME": "user"}}),
                (project, {"env": {"LD": "p", "SAME": "proj"}}),
            ]
        )
        p = resolve(_minimal_manifest(), "dev", {}, settings)
        assert p.env == {"CC": "u", "SAME": "proj", "LD": "p"}
        assert p.origins["env.CC"] == f"{user}:env.CC"
        assert p.origins["env.SAME"] == f"{project}:env.SAME"
        assert p.origins["env.LD"] == f"{project}:env.LD"


class TestManifestTimeout:
    def test_profile_beats_top_level_and_cli_beats_both(self):
        m = parse_manifest(
            {
                "project": {"name": "x", "version": "1.0.0"},
                "tool": {"mojox": {"timeout": 60, "profile": {"ci": {"timeout": 90}}}},
            }
        )
        assert resolve(m, "dev", {}, LocalSettings.EMPTY).timeout_s == 60
        assert resolve(m, "ci", {}, LocalSettings.EMPTY).timeout_s == 90
        assert resolve(m, "ci", {"timeout": 5}, LocalSettings.EMPTY).timeout_s == 5


class TestManifestOrigins:
    def test_test_parallel_from_the_manifest_and_lints_default_when_absent(self):
        m = parse_manifest({"project": {"name": "x", "version": "1.0.0"}, "tool": {"mojox": {"test-parallel": True}}})
        origins = resolve(m, "dev", {}, LocalSettings.EMPTY).origins
        assert origins["test-parallel"] == "tool.mojox.test-parallel"
        assert origins["lints"] == "default"

    def test_lints_from_the_manifest(self):
        m = parse_manifest(
            {"project": {"name": "x", "version": "1.0.0"}, "tool": {"mojox": {"lints": {"warnings": "error"}}}}
        )
        origins = resolve(m, "dev", {}, LocalSettings.EMPTY).origins
        assert (origins["test-parallel"], origins["lints"]) == ("default", "tool.mojox.lints")


class TestEnvLayer:
    def test_env_beats_the_manifest_profile(self):
        m = _minimal_manifest(compile_threads=2, profiles={"release": _profile(compile_threads=12)})
        p = resolve(m, "release", {}, LocalSettings.EMPTY, {"MOJOX_COMPILE_THREADS": "3"})
        assert (p.compile_threads, p.origins["compile-threads"]) == (3, "MOJOX_COMPILE_THREADS")

    def test_cli_beats_env(self):
        p = resolve(_minimal_manifest(), "dev", {"jobs": 4}, LocalSettings.EMPTY, {"MOJOX_JOBS": "2"})
        assert (p.jobs, p.origins["jobs"]) == (4, "--jobs")

    def test_bad_env_value_names_the_variable(self):
        with pytest.raises(ConfigError) as exc:
            resolve(_minimal_manifest(), "dev", {}, LocalSettings.EMPTY, {"MOJOX_COMPILE_THREADS": "0"})
        assert exc.value.key_path == "MOJOX_COMPILE_THREADS"

    def test_without_manifest_reads_env(self):
        assert resolve_without_manifest({}, LocalSettings.EMPTY, {"MOJOX_TIMEOUT": "9"}).timeout_s == 9

    def test_other_mojox_variables_are_ignored(self):
        p = resolve(_minimal_manifest(), "dev", {}, LocalSettings.EMPTY, {"MOJOX_OPTIMIZE": "3", "MOJOX_ENV": "x"})
        assert p.optimize == 0
