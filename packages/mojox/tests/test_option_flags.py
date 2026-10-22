"""Generated option flags: declared once in the registry, parsed by argparse, read back for the resolver."""

from __future__ import annotations

import argparse

import pytest
from mojox.option_flags import add_option_flags, build_cli_overrides
from mojox_core.manifest import parse_manifest
from mojox_core.options import (
    OPTIONS,
    Layer,
    option,
    parse_bool_strict,
    parse_count_text,
    parse_count_toml,
    registry,
)
from mojox_core.policy import resolve
from mojox_core.types import LocalSettings


def _with_throwaways(*layers):
    """The real registry plus a throwaway bool and int option allowed in *layers*."""
    return registry(
        *OPTIONS,
        option("x-flag", parse_bool_strict, layers, default=False),
        option("x-count", parse_count_toml, layers, default=1, parse_text=parse_count_text),
    )


def _parser(command="test", options=OPTIONS):
    """A bare parser holding only the generated option flags for *command*."""
    parser = argparse.ArgumentParser(prog="mojox")
    add_option_flags(parser, command, options=options)
    return parser


class TestAddingTheCliLayer:
    REG = _with_throwaways(Layer.TOP, Layer.PROFILE, Layer.ENV, Layer.CLI)

    @pytest.mark.parametrize(("argv", "expected"), [(["--x-flag"], True), (["--no-x-flag"], False)])
    def test_bool_gets_a_negative_form(self, argv, expected):
        args = _parser(options=self.REG).parse_args(argv)
        assert build_cli_overrides(args, options=self.REG) == {"x-flag": expected}

    def test_int_flag(self):
        args = _parser(options=self.REG).parse_args(["--x-count", "4"])
        assert build_cli_overrides(args, options=self.REG) == {"x-count": 4}

    def test_flags_not_given_are_absent(self):
        assert build_cli_overrides(_parser(options=self.REG).parse_args([]), options=self.REG) == {}

    def test_invalid_value_exits_2_naming_the_flag(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _parser(options=self.REG).parse_args(["--x-count", "0"])
        assert exc.value.code == 2
        assert "argument --x-count: expected an integer >= 1, got '0'" in capsys.readouterr().err

    def test_cli_beats_env_and_manifest(self):
        data = {"project": {"name": "x", "version": "1.0.0"}, "tool": {"mojox": {"profile": {"ci": {"x-count": 5}}}}}
        manifest = parse_manifest(data, options=self.REG)
        overrides = build_cli_overrides(
            _parser(options=self.REG).parse_args(["--x-count", "7", "--no-x-flag"]), options=self.REG
        )
        env = {"MOJOX_X_COUNT": "3", "MOJOX_X_FLAG": "1"}
        policy = resolve(manifest, "ci", overrides, LocalSettings.EMPTY, env, options=self.REG)
        assert (policy.origins["x-count"], policy.origins["x-flag"]) == ("--x-count", "--x-flag")

    def test_no_flag_without_the_cli_layer(self):
        reg = _with_throwaways(Layer.TOP, Layer.PROFILE)
        with pytest.raises(SystemExit) as exc:
            _parser(options=reg).parse_args(["--x-count", "3"])
        assert exc.value.code == 2

    def test_commands_restrict_where_a_flag_appears(self):
        reg = registry(
            *OPTIONS, option("x-only", parse_count_toml, {Layer.CLI}, parse_text=parse_count_text, commands={"test"})
        )
        assert _parser("test", reg).parse_args(["--x-only", "2"]).x_only == 2
        with pytest.raises(SystemExit):
            _parser("build", reg).parse_args(["--x-only", "2"])

    def test_commands_restrict_what_is_read_back(self):
        reg = registry(
            *OPTIONS, option("x-only", parse_count_toml, {Layer.CLI}, parse_text=parse_count_text, commands={"test"})
        )
        args = argparse.Namespace(x_only=2)  # e.g. a hand-written flag on another command sharing the dest
        assert build_cli_overrides(args, command="test", options=reg) == {"x-only": 2}
        assert build_cli_overrides(args, command="build", options=reg) == {}


class TestRealRegistry:
    def test_define_pairs_and_last_repeat_wins(self):
        args = _parser().parse_args(["-D", "A=1", "--define", "B", "-D", "A=2"])
        assert build_cli_overrides(args)["defines"] == {"A": "2", "B": ""}

    def test_flags_keep_their_order(self):
        args = _parser().parse_args(["--flag=-O1", "--flag", "x"])
        assert build_cli_overrides(args)["flags"] == ("-O1", "x")

    @pytest.mark.parametrize("argv", [["-j", "-2"], ["-j-2"], ["--jobs=-2"]])
    def test_negative_jobs_is_a_value(self, argv):
        assert build_cli_overrides(_parser().parse_args(argv)) == {"jobs": -2}

    def test_every_flag_defaults_to_none(self):
        args = _parser().parse_args([])
        assert (args.jobs, args.compile_threads, args.timeout, args.defines, args.flags) == (None,) * 5

    @pytest.mark.parametrize("flag", ["--compile-threads", "--timeout"])
    def test_bad_count_names_the_flag(self, flag, capsys):
        with pytest.raises(SystemExit) as exc:
            _parser().parse_args([f"{flag}=0"])
        assert exc.value.code == 2
        assert f"argument {flag}: expected an integer >= 1, got '0'" in capsys.readouterr().err
