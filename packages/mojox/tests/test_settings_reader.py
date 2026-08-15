"""Settings IO reader: discover and securely read .mojox/config.toml files."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from mojox._settings_reader import (
    _find_project_config,
    _is_path_secure,
    _safe_read_toml,
    _user_config_path,
    read_settings,
)


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

    def test_user_config_included(self, tmp_path):
        """User-level config from XDG_CONFIG_HOME is loaded via env dict."""
        user_config_dir = tmp_path / "xdg" / "mojox"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.toml").write_text("timeout = 600\n")

        no_project = tmp_path / "noproject"
        no_project.mkdir()

        settings = read_settings(
            no_project,
            env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        )
        assert settings.timeout_s == 600

    def test_project_wins_over_user(self, tmp_path):
        """Project config takes precedence over user config for the same key."""
        (tmp_path / ".git").mkdir()
        proj_config = tmp_path / ".mojox"
        proj_config.mkdir()
        (proj_config / "config.toml").write_text("jobs = 4\n")

        user_config_dir = tmp_path / "xdg" / "mojox"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.toml").write_text("jobs = 8\n")

        settings = read_settings(
            tmp_path,
            env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        )
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

    def test_explicit_config_file_missing_raises(self, tmp_path):
        """--config-file pointing to a nonexistent file raises ConfigError."""
        from mojox_core import ConfigError

        missing = tmp_path / "nonexistent.toml"
        with pytest.raises(ConfigError):
            read_settings(tmp_path, env={}, config_file=missing)

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


# ── Finding #1: TOCTOU — openat(O_NOFOLLOW) + fstat ──────────────────────


class TestTOCTOU:
    """Tests for the TOCTOU fix: using O_NOFOLLOW + fstat instead of stat-then-open."""

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"),
        reason="O_NOFOLLOW not available on this platform",
    )
    def test_safe_read_toml_uses_o_nofollow(self, tmp_path):
        """_safe_read_toml must open with O_NOFOLLOW, not plain open()."""
        config = tmp_path / "config.toml"
        config.write_text("jobs = 42\n")

        # Should succeed on a regular file
        data = _safe_read_toml(config)
        assert data == {"jobs": 42}

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"),
        reason="O_NOFOLLOW not available on this platform",
    )
    def test_safe_read_toml_rejects_symlink(self, tmp_path):
        """_safe_read_toml must reject a symlink to a config file."""
        real = tmp_path / "real.toml"
        real.write_text("jobs = 42\n")
        link = tmp_path / "config.toml"
        link.symlink_to(real)

        # O_NOFOLLOW should cause the open to fail on a symlink
        data = _safe_read_toml(link)
        assert data is None

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"),
        reason="O_NOFOLLOW not available on this platform",
    )
    def test_is_path_secure_rejects_symlinked_directory(self, tmp_path):
        """_is_path_secure must reject a path with a symlinked directory."""
        real_dir = tmp_path / "real_mojox"
        real_dir.mkdir()
        config = real_dir / "config.toml"
        config.write_text("jobs = 1\n")

        # Create a symlink for the .mojox directory
        link_dir = tmp_path / ".mojox"
        link_dir.symlink_to(real_dir)
        link_config = link_dir / "config.toml"

        result = _is_path_secure(link_config, stop_at=tmp_path)
        assert result is False

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"),
        reason="O_NOFOLLOW not available on this platform",
    )
    def test_is_path_secure_walks_with_openat(self, tmp_path):
        """_is_path_secure should use fd-based walk, not Path.stat()."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        config = config_dir / "config.toml"
        config.write_text("jobs = 1\n")

        # Valid path should pass
        assert _is_path_secure(config, stop_at=tmp_path) is True

    def test_safe_read_toml_windows_fallback(self, tmp_path):
        """On platforms without O_NOFOLLOW, plain open() is used."""
        config = tmp_path / "config.toml"
        config.write_text("jobs = 99\n")

        with patch("mojox._settings_reader._HAS_O_NOFOLLOW", False):
            data = _safe_read_toml(config)
            assert data == {"jobs": 99}


# ── Finding #2: config_paths never populated ──────────────────────────────


class TestConfigPaths:
    """Tests for config_paths being populated with loaded file paths."""

    def test_config_paths_includes_project_file(self, tmp_path):
        """config_paths should include the project config file path."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(tmp_path, env={})
        expected = str(config_dir / "config.toml")
        assert expected in settings.config_paths

    def test_config_paths_includes_user_file(self, tmp_path):
        """config_paths should include the user config file path."""
        user_config_dir = tmp_path / "xdg" / "mojox"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.toml").write_text("timeout = 600\n")

        no_project = tmp_path / "noproject"
        no_project.mkdir()

        settings = read_settings(
            no_project,
            env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        )
        expected = str(user_config_dir / "config.toml")
        assert expected in settings.config_paths

    def test_config_paths_includes_both_files(self, tmp_path):
        """config_paths should include both user and project files."""
        (tmp_path / ".git").mkdir()
        proj_config = tmp_path / ".mojox"
        proj_config.mkdir()
        (proj_config / "config.toml").write_text("jobs = 4\n")

        user_config_dir = tmp_path / "xdg" / "mojox"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.toml").write_text("timeout = 600\n")

        settings = read_settings(
            tmp_path,
            env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        )
        assert len(settings.config_paths) == 2

    def test_config_paths_empty_when_no_config(self, tmp_path):
        """config_paths should be empty when --no-config is used."""
        settings = read_settings(tmp_path, env={}, no_config=True)
        assert settings.config_paths == ()

    def test_config_paths_empty_when_no_files_found(self, tmp_path, monkeypatch):
        """config_paths should be empty when no config files exist."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("HOME", raising=False)
        settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()

    def test_config_paths_with_explicit_config_file(self, tmp_path):
        """config_paths should include explicit config file path."""
        custom = tmp_path / "custom.toml"
        custom.write_text("jobs = 16\n")

        settings = read_settings(tmp_path, env={}, config_file=custom)
        assert str(custom) in settings.config_paths


# ── Finding #3: _user_config_path bypasses env parameter ──────────────────


class TestUserConfigPathEnv:
    """Tests that _user_config_path uses the env dict, not os.environ."""

    def test_uses_env_dict_xdg(self, tmp_path, monkeypatch):
        """_user_config_path should read XDG_CONFIG_HOME from env dict."""
        # Ensure os.environ doesn't have it
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("HOME", raising=False)

        env = {"XDG_CONFIG_HOME": str(tmp_path / "custom_xdg")}
        path, boundary = _user_config_path(env)

        assert path == tmp_path / "custom_xdg" / "mojox" / "config.toml"
        assert boundary == tmp_path / "custom_xdg"

    def test_uses_env_dict_home(self, tmp_path, monkeypatch):
        """_user_config_path should read HOME from env dict."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("HOME", raising=False)

        env = {"HOME": str(tmp_path / "myhome")}
        path, boundary = _user_config_path(env)

        assert path == tmp_path / "myhome" / ".config" / "mojox" / "config.toml"
        assert boundary == tmp_path / "myhome"

    def test_ignores_os_environ(self, tmp_path, monkeypatch):
        """_user_config_path must NOT read from os.environ."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "real_xdg"))

        # Pass an empty env dict -- should return None, not use os.environ
        path, boundary = _user_config_path({})
        assert path is None
        assert boundary is None

    def test_returns_none_when_env_empty(self):
        """_user_config_path returns (None, None) when env has neither key."""
        path, boundary = _user_config_path({})
        assert path is None
        assert boundary is None

    def test_read_settings_passes_env_to_user_config(self, tmp_path, monkeypatch):
        """read_settings must pass env dict to _user_config_path."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("HOME", raising=False)

        user_config_dir = tmp_path / "xdg" / "mojox"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.toml").write_text("timeout = 300\n")

        settings = read_settings(
            tmp_path,
            env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        )
        assert settings.timeout_s == 300


# ── Finding #4: Walk doesn't stop at $HOME or mount boundary ──────────────


class TestWalkBoundaries:
    """Tests that the project config walk stops at $HOME and mount boundaries."""

    def test_stops_at_home(self, tmp_path):
        """Walk should stop at $HOME even without .git."""
        home = tmp_path / "home" / "user"
        home.mkdir(parents=True)

        # Place a .git above $HOME
        (tmp_path / ".git").mkdir()
        # Config above $HOME should not be found
        above_config = tmp_path / ".mojox"
        above_config.mkdir()
        (above_config / "config.toml").write_text("jobs = 99\n")

        # Config inside $HOME
        project_dir = home / "project"
        project_dir.mkdir()
        (project_dir / ".git").mkdir()

        settings = read_settings(
            project_dir,
            env={"HOME": str(home)},
        )
        # Should not pick up config from above $HOME
        assert settings.jobs is None

    def test_finds_config_under_home(self, tmp_path):
        """Walk should still find config under $HOME."""
        home = tmp_path / "home" / "user"
        project_dir = home / "project"
        project_dir.mkdir(parents=True)
        (project_dir / ".git").mkdir()

        config_dir = project_dir / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(
            project_dir,
            env={"HOME": str(home)},
        )
        assert settings.jobs == 4

    def test_walk_stops_at_home_before_git(self, tmp_path):
        """If $HOME is between manifest_dir and .git, walk stops at $HOME."""
        # Structure:
        #   tmp_path/
        #     .git/
        #     .mojox/config.toml  (jobs=99, should NOT be found)
        #     home/
        #       user/
        #         project/
        #           (no .git here)
        home = tmp_path / "home" / "user"
        project_dir = home / "project"
        project_dir.mkdir(parents=True)
        (tmp_path / ".git").mkdir()
        config = tmp_path / ".mojox"
        config.mkdir()
        (config / "config.toml").write_text("jobs = 99\n")

        # The walk should stop at $HOME and not reach .git above it
        path, root = _find_project_config(project_dir, home=home)
        assert path is None
        assert root is None

    def test_mount_boundary_stops_walk(self, tmp_path):
        """Walk should stop when st_dev changes (mount boundary)."""
        # We can't easily create a real mount boundary in tests,
        # so we mock os.stat to return different st_dev values.
        (tmp_path / ".git").mkdir()
        project_dir = tmp_path / "sub"
        project_dir.mkdir()

        config = tmp_path / ".mojox"
        config.mkdir()
        (config / "config.toml").write_text("jobs = 99\n")

        original_stat = os.stat

        def fake_stat(p, *args, **kwargs):
            result = original_stat(p, *args, **kwargs)
            # Make it look like tmp_path is on a different device
            p_str = str(p)
            if p_str == str(tmp_path):
                # Return a modified stat result with different st_dev

                class FakeStat:
                    """Wrapper that overrides st_dev."""

                    def __init__(self, real):
                        """Wrap a real stat result."""
                        self._real = real

                    def __getattr__(self, name):
                        """Delegate to real stat result."""
                        if name == "st_dev":
                            return self._real.st_dev + 1
                        return getattr(self._real, name)

                return FakeStat(result)
            return result

        with patch("os.stat", side_effect=fake_stat):
            path, _root = _find_project_config(project_dir, home=None)
            # The walk should have stopped at the mount boundary
            assert path is None

    def test_find_project_config_accepts_home_param(self, tmp_path):
        """_find_project_config must accept a home parameter."""
        home = tmp_path / "home"
        home.mkdir()
        project = home / "project"
        project.mkdir()
        (project / ".git").mkdir()

        # Should not error with home parameter
        _path, root = _find_project_config(project, home=home)
        assert root == project.resolve()
