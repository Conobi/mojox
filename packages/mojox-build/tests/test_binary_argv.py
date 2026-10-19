"""Wheel binaries compile with the build profile's codegen flags, never host-derived ones."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from mojox_build import build
from mojox_core import LocalSettings, Policy, Toolchain, parse_manifest, resolve
from mojox_core.io.manifest import read as read_manifest

TOOLCHAIN = Toolchain(mojo_path="/usr/bin/mojo", version="1.0.0b2", subcommand="precompile", extension=".mojoc")


def _policy(root: Path, extra: str = "") -> Policy:
    """Resolve the policy the wheel hook would use, after appending *extra* to [tool.mojox]."""
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('package-root = "src"\n', f'package-root = "src"\n{extra}'))
    manifest = parse_manifest(read_manifest(pyproject))
    return resolve(manifest, manifest.build_profile, {}, LocalSettings.EMPTY)


def _argv(root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run _compile_binary with subprocess.run captured; return the argv it would execute."""
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build.subprocess, "run", fake_run)
    build._compile_binary(root, "src/mylib/main.mojo", root / "out", policy, TOOLCHAIN, verbose=False)
    assert len(seen) == 1
    return seen[0]


def _pair(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_default_release_profile(sample_pyproject: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    argv = _argv(sample_pyproject, _policy(sample_pyproject, 'flags = ["--extra"]\n'), monkeypatch)
    assert "-O3" in argv
    assert "--debug-level" not in argv  # release is "none", which emits nothing
    assert _pair(argv, "--num-threads") == "8"
    assert _pair(argv, "-D") == "ASSERT=safe"
    assert any(a.endswith("/modular/lib") for a in argv)
    assert argv[-1] == "--extra"
    # Same relative order as `mojox build`: -O, -D, --num-threads, then flags.
    assert argv.index("-O3") < argv.index("-D") < argv.index("--num-threads") < argv.index("--extra")


def test_manifest_profile_overrides(sample_pyproject: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    extra = (
        'build-profile = "ship"\n[tool.mojox.profile.ship]\noptimize = 1\ndebug-level = "full"\ncompile-threads = 3\n'
    )
    argv = _argv(sample_pyproject, _policy(sample_pyproject, extra), monkeypatch)
    assert "-O1" in argv
    assert "-O3" not in argv
    assert _pair(argv, "--debug-level") == "full"
    assert _pair(argv, "--num-threads") == "3"


def test_argv_ignores_host_cpu_count(sample_pyproject: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    argvs = []
    for cpus in (1, 64):
        monkeypatch.setattr("os.cpu_count", lambda cpus=cpus: cpus)
        argvs.append(_argv(sample_pyproject, _policy(sample_pyproject), monkeypatch))
    assert argvs[0] == argvs[1]
