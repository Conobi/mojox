"""The CLI plans with the host's PATH and keys cached builds on the C compiler's identity."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from mojox.cli import _make_cache_context, _resolve_pipeline, build_parser


def _executable(path: Path, text: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A one-test project wired to a fake ``mojo`` in ``tmp_path/venv/bin``."""
    from mojox_core import Toolchain

    proj = tmp_path / "proj"
    (proj / "tests").mkdir(parents=True)
    (proj / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
    (proj / "tests" / "test_a.mojo").write_text("def test_a():\n    pass\n")
    mojo = _executable(tmp_path / "venv" / "bin" / "mojo", f"#!{sys.executable}\n")
    toolchain = Toolchain(mojo_path=str(mojo), version="1.1.0", subcommand="precompile", extension=".mojoc")
    monkeypatch.setattr("mojox_core.io.toolchain.resolve", lambda: toolchain)
    monkeypatch.setattr("mojox_core.io.environment.read_distributions", list)
    monkeypatch.setenv("MOJOX_CPU_COUNT", "2")
    monkeypatch.chdir(proj)
    return proj


def _pipeline():
    return _resolve_pipeline(build_parser().parse_args(["test", "--no-config"]))


def _context(host):
    return _make_cache_context(
        lib_paths=(), test_roots=(), implicit_dirs=(), compiler_version="1.1.0", no_cache=False, host=host
    )


def test_planned_path_leads_with_mojo_then_the_host_cc_dir(project, tmp_path, monkeypatch):
    cc = _executable(tmp_path / "nix" / "bin" / "cc")
    monkeypatch.setenv("PATH", f"{cc.parent}:.:bin:{project}/bin")
    *_, host, _settings, commands, _includes = _pipeline()
    assert host.cc_realpath == str(cc.resolve())
    assert commands, "the plan must contain commands"
    for command in commands:
        entries = command.env["PATH"].split(":")
        assert entries[:2] == [str(tmp_path / "venv" / "bin"), str(cc.parent)]
        assert "." not in entries
        assert "bin" not in entries


def test_forwarded_variables_reach_the_planned_commands(project, monkeypatch):
    monkeypatch.setenv("NIX_CC", "/nix/store/x-gcc-wrapper")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/evil")
    monkeypatch.setenv("NIX_CONFIG", "access-tokens = github.com=ghp_SECRET")
    *_, commands, _includes = _pipeline()
    for command in commands:
        assert command.env["NIX_CC"] == "/nix/store/x-gcc-wrapper"
        assert "LD_LIBRARY_PATH" not in command.env
        assert "NIX_CONFIG" not in command.env


def test_cache_context_carries_the_host_identity(project, tmp_path, monkeypatch):
    cc = _executable(tmp_path / "nix" / "bin" / "cc")
    monkeypatch.setenv("PATH", str(cc.parent))
    monkeypatch.setenv("NIX_CC", "/nix/store/x-gcc-wrapper")
    *_, host, _settings, _commands, _includes = _pipeline()
    ctx = _context(host)
    assert ctx.cc_realpath == str(cc.resolve())
    assert ctx.nix_cc == "/nix/store/x-gcc-wrapper"


def test_toolchain_switch_changes_the_test_key(project, tmp_path, monkeypatch):
    from mojox.exec import _build_test_cache_key
    from mojox_core import CommandKind

    cc_a = _executable(tmp_path / "a" / "bin" / "cc")
    cc_b = _executable(tmp_path / "b" / "bin" / "cc")
    monkeypatch.setenv("PATH", str(cc_a.parent))
    *_, host, _settings, commands, _includes = _pipeline()
    cmd = next(c for c in commands if c.kind == CommandKind.BUILD_TEST)
    key_a = _build_test_cache_key(cmd, _context(host), None)
    assert key_a is not None
    assert _build_test_cache_key(cmd, _context(host), None) == key_a
    switched = replace(host, cc_realpath=str(cc_b.resolve()))
    assert _build_test_cache_key(cmd, _context(switched), None) != key_a


def test_metadata_prints_names_and_no_values(project, monkeypatch, capsys):
    import json

    from mojox.cli import _cmd_metadata

    monkeypatch.setenv("NIX_CC", "s3cr3t-token-value")
    monkeypatch.setenv("TMPDIR", "s3cr3t-token-value")
    _cmd_metadata(build_parser().parse_args(["metadata", "--no-config"]))
    out = capsys.readouterr().out
    assert "s3cr3t-token-value" not in out
    assert {"NIX_CC", "PATH", "TMPDIR"} <= set(json.loads(out)["host"]["forwarded_env"])
    assert os.environ["NIX_CC"] == "s3cr3t-token-value"
