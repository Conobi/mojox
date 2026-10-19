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
    assert "[tool.mojox.build-profile] unknown profile 'nope'. Available: dev, release" in capsys.readouterr().err
