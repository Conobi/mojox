"""Generate the option flags from the registry and read them back as resolver input."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from typing import Any

from mojox_core import ConfigError
from mojox_core.options import OPTIONS, Layer, Merge, Option


def add_option_flags(parser: argparse.ArgumentParser, command: str, options: Sequence[Option] = OPTIONS) -> None:
    """Declare one flag per option that allows the CLI and serves *command*, in registry order.

    Every flag defaults to None ("not given") so lower layers apply. Values are
    parsed by the option's ``parse_text`` inside argparse, so a bad value exits
    2 before the manifest is read.
    """
    for opt in _flag_options(options, command):
        names = opt.cli or ()  # never empty here: _flag_options keeps only options with flags
        dest = opt.key.replace("-", "_")
        if opt.is_bool:
            parser.add_argument(*names, dest=dest, action=argparse.BooleanOptionalAction, default=None, help=opt.doc)
            continue
        parser.add_argument(
            *names,
            dest=dest,
            action="store" if opt.merge is Merge.REPLACE else "append",
            type=_text_type(opt),
            default=None,
            metavar=opt.metavar,
            help=opt.doc,
        )


def build_cli_overrides(
    args: argparse.Namespace, *, command: str | None = None, options: Sequence[Option] = OPTIONS
) -> dict[str, Any]:
    """Collect the option flags that were given, keyed by option key and shaped like TOML values.

    With *command*, only the flags :func:`add_option_flags` declared for it are
    read, so a same-named dest from another flag can never pose as an option.
    APPEND occurrences become a tuple and MAP_BY_KEY pairs a dict (a repeated
    key keeps its last value), which is what :func:`mojox_core.policy.resolve` expects.
    """
    overrides: dict[str, Any] = {}
    for opt in _flag_options(options, command):
        raw = getattr(args, opt.key.replace("-", "_"), None)
        if raw is None:
            continue
        if opt.merge is Merge.APPEND:
            overrides[opt.key] = tuple(raw)
        elif opt.merge is Merge.MAP_BY_KEY:
            overrides[opt.key] = dict(raw)
        else:
            overrides[opt.key] = raw
    return overrides


def _flag_options(options: Sequence[Option], command: str | None) -> list[Option]:
    """The options that own a flag on *command* (every command when None); one predicate for declaring and reading."""
    return [
        opt
        for opt in options
        if Layer.CLI in opt.layers
        and opt.cli is not None
        and (command is None or opt.commands is None or command in opt.commands)
    ]


def _text_type(opt: Option) -> Callable[[str], Any]:
    """Wrap *opt*'s text parser as an argparse ``type=``, turning a ConfigError into a usage error (exit 2)."""
    if opt.parse_text is None:
        raise ValueError(f"option {opt.key!r} has no parse_text")
    parse_text: Callable[[str, str], Any] = opt.parse_text
    origin = opt.cli_origin

    def parse(text: str) -> Any:
        try:
            return parse_text(text, origin)
        except ConfigError as e:
            raise argparse.ArgumentTypeError(e.message) from None

    return parse
