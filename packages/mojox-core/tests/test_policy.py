"""Policy resolution: merge profiles, lints, and CLI into the flag set."""

from __future__ import annotations

import pytest
from mojox_core.errors import ConfigError
from mojox_core.policy import (
    BUILTIN_DEV,
    BUILTIN_RELEASE,
    parse_count,
    parse_jobs,
    resolve,
    resolve_jobs,
    resolve_without_manifest,
)
from mojox_core.settings import parse_settings
from mojox_core.types import LintConfig, LocalSettings, Manifest, Profile


def _minimal_manifest(**overrides) -> Manifest:
    """Build a minimal Manifest, overriding specific fields."""

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
        "test_parallel": False,
        "defines": {},
        "flags": (),
        "lints": LintConfig(),
        "optimize": None,
        "debug_level": None,
        "pre_build": (),
        "native_libs": (),
        "source_include": None,
        "source_exclude": (),
        "wheel_exclude": (),
        "profiles": {},
        "build_profile": "release",
    }
    defaults.update(overrides)
    return Manifest(**defaults)


class TestBuiltinDefaults:
    def test_dev_assert_is_all(self):
        assert BUILTIN_DEV.defines["ASSERT"] == "all"

    def test_dev_optimize_is_zero(self):
        assert BUILTIN_DEV.optimize == 0

    def test_dev_debug_is_line_tables(self):
        assert BUILTIN_DEV.debug_level == "line-tables"

    def test_release_assert_is_safe(self):
        assert BUILTIN_RELEASE.defines["ASSERT"] == "safe"

    def test_release_optimize_is_three(self):
        assert BUILTIN_RELEASE.optimize == 3

    def test_release_debug_is_none(self):
        assert BUILTIN_RELEASE.debug_level == "none"


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
            profiles={"dev": Profile(optimize=1, defines={}, flags=())},
        )
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.optimize == 1

    def test_cli_overrides_everything(self):
        m = _minimal_manifest(
            optimize=2,
            profiles={"dev": Profile(optimize=1, defines={}, flags=())},
        )
        p = resolve(m, "dev", {"optimize": 0}, LocalSettings.EMPTY)
        assert p.optimize == 0

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
            profiles={"dev": Profile(flags=("--extra",), defines={})},
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
        assert (BUILTIN_DEV.compile_threads, BUILTIN_RELEASE.compile_threads) == (1, 8)

    @pytest.mark.parametrize("profile", ["dev", "release"])
    def test_top_level_overrides_every_builtin(self, profile):
        m = _minimal_manifest(compile_threads=3)
        assert resolve(m, profile, {}, LocalSettings.EMPTY).compile_threads == 3

    def test_named_profile_beats_top_level(self):
        m = _minimal_manifest(compile_threads=3, profiles={"release": Profile(compile_threads=12)})
        assert resolve(m, "release", {}, LocalSettings.EMPTY).compile_threads == 12

    def test_named_profile_only_affects_itself(self):
        m = _minimal_manifest(compile_threads=3, profiles={"release": Profile(compile_threads=12)})
        assert resolve(m, "dev", {}, LocalSettings.EMPTY).compile_threads == 3

    def test_profile_without_key_keeps_builtin(self):
        m = _minimal_manifest(profiles={"release": Profile(optimize=2)})
        assert resolve(m, "release", {}, LocalSettings.EMPTY).compile_threads == 8

    def test_user_profile_without_key_inherits_top_level(self):
        m = _minimal_manifest(compile_threads=5, profiles={"bench": Profile(optimize=3)})
        assert resolve(m, "bench", {}, LocalSettings.EMPTY).compile_threads == 5

    def test_user_profile_without_key_or_top_level_is_one(self):
        m = _minimal_manifest(profiles={"bench": Profile(optimize=3)})
        assert resolve(m, "bench", {}, LocalSettings.EMPTY).compile_threads == 1

    def test_user_profile_value_used(self):
        m = _minimal_manifest(profiles={"bench": Profile(compile_threads=6)})
        assert resolve(m, "bench", {}, LocalSettings.EMPTY).compile_threads == 6

    def test_settings_beat_named_profile(self):
        m = _minimal_manifest(compile_threads=3, profiles={"release": Profile(compile_threads=12)})
        assert resolve(m, "release", {}, LocalSettings(compile_threads=2)).compile_threads == 2

    def test_cli_beats_settings_and_profile(self):
        m = _minimal_manifest(profiles={"release": Profile(compile_threads=12)})
        p = resolve(m, "release", {"compile_threads": 4}, LocalSettings(compile_threads=2))
        assert p.compile_threads == 4

    def test_env_beats_named_profile(self):
        m = _minimal_manifest(profiles={"release": Profile(compile_threads=12)})
        settings = parse_settings(None, None, {"MOJOX_COMPILE_THREADS": "2"})
        assert resolve(m, "release", {}, settings).compile_threads == 2

    def test_manifest_beats_default(self):
        m = _minimal_manifest(compile_threads=2)
        assert resolve(m, "dev", {}, LocalSettings.EMPTY).compile_threads == 2

    def test_settings_beat_manifest(self):
        m = _minimal_manifest(compile_threads=2)
        assert resolve(m, "dev", {}, LocalSettings(compile_threads=3)).compile_threads == 3

    def test_cli_beats_settings(self):
        m = _minimal_manifest(compile_threads=2)
        p = resolve(m, "dev", {"compile_threads": 4}, LocalSettings(compile_threads=3))
        assert p.compile_threads == 4

    def test_none_falls_through(self):
        m = _minimal_manifest(compile_threads=2)
        assert resolve(m, "dev", {"compile_threads": None}, LocalSettings.EMPTY).compile_threads == 2

    @pytest.mark.parametrize("raw", [0, -1, True, "4"])
    def test_invalid_cli_value_rejected(self, raw):
        with pytest.raises(ConfigError):
            resolve(_minimal_manifest(), "dev", {"compile_threads": raw}, LocalSettings.EMPTY)

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
        p = resolve(_minimal_manifest(), "dev", {"jobs": 2}, LocalSettings(jobs="num-cpus"))
        assert p.jobs == 2
        assert p.jobs_spec == "2"

    def test_settings_spec_used(self):
        p = resolve(_minimal_manifest(), "dev", {}, LocalSettings(jobs=-1), cpu_count=8)
        assert p.jobs == 7

    @pytest.mark.parametrize("spec", ["num-cpus", -1])
    def test_cpu_spec_without_cpu_count_is_programmer_error(self, spec):
        with pytest.raises(ValueError):
            resolve(_minimal_manifest(), "dev", {}, LocalSettings(jobs=spec))

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
            ({"jobs": -2}, LocalSettings(jobs=4)),
            ({}, LocalSettings(jobs="num-cpus", compile_threads=3)),
            ({"compile_threads": 5}, LocalSettings(compile_threads=3)),
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
        assert p.optimize == BUILTIN_DEV.optimize
        assert p.debug_level == BUILTIN_DEV.debug_level
        assert p.defines == BUILTIN_DEV.defines
        assert p.lints == LintConfig()
        assert p.compile_threads == BUILTIN_DEV.compile_threads == 1

    def test_single_file_runs_with_full_jobs(self):
        p = resolve_without_manifest({"jobs": 3}, LocalSettings.EMPTY)
        assert p.jobs_tests == 3

    def test_cli_and_settings_layers(self):
        p = resolve_without_manifest(
            {"optimize": 2, "defines": {"X": "1"}, "flags": ("--f",), "timeout": 9},
            LocalSettings(timeout_s=50),
        )
        assert p.optimize == 2
        assert p.defines["X"] == "1"
        assert p.flags == ("--f",)
        assert p.timeout_s == 9

    def test_invalid_cli_jobs_rejected(self):
        with pytest.raises(ConfigError):
            resolve_without_manifest({"jobs": 0}, LocalSettings.EMPTY)


class TestTimeoutValidation:
    @pytest.mark.parametrize("resolver", ["resolve", "without_manifest"])
    @pytest.mark.parametrize(
        ("cli", "settings", "key_path"),
        [
            ({"timeout": 0}, LocalSettings.EMPTY, "--timeout"),
            ({"timeout": -1}, LocalSettings.EMPTY, "--timeout"),
            ({}, LocalSettings(timeout_s=0), "settings.timeout"),
        ],
    )
    def test_non_positive_rejected(self, resolver, cli, settings, key_path):
        with pytest.raises(ConfigError) as exc:
            if resolver == "resolve":
                resolve(_minimal_manifest(), "dev", cli, settings)
            else:
                resolve_without_manifest(cli, settings)
        assert exc.value.key_path == key_path

    def test_cli_beats_settings(self):
        assert resolve(_minimal_manifest(), "dev", {"timeout": 7}, LocalSettings(timeout_s=50)).timeout_s == 7


class TestFoldBackedResolver:
    @pytest.mark.parametrize("name", ["_resolve_parallelism", "_resolve_timeout", "_resolve_scalar"])
    def test_hand_written_resolvers_are_gone(self, name):
        from mojox_core import policy

        assert not hasattr(policy, name)

    @pytest.mark.parametrize(("profile", "builtin"), [("dev", BUILTIN_DEV), ("release", BUILTIN_RELEASE)])
    def test_builtin_profiles_match_the_registry(self, profile, builtin):
        from mojox_core.options import OPTIONS, by_key

        table = by_key(OPTIONS)
        keys = ("optimize", "debug-level", "defines", "flags", "compile-threads")
        assert tuple(table[key].builtin[profile] for key in keys) == (
            builtin.optimize,
            builtin.debug_level,
            builtin.defines,
            builtin.flags,
            builtin.compile_threads,
        )
