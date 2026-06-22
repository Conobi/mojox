"""Tests for Mojo toolchain detection (command + package-extension selection).

The build backend must target two incompatible toolchains from one codebase:

* Mojo < 1.0  → `mojo package`    producing `.mojopkg`
* Mojo >= 1.0 → `mojo precompile` producing `.mojoc`

These tests pin the version-string parsing and the legacy/modern branch point.
"""

from __future__ import annotations

import pytest

from mojox_build._toolchain import (
    Toolchain,
    detect_mojo_release,
    parse_mojo_version,
)


class TestParseMojoVersion:
    def test_parses_release_from_dev_nightly_string(self) -> None:
        out = "Mojo 0.26.3.0.dev2026042005 (32e188d3)"
        assert parse_mojo_version(out) == (0, 26, 3, 0)

    def test_parses_release_from_beta_string(self) -> None:
        out = "Mojo 1.0.0b2 (abc1234)"
        assert parse_mojo_version(out) == (1, 0, 0)

    def test_parses_release_from_stable_three_part_string(self) -> None:
        out = "Mojo 1.0.0 (abc1234)"
        assert parse_mojo_version(out) == (1, 0, 0)

    def test_parses_release_lowercase_without_hash(self) -> None:
        out = "mojo 0.26.2.0"
        assert parse_mojo_version(out) == (0, 26, 2, 0)

    def test_ignores_trailing_git_hash_token(self) -> None:
        # The parenthesised git hash must never be mistaken for the version.
        out = "Mojo 1.0.0b2 (32e188d3)"
        assert parse_mojo_version(out) == (1, 0, 0)

    def test_raises_when_no_version_token_present(self) -> None:
        with pytest.raises(ValueError):
            parse_mojo_version("no version here at all")


class TestToolchainFromRelease:
    def test_legacy_release_selects_package_and_mojopkg(self) -> None:
        tc = Toolchain.from_release((0, 26, 3, 0))
        assert tc.subcommand == "package"
        assert tc.extension == ".mojopkg"

    def test_one_zero_beta_selects_precompile_and_mojoc(self) -> None:
        tc = Toolchain.from_release((1, 0, 0))
        assert tc.subcommand == "precompile"
        assert tc.extension == ".mojoc"

    def test_future_major_selects_precompile_and_mojoc(self) -> None:
        tc = Toolchain.from_release((2, 1))
        assert tc.subcommand == "precompile"
        assert tc.extension == ".mojoc"


class TestDetectAgainstLocalMojo:
    """Integration: exercise the real `mojo --version` on PATH (no mocks)."""

    def test_detects_a_release_tuple(self) -> None:
        release = detect_mojo_release()
        assert isinstance(release, tuple)
        assert release  # non-empty
        assert all(isinstance(part, int) for part in release)

    def test_detect_round_trips_into_a_toolchain(self) -> None:
        tc = Toolchain.detect()
        assert tc.subcommand in {"package", "precompile"}
        assert tc.extension in {".mojopkg", ".mojoc"}
