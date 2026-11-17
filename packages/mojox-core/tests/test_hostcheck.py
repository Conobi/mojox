"""``python -m mojox_core.io.hostcheck``: the toolchain facts the Nix shell self-check reads."""

from __future__ import annotations

import io

import pytest
from mojox_core.errors import ConfigError
from mojox_core.io import hostcheck
from mojox_core.types import Toolchain


def _executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def _run(tmp_path, monkeypatch, environ, *, version="0.7.0"):
    mojo = tmp_path / "venv" / "bin" / "mojo"
    monkeypatch.setattr(
        hostcheck,
        "resolve_toolchain",
        lambda: Toolchain(mojo_path=str(mojo), version="1.1.0", subcommand="precompile", extension=".mojoc"),
    )
    monkeypatch.setattr(hostcheck, "_installed_version", lambda _name: version)
    out = io.StringIO()
    status = hostcheck.main(["--root", str(tmp_path / "proj")], environ=environ, stdout=out)
    pairs = [line.split("=", 1) for line in out.getvalue().splitlines()]
    return status, dict(pairs), [key for key, _ in pairs], mojo


def test_reports_the_toolchain_the_planner_would_use(tmp_path, monkeypatch):
    cc = _executable(tmp_path / "nix" / "bin" / "cc")
    status, facts, order, mojo = _run(tmp_path, monkeypatch, {"PATH": f"{cc.parent}:.:{tmp_path}/proj/bin"})
    assert status == 0
    assert order == ["mojo", "mojo_version", "cc", "path", "mojox"]
    assert facts["mojo"] == str(mojo)
    assert facts["mojo_version"] == "1.1.0"
    assert facts["cc"] == str(cc.resolve())
    assert facts["path"].split(":")[:2] == [str(mojo.parent), str(cc.parent)]
    assert "." not in facts["path"].split(":")
    assert facts["mojox"] == "0.7.0"


def test_a_project_local_cc_is_not_reported(tmp_path, monkeypatch):
    planted = _executable(tmp_path / "proj" / "bin" / "cc")
    monkeypatch.chdir(tmp_path / "proj")
    _, facts, _, _ = _run(tmp_path, monkeypatch, {"PATH": "bin:."})
    assert not facts["cc"].startswith(str(planted.parent))


def test_a_missing_mojox_distribution_prints_an_empty_version(tmp_path, monkeypatch):
    _, facts, _, _ = _run(tmp_path, monkeypatch, {"PATH": "/usr/bin"}, version="")
    assert facts["mojox"] == ""


def test_exit_status_2_and_a_message_when_no_compiler_is_installed(tmp_path, monkeypatch, capsys):
    def missing():
        raise ConfigError("toolchain", "mojo-compiler is not installed. Install it with: uv add mojo-compiler")

    monkeypatch.setattr(hostcheck, "resolve_toolchain", missing)
    out = io.StringIO()
    assert hostcheck.main(["--root", str(tmp_path)], environ={}, stdout=out) == 2
    assert out.getvalue() == ""
    assert "mojo-compiler is not installed" in capsys.readouterr().err


def test_a_bad_cpu_count_override_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        hostcheck,
        "resolve_toolchain",
        lambda: Toolchain(mojo_path="/venv/bin/mojo", version="1.1.0", subcommand="precompile", extension=".mojoc"),
    )
    assert hostcheck.main(["--root", str(tmp_path)], environ={"MOJOX_CPU_COUNT": "0"}, stdout=io.StringIO()) == 2
    assert "MOJOX_CPU_COUNT" in capsys.readouterr().err


def test_unknown_arguments_exit_through_argparse(tmp_path):
    with pytest.raises(SystemExit):
        hostcheck.main(["--nope"], environ={}, stdout=io.StringIO())
