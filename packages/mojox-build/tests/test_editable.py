"""Editable installs: per-distribution qualification, stale symlink purge."""

from __future__ import annotations

import json

from mojox_build._build import _write_editable_hook


class TestEditableHookFiles:
    def test_per_distribution_filenames(self, tmp_path):
        """Editable files are qualified by distribution name."""
        pkg = tmp_path / "src" / "mylib"
        pkg.mkdir(parents=True)
        (pkg / "main.mojo").write_text("fn main(): pass\n")

        platlib = tmp_path / "platlib"
        platlib.mkdir()
        _write_editable_hook(platlib, [pkg], "my_lib")

        assert (platlib / "_mojox_editable_my_lib.pth").is_file()
        assert (platlib / "_mojox_editable_my_lib_hook.py").is_file()
        assert (platlib / "_mojox_editable_my_lib_manifest.json").is_file()

    def test_two_distributions_coexist(self, tmp_path):
        """Two distributions can write editable hooks without clobbering."""
        pkg_a = tmp_path / "src_a" / "lib_a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "a.mojo").write_text("fn a(): pass\n")

        pkg_b = tmp_path / "src_b" / "lib_b"
        pkg_b.mkdir(parents=True)
        (pkg_b / "b.mojo").write_text("fn b(): pass\n")

        platlib = tmp_path / "platlib"
        platlib.mkdir()

        _write_editable_hook(platlib, [pkg_a], "lib_a")
        _write_editable_hook(platlib, [pkg_b], "lib_b")

        # Both manifests exist
        manifest_a = json.loads((platlib / "_mojox_editable_lib_a_manifest.json").read_text())
        manifest_b = json.loads((platlib / "_mojox_editable_lib_b_manifest.json").read_text())
        assert "lib_a" in manifest_a["packages"]
        assert "lib_b" in manifest_b["packages"]

        # Both .pth files exist
        assert (platlib / "_mojox_editable_lib_a.pth").is_file()
        assert (platlib / "_mojox_editable_lib_b.pth").is_file()

    def test_manifest_contains_resolved_paths(self, tmp_path):
        """The manifest records resolved absolute paths to source dirs."""
        pkg = tmp_path / "src" / "mylib"
        pkg.mkdir(parents=True)
        (pkg / "main.mojo").write_text("fn main(): pass\n")

        platlib = tmp_path / "platlib"
        platlib.mkdir()
        _write_editable_hook(platlib, [pkg], "my_lib")

        manifest = json.loads((platlib / "_mojox_editable_my_lib_manifest.json").read_text())
        assert manifest["packages"]["mylib"] == str(pkg.resolve())

    def test_pth_imports_correct_hook(self, tmp_path):
        """The .pth file imports the per-distribution hook module."""
        pkg = tmp_path / "src" / "mylib"
        pkg.mkdir(parents=True)

        platlib = tmp_path / "platlib"
        platlib.mkdir()
        _write_editable_hook(platlib, [pkg], "my_lib")

        pth_content = (platlib / "_mojox_editable_my_lib.pth").read_text()
        assert "import _mojox_editable_my_lib_hook" in pth_content
