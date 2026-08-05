"""Settings IO reader: discover and securely read .mojox/config.toml files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mojox._settings_reader import read_settings


class TestDiscovery:
    def test_finds_project_config_at_git_root(self, tmp_path):
        """Config in the same dir as .git is found."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(tmp_path, env={})
        assert settings.jobs == 4

    def test_walks_up_to_git_boundary(self, tmp_path):
        """Config at repo root found when starting from a subdirectory."""
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "packages" / "mylib"
        subdir.mkdir(parents=True)
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 2\n")

        settings = read_settings(subdir, env={})
        assert settings.jobs == 2

    def test_nearest_config_wins(self, tmp_path):
        """When multiple configs exist, the one nearest to manifest_dir wins."""
        (tmp_path / ".git").mkdir()
        # Config at repo root
        root_config = tmp_path / ".mojox"
        root_config.mkdir()
        (root_config / "config.toml").write_text("jobs = 2\n")
        # Config in a subdirectory (nearer to manifest_dir)
        subdir = tmp_path / "packages"
        subdir.mkdir()
        sub_config = subdir / ".mojox"
        sub_config.mkdir()
        (sub_config / "config.toml").write_text("jobs = 8\n")

        settings = read_settings(subdir, env={})
        assert settings.jobs == 8

    def test_stops_at_inner_git_boundary(self, tmp_path):
        """A nested .git stops the walk -- outer config not found."""
        outer = tmp_path / "outer"
        outer.mkdir()
        (outer / ".git").mkdir()
        outer_config = outer / ".mojox"
        outer_config.mkdir()
        (outer_config / "config.toml").write_text("jobs = 8\n")

        inner = outer / "inner"
        inner.mkdir()
        (inner / ".git").mkdir()

        settings = read_settings(inner, env={})
        assert settings.jobs is None

    def test_no_git_means_no_project_config(self, tmp_path):
        """Without a .git ancestor, project-level config is not loaded."""
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(tmp_path, env={})
        assert settings.jobs is None

    def test_git_file_counts_as_boundary(self, tmp_path):
        """A .git file (worktree) is treated as a repo root."""
        (tmp_path / ".git").write_text("gitdir: /some/worktree\n")
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(tmp_path, env={})
        assert settings.jobs == 4

    def test_user_config_included(self, tmp_path, monkeypatch):
        """User-level config from XDG_CONFIG_HOME is loaded."""
        user_config_dir = tmp_path / "xdg" / "mojox"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.toml").write_text("timeout = 600\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

        no_project = tmp_path / "noproject"
        no_project.mkdir()

        settings = read_settings(no_project, env={})
        assert settings.timeout_s == 600

    def test_project_wins_over_user(self, tmp_path, monkeypatch):
        """Project config takes precedence over user config for the same key."""
        (tmp_path / ".git").mkdir()
        proj_config = tmp_path / ".mojox"
        proj_config.mkdir()
        (proj_config / "config.toml").write_text("jobs = 4\n")

        user_config_dir = tmp_path / "xdg" / "mojox"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.toml").write_text("jobs = 8\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

        settings = read_settings(tmp_path, env={})
        assert settings.jobs == 4

    def test_no_config_flag(self, tmp_path):
        """--no-config skips all config file discovery."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(tmp_path, env={}, no_config=True)
        assert settings.jobs is None

    def test_explicit_config_file(self, tmp_path):
        """--config-file replaces all discovery."""
        custom = tmp_path / "custom.toml"
        custom.write_text("jobs = 16\ntimeout = 120\n")

        settings = read_settings(tmp_path, env={}, config_file=custom)
        assert settings.jobs == 16
        assert settings.timeout_s == 120

    def test_no_config_returns_empty(self, tmp_path, monkeypatch):
        """When no config files exist, returns empty settings."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("HOME", raising=False)
        settings = read_settings(tmp_path, env={})
        assert settings.jobs is None
        assert settings.timeout_s is None


class TestSecurity:
    def test_world_writable_dir_refused(self, tmp_path):
        """Config under a world-writable directory is not loaded."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")
        config_dir.chmod(0o777)

        settings = read_settings(tmp_path, env={})
        assert settings.jobs is None
        config_dir.chmod(0o755)
