"""Configuration options declared once: where each may be set, how it parses and merges.

The manifest, settings, ``MOJOX_*`` and CLI parsers and the resolver all read
this registry, so a new option is one :func:`option` entry plus the code that
consumes its value.

Pure: no environment or file access; every input is passed in.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal

from .errors import ConfigError
from .types import LintConfig, Value


class Layer(Enum):
    """A place a value can come from, lowest precedence first; the values name it in errors."""

    TOP = "tool.mojox"
    PROFILE = "tool.mojox.profile.<name>"
    SETTINGS = "local settings"
    ENV = "environment"
    CLI = "command line"


class Merge(Enum):
    """How a value combines with the one from the layer below it."""

    REPLACE = "replace"
    MAP_BY_KEY = "map-by-key"
    APPEND = "append"


@dataclass(frozen=True)
class Option:
    """One configuration option; build it with :func:`option`, which fills in ``env`` and ``cli``.

    ``parse`` validates a TOML-shaped value and names its origin in the
    ConfigError. ``parse_text`` parses one environment value or one CLI
    occurrence: the whole value for REPLACE, one element for APPEND, one
    ``(key, value)`` pair for MAP_BY_KEY. The fold starts from
    ``builtin[profile]`` for a built-in profile, else from ``default``.
    Both are left out of the hash, since they may hold dicts.
    """

    key: str
    parse: Callable[[Any, str], Any]
    layers: frozenset[Layer]
    merge: Merge = Merge.REPLACE
    default: Any = field(default=None, hash=False)
    builtin: Mapping[str, Any] = field(default_factory=dict, hash=False)
    parse_text: Callable[[str, str], Any] | None = None
    cli: tuple[str, ...] | None = None
    commands: frozenset[str] | None = None
    env: str | None = None
    metavar: str | None = None
    doc: str = ""

    @property
    def is_bool(self) -> bool:
        """Bool options become ``--x/--no-x`` flags and read ``true``/``false``/``1``/``0`` from the environment."""
        return self.parse is parse_bool_strict

    @property
    def cli_origin(self) -> str:
        """The origin of a CLI value: the first long flag, whichever spelling was typed.

        Raises:
            ValueError: The option has no long flag (not a CLI option).
        """
        for name in self.cli or ():
            if name.startswith("--"):
                return name
        raise ValueError(f"option {self.key!r} has no long flag")


def option(
    key: str,
    parse: Callable[[Any, str], Any],
    layers: Iterable[Layer],
    *,
    merge: Merge = Merge.REPLACE,
    default: Any = None,
    builtin: Mapping[str, Any] | None = None,
    parse_text: Callable[[str, str], Any] | None = None,
    cli: tuple[str, ...] | None = None,
    commands: Iterable[str] | None = None,
    metavar: str | None = None,
    doc: str = "",
) -> Option:
    """Build an :class:`Option`, deriving its ``MOJOX_*`` name and default ``--<key>`` flag.

    The env name exists only when ENV is allowed. *cli*, when given, is the
    complete flag list: ``--<key>`` is not added. *commands* None means every
    subcommand that takes option flags.

    Raises:
        ValueError: ENV or CLI is allowed but a non-bool option has no
            *parse_text*, *cli* is given without the CLI layer, or *cli*
            is an empty tuple.
    """
    allowed = frozenset(layers)
    if parse is parse_bool_strict and parse_text is None:
        parse_text = parse_bool_text
    if (Layer.ENV in allowed or Layer.CLI in allowed) and parse_text is None:
        raise ValueError(f"option {key!r} can be set from the environment or CLI but has no parse_text")
    if cli is not None and Layer.CLI not in allowed:
        raise ValueError(f"option {key!r} names CLI flags but does not allow the CLI layer")
    if cli == ():
        raise ValueError(f"option {key!r} allows the CLI layer, so it needs at least one flag")
    return Option(
        key=key,
        parse=parse,
        layers=allowed,
        merge=merge,
        default=default,
        builtin=MappingProxyType(dict(builtin or {})),
        parse_text=parse_text,
        cli=(cli if cli is not None else (f"--{key}",)) if Layer.CLI in allowed else None,
        commands=frozenset(commands) if commands is not None else None,
        env=f"MOJOX_{key.upper().replace('-', '_')}" if Layer.ENV in allowed else None,
        metavar=metavar,
        doc=doc,
    )


# Top-level [tool.mojox] keys that manifest.py parses itself; valid at TOP only.
STRUCTURAL_KEYS: frozenset[str] = frozenset(
    {
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
)


def registry(*options: Option) -> tuple[Option, ...]:
    """Return *options* as a registry once keys, ``MOJOX_*`` names and flags are known to be unambiguous.

    Raises:
        ValueError: A duplicate key, env name or flag; a key that is also a
            structural manifest key; or a CLI option without a long flag.
    """
    seen: set[str] = set()
    for opt in options:
        if opt.key in STRUCTURAL_KEYS:
            raise ValueError(f"option {opt.key!r} is also a structural manifest key")
        if opt.cli is not None and not any(name.startswith("--") for name in opt.cli):
            raise ValueError(f"option {opt.key!r} has no long flag")
        for name in (opt.key, opt.env, *(opt.cli or ())):
            if name is None:
                continue
            if name in seen:
                raise ValueError(f"duplicate option name {name!r}")
            seen.add(name)
    return options


def by_key(options: Sequence[Option]) -> dict[str, Option]:
    """Index *options* by key, keeping registry order."""
    return {opt.key: opt for opt in options}


# -- Value parsers ------------------------------------------------------------


def parse_bool_strict(value: object, origin: str) -> bool:
    """Accept only a real TOML boolean, so ``"yes"`` or ``1`` is an error rather than truthy."""
    if isinstance(value, bool):
        return value
    raise ConfigError(origin, f"expected true or false, got {value!r}")


_BOOL_TEXT = {"true": True, "1": True, "false": False, "0": False}


def parse_bool_text(text: str, origin: str) -> bool:
    """Parse ``true``/``false``/``1``/``0`` (any case, surrounding whitespace ignored) from the environment."""
    try:
        return _BOOL_TEXT[text.strip().lower()]
    except KeyError:
        raise ConfigError(origin, f"expected true, false, 1 or 0, got {text!r}") from None


NUM_CPUS: Literal["num-cpus"] = "num-cpus"
_INT_TEXT = re.compile(r"[+-]?[0-9]+")
_JOBS_FORMS = 'a positive integer, a negative integer (CPUs to leave free), or "num-cpus"'


def _int_from_text(text: str) -> int | None:
    """Parse ASCII decimal digits only; None for anything else (incl. ``1_000``, ``4.0``, huge strings)."""
    stripped = text.strip()
    if not _INT_TEXT.fullmatch(stripped):
        return None
    try:
        return int(stripped)
    except ValueError:  # beyond the interpreter's int-from-str digit limit
        return None


def parse_jobs(value: object, key_path: str) -> int | Literal["num-cpus"]:
    """Validate a jobs spec from TOML, the environment, or the CLI.

    Accepts an int other than 0, its decimal text, or ``"num-cpus"``
    (case-sensitive, surrounding whitespace ignored). Negative values mean
    "all CPUs but k" and are resolved later by :func:`mojox_core.policy.resolve_jobs`.

    Raises:
        ConfigError: At *key_path* for 0, bools, floats and anything else.
    """
    if isinstance(value, bool):  # bool is an int subclass: reject first
        raise ConfigError(key_path, f"expected {_JOBS_FORMS}, got {value!r}")
    if isinstance(value, int):
        jobs: int | None = value
    elif isinstance(value, str):
        if value.strip() == NUM_CPUS:
            return NUM_CPUS
        jobs = _int_from_text(value)
    else:
        jobs = None
    if jobs is None:
        raise ConfigError(key_path, f"expected {_JOBS_FORMS}, got {value!r}")
    if jobs == 0:
        raise ConfigError(key_path, 'jobs must not be 0; use 1 for serial or "num-cpus"')
    return jobs


def parse_count(value: object, key_path: str, *, from_text: bool) -> int:
    """Validate a count that must be an integer >= 1 (compile-threads, timeout).

    With *from_text* (environment, CLI) the value is decimal text; otherwise
    (TOML) it must be a real int, so a quoted ``"2"`` is rejected.

    Raises:
        ConfigError: At *key_path* for anything but an integer >= 1.
    """
    if from_text and isinstance(value, str):
        return parse_count_text(value, key_path)
    if not from_text and isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    raise ConfigError(key_path, f"expected an integer >= 1, got {value!r}")


def parse_count_text(text: str, key_path: str) -> int:
    """Parse decimal text (environment, CLI) naming an integer >= 1; surrounding whitespace is ignored.

    Raises:
        ConfigError: At *key_path* for anything else, including ``""``.
    """
    count = _int_from_text(text)
    if count is None or count < 1:
        raise ConfigError(key_path, f"expected an integer >= 1, got {text!r}")
    return count


def parse_count_toml(value: object, origin: str) -> int:
    """TOML form of :func:`parse_count`: a real integer >= 1, so ``"2"`` is rejected."""
    return parse_count(value, origin, from_text=False)


def parse_optimize(value: object, origin: str) -> int:
    """Accept an integer 0-3; strings, floats and booleans are errors, not coerced."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 3:
        return value
    raise ConfigError(origin, f"must be 0–3, got {value!r}")


_DEBUG_LEVELS = ("none", "line-tables", "full")


def parse_debug_level(value: object, origin: str) -> str:
    """Accept ``none``, ``line-tables`` or ``full``."""
    if isinstance(value, str) and value in _DEBUG_LEVELS:
        return value
    raise ConfigError(origin, f"must be one of {sorted(_DEBUG_LEVELS)}, got {value!r}")


def parse_defines(value: object, origin: str) -> dict[str, str]:
    """Accept a table; keys and values are coerced with ``str()``, as before the registry."""
    if not isinstance(value, Mapping):
        raise ConfigError(origin, f"expected a table, got {type(value).__name__}")
    return {str(key): str(item) for key, item in value.items()}


def parse_define_text(text: str, origin: str) -> tuple[str, str]:
    """Split one ``KEY=VALUE`` occurrence at the first ``=``; a bare ``KEY`` defines it as ``""``."""
    key, _sep, value = text.partition("=")
    return key, value


def parse_flags(value: object, origin: str) -> tuple[str, ...]:
    """Accept a list of strings (a tuple too: CLI values are re-validated with this parser)."""
    if not isinstance(value, (list, tuple)):
        raise ConfigError(origin, f"expected a list, got {type(value).__name__}")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(f"{origin}[{index}]", f"expected a string, got {type(item).__name__}")
    return tuple(value)


def parse_flag_text(text: str, origin: str) -> str:
    """One ``--flag`` occurrence, passed to the compiler untouched."""
    return text


_LINT_LEVELS = {
    "warnings": "error",
    "check-doc-strings": "check",
    "missing-doc-strings": "warn",
    "unstable-apis": "warn",
}


def parse_lints(value: object, origin: str) -> LintConfig:
    """Parse the ``lints`` table; a bad sub-key is reported at ``<origin>.<sub-key>``, unknown sub-keys are ignored."""
    if not isinstance(value, Mapping):
        raise ConfigError(origin, f"expected a table, got {type(value).__name__}")
    for sub_key, level in _LINT_LEVELS.items():
        if sub_key in value and value[sub_key] != level:
            raise ConfigError(f"{origin}.{sub_key}", f"must be {level!r}, got {value[sub_key]!r}")
    return LintConfig(
        warnings_as_errors=value.get("warnings") == "error",
        check_doc_strings=value.get("check-doc-strings") == "check",
        missing_doc_strings=value.get("missing-doc-strings") == "warn",
        unstable_apis=value.get("unstable-apis") == "warn",
    )


def parse_env_table(value: object, origin: str) -> dict[str, str]:
    """Accept a table of strings: variables added to every command mojox runs."""
    if not isinstance(value, Mapping):
        raise ConfigError(origin, f"expected a table of strings, got {type(value).__name__}")
    for name, item in value.items():
        if not isinstance(item, str):
            raise ConfigError(f"{origin}.{name}", f"expected a string, got {type(item).__name__}")
    return {str(name): item for name, item in value.items()}


# -- Registry -----------------------------------------------------------------

# Order is the relative order of the option flags in --help.
OPTIONS: tuple[Option, ...] = registry(
    option(
        "jobs",
        parse_jobs,
        {Layer.SETTINGS, Layer.ENV, Layer.CLI},
        default=1,
        parse_text=parse_jobs,
        cli=("--jobs", "-j"),
        doc='Maximum concurrent compilations: N, "num-cpus", or -K for all CPUs but K',
    ),
    option(
        "compile-threads",
        parse_count_toml,
        {Layer.TOP, Layer.PROFILE, Layer.SETTINGS, Layer.ENV, Layer.CLI},
        default=1,
        builtin={"dev": 1, "release": 8},
        parse_text=parse_count_text,
        metavar="N",
        doc="Threads each mojo build may use (--num-threads; default from the profile: dev 1, release 8)",
    ),
    option(
        "timeout",
        parse_count_toml,
        {Layer.TOP, Layer.PROFILE, Layer.SETTINGS, Layer.ENV, Layer.CLI},
        default=300,
        parse_text=parse_count_text,
        doc="Per-target timeout in seconds",
    ),
    option(
        "defines",
        parse_defines,
        {Layer.TOP, Layer.PROFILE, Layer.CLI},
        merge=Merge.MAP_BY_KEY,
        default={},
        builtin={"dev": {"ASSERT": "all"}, "release": {"ASSERT": "safe"}},
        parse_text=parse_define_text,
        cli=("-D", "--define"),
        doc="Define a compile-time variable (KEY=VALUE)",
    ),
    option(
        "flags",
        parse_flags,
        {Layer.TOP, Layer.PROFILE, Layer.CLI},
        merge=Merge.APPEND,
        default=(),
        builtin={"dev": (), "release": ()},
        parse_text=parse_flag_text,
        cli=("--flag",),
        doc="Extra flag passed through to the compiler (use --flag=VALUE for dash-prefixed flags)",
    ),
    option("optimize", parse_optimize, {Layer.TOP, Layer.PROFILE}, default=0, builtin={"dev": 0, "release": 3}),
    option(
        "debug-level",
        parse_debug_level,
        {Layer.TOP, Layer.PROFILE},
        default="none",
        builtin={"dev": "line-tables", "release": "none"},
    ),
    option("test-parallel", parse_bool_strict, {Layer.TOP}, default=False),
    option("lints", parse_lints, {Layer.TOP}, default=LintConfig()),
    option("env", parse_env_table, {Layer.SETTINGS}, merge=Merge.MAP_BY_KEY, default={}),
)


# -- Fold ---------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """The fold's output: plain values by option key, and their origins in ``Policy.origins`` form.

    ``origins`` maps a REPLACE key to the winning origin, an APPEND key to one
    origin per element, and ``"<key>.<map key>"`` to each MAP_BY_KEY entry's
    origin (an empty map contributes no entry).
    """

    values: Mapping[str, Any]
    origins: Mapping[str, str | tuple[str, ...]]


def resolve_values(
    sources: Sequence[Mapping[str, Value]],
    profile_name: str,
    *,
    options: Sequence[Option] = OPTIONS,
) -> Resolution:
    """Fold each option over *sources* (lowest precedence first) by its merge kind.

    The fold starts from ``builtin[profile_name]`` (origin ``builtin:<profile>``)
    or else from ``default`` (origin ``default``). Sources are trusted: their
    values were parsed and allow-listed when the source was built.
    """
    values: dict[str, Any] = {}
    origins: dict[str, str | tuple[str, ...]] = {}
    for opt in options:
        if profile_name in opt.builtin:
            start = Value(opt.builtin[profile_name], f"builtin:{profile_name}")
        else:
            start = Value(opt.default, "default")
        found = [source[opt.key] for source in sources if opt.key in source]
        if opt.merge is Merge.REPLACE:
            winner = found[-1] if found else start
            values[opt.key] = winner.value
            origins[opt.key] = winner.origin
        elif opt.merge is Merge.APPEND:
            items = [(item, layer.origin) for layer in (start, *found) for item in layer.value]
            values[opt.key] = tuple(item for item, _origin in items)
            origins[opt.key] = tuple(origin for _item, origin in items)
        else:
            entries = {key: (item, start.origin) for key, item in start.value.items()}
            for layer in found:
                entries.update({key: (item, f"{layer.origin}.{key}") for key, item in layer.value.items()})
            values[opt.key] = {key: item for key, (item, _origin) in entries.items()}
            origins.update({f"{opt.key}.{key}": origin for key, (_item, origin) in entries.items()})
    return Resolution(values=values, origins=origins)
