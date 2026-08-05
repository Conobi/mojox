"""User-facing configuration errors with structured key paths."""

from __future__ import annotations


class ConfigError(Exception):
    """A configuration error carrying the offending key path and a remediation.

    No KeyError, no TypeError, no stack trace reaches the user — every
    rejection produces one of these with a human-readable message.
    """

    def __init__(self, key_path: str, message: str) -> None:
        self.key_path = key_path
        self.message = message
        super().__init__(f"[{key_path}] {message}")
