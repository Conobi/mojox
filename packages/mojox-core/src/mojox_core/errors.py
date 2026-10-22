"""User-facing configuration errors with structured key paths."""

from __future__ import annotations

import unicodedata

# Control characters (C0, DEL, C1), format characters (bidi overrides such as
# U+202E), lone surrogates (undecodable filename bytes) and line/paragraph
# separators: printed raw, they can drive the terminal, spoof the text, or
# make printing raise UnicodeEncodeError.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def escape_for_display(text: str) -> str:
    """*text* unchanged when safe to print, else its ``repr()`` (which escapes every unsafe character).

    For display only: paths and origins stay raw as data.
    """
    if any(unicodedata.category(c) in _UNSAFE_CATEGORIES for c in text):
        return repr(text)
    return text


class ConfigError(Exception):
    """A configuration error naming where the bad value came from, plus a remediation.

    ``key_path`` is the origin (``tool.mojox.optimize``, ``<settings file>:jobs``,
    ``MOJOX_JOBS``, ``--jobs``), and the error renders as ``<origin>: <message>``
    so it reads the same in every layer. No KeyError, no TypeError, no stack
    trace reaches the user — every rejection produces one of these.

    ``key_path`` and ``message`` stay raw; the rendering escapes each through
    :func:`escape_for_display`, so a path holding terminal escapes, bidi
    overrides or undecodable bytes prints safely wherever the error was raised.
    """

    def __init__(self, key_path: str, message: str) -> None:
        self.key_path = key_path
        self.message = message
        super().__init__(f"{key_path}: {message}")

    def __str__(self) -> str:
        return f"{escape_for_display(self.key_path)}: {escape_for_display(self.message)}"
