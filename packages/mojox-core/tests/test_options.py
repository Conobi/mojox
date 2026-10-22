"""Option registry: declaration, derived names, construction checks, parsers and the fold."""

from __future__ import annotations

import pytest
from mojox_core.errors import ConfigError
from mojox_core.options import (
    BUILTIN_PROFILES,
    OPTIONS,
    STRUCTURAL_KEYS,
    Layer,
    Merge,
    allowed_in,
    by_key,
    env_source,
    option,
    origin_under,
    parse_bool_strict,
    parse_bool_text,
    parse_count_toml,
    parse_debug_level,
    parse_define_text,
    parse_defines,
    parse_env_table,
    parse_flag_text,
    parse_flags,
    parse_lints,
    parse_optimize,
    parse_table,
    registry,
    resolve_values,
)
from mojox_core.types import LintConfig, Value


def _same(value, origin):
    """A TOML parser that accepts anything, for options whose parsing is not under test."""
    return value


def _same_text(text, origin):
    """A text parser that accepts anything."""
    return text


class TestOptionFactory:
    def test_env_name_is_derived_when_env_is_allowed(self):
        assert option("x-count", _same, {Layer.TOP, Layer.ENV}, parse_text=_same_text).env == "MOJOX_X_COUNT"

    def test_no_env_name_without_env_layer(self):
        assert option("x-count", _same, {Layer.TOP}).env is None

    def test_cli_name_is_derived(self):
        assert option("x-count", _same, {Layer.CLI}, parse_text=_same_text).cli == ("--x-count",)

    def test_given_cli_names_are_the_complete_list(self):
        opt = option("x-count", _same, {Layer.CLI}, parse_text=_same_text, cli=("--count", "-c"))
        assert opt.cli == ("--count", "-c")

    def test_no_cli_names_without_cli_layer(self):
        assert option("x-count", _same, {Layer.TOP}).cli is None

    def test_cli_names_without_cli_layer_are_rejected(self):
        with pytest.raises(ValueError, match="CLI"):
            option("x-count", _same, {Layer.TOP}, cli=("--count",))

    def test_empty_cli_names_are_rejected(self):
        with pytest.raises(ValueError, match="at least one flag"):
            option("x-count", _same, {Layer.CLI}, parse_text=_same_text, cli=())

    def test_option_is_hashable(self):
        opt = option("x-count", _same, {Layer.TOP, Layer.PROFILE}, default={}, builtin={"dev": 2})
        assert hash(opt) == hash(option("x-count", _same, {Layer.TOP, Layer.PROFILE}, default={}, builtin={"dev": 2}))

    def test_cli_origin_is_the_first_long_flag(self):
        opt = option("x-count", _same, {Layer.CLI}, parse_text=_same_text, cli=("-c", "--count", "--cnt"))
        assert opt.cli_origin == "--count"

    def test_bool_option_gets_the_bool_text_grammar(self):
        opt = option("x-flag", parse_bool_strict, {Layer.TOP, Layer.ENV, Layer.CLI})
        assert opt.is_bool
        assert opt.parse_text is parse_bool_text

    @pytest.mark.parametrize("layer", [Layer.ENV, Layer.CLI])
    def test_env_or_cli_without_parse_text_is_rejected(self, layer):
        with pytest.raises(ValueError, match="parse_text"):
            option("x-count", _same, {Layer.TOP, layer})

    def test_defaults(self):
        opt = option("x-count", _same, {Layer.TOP})
        assert (opt.merge, opt.default, dict(opt.builtin), opt.commands, opt.metavar, opt.doc) == (
            Merge.REPLACE,
            None,
            {},
            None,
            None,
            "",
        )


class TestRegistryChecks:
    def test_returns_the_options_in_order(self):
        a = option("a-key", _same, {Layer.TOP})
        b = option("b-key", _same, {Layer.TOP})
        assert registry(a, b) == (a, b)

    def test_duplicate_key(self):
        with pytest.raises(ValueError, match="duplicate"):
            registry(option("a-key", _same, {Layer.TOP}), option("a-key", _same, {Layer.PROFILE}))

    def test_duplicate_env_name(self):
        first = option("a-b", _same, {Layer.ENV}, parse_text=_same_text)
        second = option("a_b", _same, {Layer.ENV}, parse_text=_same_text)
        with pytest.raises(ValueError, match="MOJOX_A_B"):
            registry(first, second)

    def test_duplicate_flag(self):
        first = option("a-key", _same, {Layer.CLI}, parse_text=_same_text, cli=("--x", "-x"))
        second = option("b-key", _same, {Layer.CLI}, parse_text=_same_text, cli=("--y", "-x"))
        with pytest.raises(ValueError, match="'-x'"):
            registry(first, second)

    def test_structural_key_cannot_be_an_option(self):
        with pytest.raises(ValueError, match="structural"):
            registry(option("packages", _same, {Layer.TOP}))

    def test_cli_option_needs_a_long_flag(self):
        with pytest.raises(ValueError, match="long flag"):
            registry(option("a-key", _same, {Layer.CLI}, parse_text=_same_text, cli=("-a",)))


class TestBoolGrammar:
    @pytest.mark.parametrize("value", [True, False])
    def test_toml_accepts_real_booleans(self, value):
        assert parse_bool_strict(value, "tool.mojox.x") is value

    @pytest.mark.parametrize("value", ["true", "yes", 1, 0, None])
    def test_toml_rejects_everything_else(self, value):
        with pytest.raises(ConfigError) as exc:
            parse_bool_strict(value, "tool.mojox.x")
        assert exc.value.key_path == "tool.mojox.x"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("true", True),
            ("TRUE", True),
            (" True ", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("0", False),
        ],
    )
    def test_text_accepted(self, text, expected):
        assert parse_bool_text(text, "MOJOX_X") is expected

    @pytest.mark.parametrize("text", ["", "  ", "yes", "no", "on", "maybe", "2", "tru"])
    def test_text_rejected(self, text):
        with pytest.raises(ConfigError) as exc:
            parse_bool_text(text, "MOJOX_X")
        assert exc.value.key_path == "MOJOX_X"


class TestValueParsers:
    @pytest.mark.parametrize("value", [0, 1, 2, 3])
    def test_optimize_accepted(self, value):
        assert parse_optimize(value, "tool.mojox.optimize") == value

    @pytest.mark.parametrize("value", [4, -1, 2.7, 2.0, "2", True, False, None])
    def test_optimize_rejected(self, value):
        with pytest.raises(ConfigError) as exc:
            parse_optimize(value, "tool.mojox.optimize")
        assert (exc.value.key_path, exc.value.message) == ("tool.mojox.optimize", f"must be 0–3, got {value!r}")

    @pytest.mark.parametrize("value", ["none", "line-tables", "full"])
    def test_debug_level_accepted(self, value):
        assert parse_debug_level(value, "o") == value

    @pytest.mark.parametrize("value", ["verbose", "", 1, None])
    def test_debug_level_rejected(self, value):
        with pytest.raises(ConfigError, match="must be one of"):
            parse_debug_level(value, "o")

    def test_defines_coerced_with_str(self):
        assert parse_defines({"A": 1, "B": "x"}, "o") == {"A": "1", "B": "x"}

    def test_defines_must_be_a_table(self):
        with pytest.raises(ConfigError, match="expected a table, got list"):
            parse_defines(["A=1"], "tool.mojox.defines")

    @pytest.mark.parametrize(("text", "pair"), [("A=1", ("A", "1")), ("A", ("A", "")), ("A=b=c", ("A", "b=c"))])
    def test_define_text(self, text, pair):
        assert parse_define_text(text, "--define") == pair

    @pytest.mark.parametrize("value", [["-a", "b"], ("-a", "b")])
    def test_flags_accepted(self, value):
        assert parse_flags(value, "o") == ("-a", "b")

    def test_flags_reject_a_string(self):
        with pytest.raises(ConfigError, match="expected a list, got str"):
            parse_flags("-a", "tool.mojox.flags")

    def test_flags_name_the_bad_element(self):
        with pytest.raises(ConfigError) as exc:
            parse_flags(["-a", 1], "tool.mojox.flags")
        assert exc.value.key_path == "tool.mojox.flags[1]"

    def test_flag_text_is_untouched(self):
        assert parse_flag_text("-I", "--flag") == "-I"

    @pytest.mark.parametrize(("value", "expected"), [(1, 1), (64, 64)])
    def test_count_toml_accepted(self, value, expected):
        assert parse_count_toml(value, "o") == expected

    @pytest.mark.parametrize("value", [0, -1, True, "2", 1.5])
    def test_count_toml_rejected(self, value):
        with pytest.raises(ConfigError):
            parse_count_toml(value, "o")

    def test_lints(self):
        assert parse_lints({"warnings": "error", "unstable-apis": "warn"}, "tool.mojox.lints") == LintConfig(
            warnings_as_errors=True, unstable_apis=True
        )

    def test_lints_sub_key_error_names_the_sub_key(self):
        with pytest.raises(ConfigError) as exc:
            parse_lints({"warnings": "warn"}, "tool.mojox.lints")
        assert (exc.value.key_path, exc.value.message) == ("tool.mojox.lints.warnings", "must be 'error', got 'warn'")

    def test_lints_must_be_a_table(self):
        with pytest.raises(ConfigError, match="expected a table"):
            parse_lints("error", "tool.mojox.lints")

    def test_env_table(self):
        assert parse_env_table({"CC": "clang"}, "f:env") == {"CC": "clang"}

    def test_env_must_be_a_table(self):
        with pytest.raises(ConfigError) as exc:
            parse_env_table("x", "f:env")
        assert (exc.value.key_path, exc.value.message) == ("f:env", "expected a table of strings, got str")

    def test_env_values_must_be_strings(self):
        with pytest.raises(ConfigError) as exc:
            parse_env_table({"CC": 1}, "f:env")
        assert exc.value.key_path == "f:env.CC"


def test_policy_reexports_the_moved_parsers():
    from mojox_core import options, policy

    assert (policy.parse_jobs, policy.parse_count, policy.parse_count_text) == (
        options.parse_jobs,
        options.parse_count,
        options.parse_count_text,
    )


_T, _P, _S, _E, _C = Layer.TOP, Layer.PROFILE, Layer.SETTINGS, Layer.ENV, Layer.CLI

# key: (layers, merge, builtin, default, cli names, env name)
_EXPECTED = {
    "jobs": ({_S, _E, _C}, Merge.REPLACE, {}, 1, ("--jobs", "-j"), "MOJOX_JOBS"),
    "compile-threads": (
        {_T, _P, _S, _E, _C},
        Merge.REPLACE,
        {"dev": 1, "release": 8},
        1,
        ("--compile-threads",),
        "MOJOX_COMPILE_THREADS",
    ),
    "timeout": ({_T, _P, _S, _E, _C}, Merge.REPLACE, {}, 300, ("--timeout",), "MOJOX_TIMEOUT"),
    "defines": (
        {_T, _P, _C},
        Merge.MAP_BY_KEY,
        {"dev": {"ASSERT": "all"}, "release": {"ASSERT": "safe"}},
        {},
        ("-D", "--define"),
        None,
    ),
    "flags": ({_T, _P, _C}, Merge.APPEND, {"dev": (), "release": ()}, (), ("--flag",), None),
    "optimize": ({_T, _P}, Merge.REPLACE, {"dev": 0, "release": 3}, 0, None, None),
    "debug-level": ({_T, _P}, Merge.REPLACE, {"dev": "line-tables", "release": "none"}, "none", None, None),
    "test-parallel": ({_T}, Merge.REPLACE, {}, False, None, None),
    "lints": ({_T}, Merge.REPLACE, {}, LintConfig(), None, None),
    "env": ({_S}, Merge.MAP_BY_KEY, {}, {}, None, None),
}

_HELP = {
    "jobs": 'Maximum concurrent compilations: N, "num-cpus", or -K for all CPUs but K',
    "compile-threads": "Threads each mojo build may use (--num-threads; default from the profile: dev 1, release 8)",
    "timeout": "Per-target timeout in seconds",
    "defines": "Define a compile-time variable (KEY=VALUE)",
    "flags": "Extra flag passed through to the compiler (use --flag=VALUE for dash-prefixed flags)",
}


class TestRegistryContents:
    def test_keys_in_help_order(self):
        assert [opt.key for opt in OPTIONS] == list(_EXPECTED)

    @pytest.mark.parametrize("key", list(_EXPECTED))
    def test_entry(self, key):
        opt = by_key(OPTIONS)[key]
        layers, merge, builtin, default, cli, env = _EXPECTED[key]
        assert (opt.layers, opt.merge, dict(opt.builtin), opt.default, opt.cli, opt.env) == (
            frozenset(layers),
            merge,
            builtin,
            default,
            cli,
            env,
        )

    def test_help_text_and_metavars_match_todays_flags(self):
        table = by_key(OPTIONS)
        assert {key: table[key].doc for key in _HELP} == _HELP
        assert {opt.key: opt.metavar for opt in OPTIONS if opt.metavar} == {"compile-threads": "N"}

    def test_every_cli_option_parses_text(self):
        assert all(opt.parse_text is not None for opt in OPTIONS if opt.cli or opt.env)

    def test_structural_keys(self):
        assert STRUCTURAL_KEYS == {
            "packages",
            "package-root",
            "binaries",
            "test-roots",
            "pre-build",
            "native-libs",
            "source-include",
            "source-exclude",
            "wheel-exclude",
            "build-profile",
            "profile",
        }

    def test_registry_passes_its_own_checks(self):
        assert registry(*OPTIONS) == OPTIONS


_FOLD = registry(
    option("r", parse_count_toml, {Layer.TOP, Layer.PROFILE}, default=1, builtin={"dev": 2}),
    option("a", parse_flags, {Layer.TOP, Layer.PROFILE}, merge=Merge.APPEND, default=(), builtin={"dev": ("-b",)}),
    option(
        "m",
        parse_defines,
        {Layer.TOP, Layer.PROFILE},
        merge=Merge.MAP_BY_KEY,
        default={},
        builtin={"dev": {"K": "dev"}},
    ),
)


class TestFold:
    def test_starts_from_the_profile_builtin(self):
        res = resolve_values([], "dev", options=_FOLD)
        assert res.values == {"r": 2, "a": ("-b",), "m": {"K": "dev"}}
        assert res.origins == {"r": "builtin:dev", "a": ("builtin:dev",), "m.K": "builtin:dev"}

    def test_starts_from_default_without_builtin(self):
        res = resolve_values([], "ci", options=_FOLD)
        assert res.values == {"r": 1, "a": (), "m": {}}
        assert res.origins == {"r": "default", "a": ()}

    def test_replace_takes_the_last_source(self):
        sources = [{"r": Value(3, "tool.mojox.r")}, {}, {"r": Value(4, "/u/config.toml:r")}]
        res = resolve_values(sources, "dev", options=_FOLD)
        assert (res.values["r"], res.origins["r"]) == (4, "/u/config.toml:r")

    def test_append_puts_higher_layers_last(self):
        sources = [{"a": Value(("-t",), "tool.mojox.a")}, {"a": Value(("-p", "-q"), "tool.mojox.profile.dev.a")}]
        res = resolve_values(sources, "dev", options=_FOLD)
        assert res.values["a"] == ("-b", "-t", "-p", "-q")
        assert res.origins["a"] == (
            "builtin:dev",
            "tool.mojox.a",
            "tool.mojox.profile.dev.a",
            "tool.mojox.profile.dev.a",
        )

    def test_map_replaces_by_key_and_names_each_entry(self):
        sources = [{"m": Value({"K": "top", "L": "1"}, "tool.mojox.m")}, {"m": Value({"L": "2"}, "--define")}]
        res = resolve_values(sources, "dev", options=_FOLD)
        assert list(res.values["m"].items()) == [("K", "top"), ("L", "2")]
        assert res.origins == {"r": "builtin:dev", "a": ("builtin:dev",), "m.K": "tool.mojox.m.K", "m.L": "--define.L"}

    def test_defaults_are_never_mutated(self):
        resolve_values([{"m": Value({"X": "1"}, "tool.mojox.m")}], "ci", options=_FOLD)
        assert _FOLD[2].default == {}


def _parse(table, layer, origin_of):
    """parse_table with a fresh unknown-key list; returns (values as (value, origin), unknown errors)."""
    unknown = []
    values = parse_table(table, layer, origin_of, unknown=unknown)
    return {key: (v.value, v.origin) for key, v in values.items()}, unknown


def _top(key):
    """Origin of a [tool.mojox] key."""
    return f"tool.mojox.{key}"


def _settings_file(key):
    """Origin of a key in a project settings file."""
    return f"/p/.mojox/config.toml:{key}"


def _profile_ci(key):
    """Origin of a key in [tool.mojox.profile.ci]."""
    return f"tool.mojox.profile.ci.{key}"


class TestParseTable:
    def test_option_keys_become_values_with_origins(self):
        values, unknown = _parse({"optimize": 2, "defines": {"A": 1}}, Layer.TOP, _top)
        assert values == {"optimize": (2, "tool.mojox.optimize"), "defines": ({"A": "1"}, "tool.mojox.defines")}
        assert unknown == []

    def test_structural_keys_are_left_to_the_manifest_parser_at_top(self):
        assert _parse({"packages": ["src/x"], "profile": {"ci": {}}}, Layer.TOP, _top) == ({}, [])

    @pytest.mark.parametrize(
        ("layer", "origin_of", "origin"),
        [
            (Layer.PROFILE, _profile_ci, "tool.mojox.profile.ci.pre-build"),
            (Layer.SETTINGS, _settings_file, "/p/.mojox/config.toml:pre-build"),
        ],
    )
    def test_structural_keys_elsewhere_are_allow_list_errors(self, layer, origin_of, origin):
        with pytest.raises(ConfigError) as exc:
            _parse({"pre-build": ["make"]}, layer, origin_of)
        assert exc.value.key_path == origin
        assert exc.value.message == f"`pre-build` cannot be set in {layer.value}; allowed in: tool.mojox"

    def test_option_outside_its_layers(self):
        with pytest.raises(ConfigError) as exc:
            _parse({"defines": {"A": "1"}}, Layer.SETTINGS, _settings_file)
        assert str(exc.value) == (
            "/p/.mojox/config.toml:defines: `defines` cannot be set in local settings; "
            "allowed in: tool.mojox, tool.mojox.profile.<name>, -D/--define"
        )

    def test_unknown_key_is_collected_with_a_suggestion(self):
        values, unknown = _parse({"compile-thread": 2, "optimize": 1}, Layer.TOP, _top)
        assert values == {"optimize": (1, "tool.mojox.optimize")}
        assert [str(err) for err in unknown] == [
            "tool.mojox.compile-thread: unknown key `compile-thread`; did you mean `compile-threads`?"
        ]

    def test_structural_keys_are_suggested_at_top(self):
        _values, unknown = _parse({"test-root": ["t"]}, Layer.TOP, _top)
        assert unknown[0].message == "unknown key `test-root`; did you mean `test-roots`?"

    def test_suggestions_prefer_keys_the_layer_accepts(self):
        _values, unknown = _parse({"time-out": 5, "optimise": 2}, Layer.SETTINGS, _settings_file)
        assert [err.message for err in unknown] == [
            "unknown key `time-out`; did you mean `timeout`?",
            "unknown key `optimise`; did you mean `optimize`? (allowed in: tool.mojox, tool.mojox.profile.<name>)",
        ]

    def test_misplaced_option_name_points_at_its_layers(self):
        _values, unknown = _parse({"job": 2}, Layer.TOP, _top)
        assert [str(err) for err in unknown] == [
            "tool.mojox.job: unknown key `job`; did you mean `jobs`? (allowed in: local settings, MOJOX_JOBS, --jobs/-j)"
        ]

    def test_unknown_key_in_a_profile_names_the_profile(self):
        _values, unknown = _parse({"compile-thread": 2}, Layer.PROFILE, _profile_ci)
        assert [str(err) for err in unknown] == [
            "tool.mojox.profile.ci.compile-thread: unknown key `compile-thread`; did you mean `compile-threads`?"
        ]

    def test_map_entry_error_names_the_entry(self):
        with pytest.raises(ConfigError) as exc:
            _parse({"env": {"CC": 1}}, Layer.SETTINGS, _settings_file)
        assert exc.value.key_path == "/p/.mojox/config.toml:env.CC"

    def test_invalid_value_names_its_origin(self):
        with pytest.raises(ConfigError) as exc:
            _parse({"optimize": "2"}, Layer.TOP, _top)
        assert exc.value.key_path == "tool.mojox.optimize"

    def test_origin_under(self):
        assert origin_under("tool.mojox.profile.ci")("jobs") == "tool.mojox.profile.ci.jobs"
        assert origin_under("/p/config.toml", ":")("jobs") == "/p/config.toml:jobs"

    def test_allowed_in_follows_precedence_order(self):
        table = by_key(OPTIONS)
        assert allowed_in(table["jobs"]) == "local settings, MOJOX_JOBS, --jobs/-j"
        assert allowed_in(table["compile-threads"]) == (
            "tool.mojox, tool.mojox.profile.<name>, local settings, MOJOX_COMPILE_THREADS, --compile-threads"
        )
        assert allowed_in(table["test-parallel"]) == "tool.mojox"


def test_builtin_profiles_cover_every_builtin_entry():
    assert BUILTIN_PROFILES == ("dev", "release")
    assert all(set(opt.builtin) <= set(BUILTIN_PROFILES) for opt in OPTIONS)


class TestEnvSource:
    def test_reads_only_the_variables_of_env_enabled_options(self):
        env = {
            "MOJOX_JOBS": "2",
            "MOJOX_COMPILE_THREADS": "3",
            "MOJOX_TIMEOUT": "4",
            "MOJOX_OPTIMIZE": "3",
            "MOJOX_TEST_BUNDLE": "1",
            "MOJOX_ENV": "x",
            "MOJOX_CPU_COUNT": "8",
            "MOJOX_DEFINES": "A=1",
            "PATH": "/bin",
        }
        assert {key: (v.value, v.origin) for key, v in env_source(env).items()} == {
            "jobs": (2, "MOJOX_JOBS"),
            "compile-threads": (3, "MOJOX_COMPILE_THREADS"),
            "timeout": (4, "MOJOX_TIMEOUT"),
        }

    @pytest.mark.parametrize(("text", "expected"), [("num-cpus", "num-cpus"), ("-2", -2), (" 3 ", 3)])
    def test_jobs_text(self, text, expected):
        assert env_source({"MOJOX_JOBS": text})["jobs"].value == expected

    @pytest.mark.parametrize(
        ("name", "text"),
        [
            ("MOJOX_JOBS", "0"),
            ("MOJOX_JOBS", "two"),
            ("MOJOX_JOBS", ""),
            ("MOJOX_JOBS", "1.5"),
            ("MOJOX_COMPILE_THREADS", "0"),
            ("MOJOX_COMPILE_THREADS", "x"),
            ("MOJOX_COMPILE_THREADS", "-1"),
            ("MOJOX_COMPILE_THREADS", ""),
            ("MOJOX_TIMEOUT", "0"),
            ("MOJOX_TIMEOUT", "-3"),
            ("MOJOX_TIMEOUT", "1.5"),
        ],
    )
    def test_bad_text_names_the_variable(self, name, text):
        with pytest.raises(ConfigError) as exc:
            env_source({name: text})
        assert exc.value.key_path == name

    def test_append_and_map_values_take_one_element(self):
        reg = registry(
            option("x-list", parse_flags, {Layer.ENV}, merge=Merge.APPEND, default=(), parse_text=parse_flag_text),
            option(
                "x-map", parse_defines, {Layer.ENV}, merge=Merge.MAP_BY_KEY, default={}, parse_text=parse_define_text
            ),
        )
        src = env_source({"MOJOX_X_LIST": "-a", "MOJOX_X_MAP": "K=v"}, options=reg)
        assert (src["x-list"].value, src["x-map"].value) == (("-a",), {"K": "v"})
