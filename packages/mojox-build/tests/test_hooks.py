"""PEP 517/660 hooks: configuration errors surface as a clean fatal message."""

from __future__ import annotations

from pathlib import Path

import mojox_build
import pytest


@pytest.mark.parametrize("hook", [mojox_build.build_wheel, mojox_build.build_editable])
def test_unknown_build_profile_names_the_key(sample_pyproject: Path, monkeypatch, capsys, tmp_path, hook):
    pyproject = sample_pyproject / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace('package-root = "src"\n', 'package-root = "src"\nbuild-profile = "nope"\n')
    )
    monkeypatch.chdir(sample_pyproject)
    with pytest.raises(SystemExit) as exc:
        hook(str(tmp_path / "dist"))
    assert exc.value.code == 1
    assert "tool.mojox.build-profile: unknown profile 'nope'. Available: dev, release" in capsys.readouterr().err


@pytest.fixture
def fake_backend(monkeypatch):
    """Stub the compiler lookup, preflight and wheel assembly; returns the policies build_wheel received."""
    from mojox_core import Toolchain

    policies = []
    toolchain = Toolchain(mojo_path="/usr/bin/mojo", version="1.0.0", subcommand="precompile", extension=".mojoc")

    def fake_build_wheel(root, manifest, policy, toolchain, **kwargs):
        policies.append(policy)
        return "my_lib-1.0.0-py3-none-any.whl"

    monkeypatch.setattr("mojox_build.hooks.resolve_toolchain", lambda: toolchain)
    monkeypatch.setattr("mojox_build.hooks._preflight", lambda *args: None)
    monkeypatch.setattr("mojox_build.hooks.build_wheel", fake_build_wheel)
    return policies


def _add_to_tool_mojox(root: Path, line: str) -> None:
    """Append *line* to the sample project's [tool.mojox] table."""
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('package-root = "src"\n', f'package-root = "src"\n{line}'))


def test_unknown_key_warns_and_the_wheel_builds(sample_pyproject, monkeypatch, capsys, tmp_path, fake_backend):
    _add_to_tool_mojox(sample_pyproject, "compile-thread = 2\n")
    monkeypatch.chdir(sample_pyproject)
    (tmp_path / "meta").mkdir()
    assert mojox_build.prepare_metadata_for_build_wheel(str(tmp_path / "meta")).endswith(".dist-info")
    assert mojox_build.build_wheel(str(tmp_path / "dist")) == "my_lib-1.0.0-py3-none-any.whl"
    warning = (
        "mojox-build: warning: tool.mojox.compile-thread: unknown key `compile-thread`; did you mean `compile-threads`?"
    )
    assert capsys.readouterr().err.count(warning) == 2


def test_invalid_known_key_still_fails(sample_pyproject, monkeypatch, capsys, tmp_path, fake_backend):
    _add_to_tool_mojox(sample_pyproject, "optimize = 9\n")
    monkeypatch.chdir(sample_pyproject)
    with pytest.raises(SystemExit) as exc:
        mojox_build.build_wheel(str(tmp_path / "dist"))
    assert exc.value.code == 1
    assert "tool.mojox.optimize: must be 0–3, got 9" in capsys.readouterr().err


def test_environment_never_reaches_the_wheel_policy(sample_pyproject, monkeypatch, tmp_path, fake_backend):
    monkeypatch.chdir(sample_pyproject)
    mojox_build.build_wheel(str(tmp_path / "dist"))
    monkeypatch.setenv("MOJOX_COMPILE_THREADS", "3")
    monkeypatch.setenv("MOJOX_JOBS", "5")
    monkeypatch.setenv("MOJOX_TIMEOUT", "7")
    mojox_build.build_wheel(str(tmp_path / "dist"))
    assert fake_backend[0] == fake_backend[1]
    assert fake_backend[1].compile_threads == 8


def test_environment_never_reaches_the_editable_policy(sample_pyproject, monkeypatch, tmp_path, fake_backend):
    def fake_build_editable_wheel(root, manifest, policy, toolchain, **kwargs):
        fake_backend.append(policy)
        return "my_lib-1.0.0-py3-none-any.whl"

    monkeypatch.setattr("mojox_build.hooks.build_editable_wheel", fake_build_editable_wheel)
    monkeypatch.chdir(sample_pyproject)
    mojox_build.build_editable(str(tmp_path / "dist"))
    monkeypatch.setenv("MOJOX_COMPILE_THREADS", "3")
    monkeypatch.setenv("MOJOX_JOBS", "5")
    monkeypatch.setenv("MOJOX_TIMEOUT", "7")
    mojox_build.build_editable(str(tmp_path / "dist"))
    assert fake_backend[0] == fake_backend[1]
    assert fake_backend[1].compile_threads == 8
