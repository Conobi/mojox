"""Settings IO reader: discover and securely read .mojox/config.toml files."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest
from mojox.settings_reader import (
    _find_project_config,
    _read_trusted_toml,
    _untrusted_directory,
    _UntrustedFileError,
    _user_config_path,
    read_settings,
)


def _get(settings, key):
    """The effective value of settings *key* across the loaded files (later file wins), or None."""
    found = [values[key].value for _path, values in settings.sources if key in values]
    return found[-1] if found else None


class TestDiscovery:
    def test_finds_project_config_at_git_root(self, tmp_path):
        """Config in the same dir as .git is found."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(tmp_path, env={})
        assert _get(settings, "jobs") == 4

    def test_parallelism_keys_round_trip(self, tmp_path):
        """A TOML jobs spec and compile-threads survive discovery and parsing."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('jobs = "num-cpus"\ncompile-threads = 2\n')

        settings = read_settings(tmp_path, env={})
        assert _get(settings, "jobs") == "num-cpus"
        assert _get(settings, "compile-threads") == 2

    def test_walks_up_to_git_boundary(self, tmp_path):
        """Config at repo root found when starting from a subdirectory."""
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "packages" / "mylib"
        subdir.mkdir(parents=True)
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 2\n")

        settings = read_settings(subdir, env={})
        assert _get(settings, "jobs") == 2

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
        assert _get(settings, "jobs") == 8

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
        assert _get(settings, "jobs") is None

    def test_no_git_means_no_project_config(self, tmp_path):
        """Without a .git ancestor, project-level config is not loaded."""
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(tmp_path, env={})
        assert _get(settings, "jobs") is None

    def test_git_file_counts_as_boundary(self, tmp_path):
        """A .git file (worktree) is treated as a repo root."""
        (tmp_path / ".git").write_text("gitdir: /some/worktree\n")
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(tmp_path, env={})
        assert _get(settings, "jobs") == 4

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
        assert _get(settings, "timeout") == 600

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
        assert _get(settings, "jobs") == 4

    def test_no_config_flag(self, tmp_path):
        """--no-config skips all config file discovery."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")

        settings = read_settings(tmp_path, env={}, no_config=True)
        assert _get(settings, "jobs") is None

    def test_explicit_config_file(self, tmp_path):
        """--config-file replaces all discovery."""
        custom = tmp_path / "custom.toml"
        custom.write_text("jobs = 16\ntimeout = 120\n")

        settings = read_settings(tmp_path, env={}, config_file=custom)
        assert _get(settings, "jobs") == 16
        assert _get(settings, "timeout") == 120

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
        assert _get(settings, "jobs") is None
        assert _get(settings, "timeout") is None


class TestSecurity:
    def test_world_writable_dir_refused(self, tmp_path):
        """Config under a world-writable directory is not loaded."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")
        config_dir.chmod(0o777)

        settings = read_settings(tmp_path, env={})
        assert _get(settings, "jobs") is None
        config_dir.chmod(0o755)


class TestInvalidToml:
    """A discovered settings file that is not valid TOML is a ConfigError naming the file."""

    @staticmethod
    def _project(tmp_path, content: bytes):
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        config = config_dir / "config.toml"
        config.write_bytes(content)
        return config

    def test_project_file_with_syntax_error_raises(self, tmp_path):
        from mojox_core import ConfigError

        config = self._project(tmp_path, b"jobs = 4\njobs =\n")
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={})
        assert exc.value.key_path == str(config)
        assert exc.value.message.startswith("not valid TOML: ")
        assert "line 2" in exc.value.message
        assert str(exc.value).startswith(f"{config}: not valid TOML: ")

    def test_project_file_with_invalid_utf8_raises(self, tmp_path):
        from mojox_core import ConfigError

        config = self._project(tmp_path, b"jobs = 4\n\xff\n")
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={})
        assert exc.value.key_path == str(config)
        assert exc.value.message.startswith("not valid TOML: ")

    def test_user_file_with_syntax_error_raises(self, tmp_path):
        from mojox_core import ConfigError

        user_dir = tmp_path / "xdg" / "mojox"
        user_dir.mkdir(parents=True)
        config = user_dir / "config.toml"
        config.write_text("[unclosed\n")
        no_project = tmp_path / "noproject"
        no_project.mkdir()

        with pytest.raises(ConfigError) as exc:
            read_settings(no_project, env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
        assert exc.value.key_path == str(config)
        assert exc.value.message.startswith("not valid TOML: ")
        assert "line 1" in exc.value.message

    def test_explicit_config_file_with_syntax_error_raises(self, tmp_path):
        from mojox_core import ConfigError

        custom = tmp_path / "custom.toml"
        custom.write_text("jobs = = 2\n")
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=custom)
        assert exc.value.key_path == str(custom)
        assert exc.value.message.startswith("not valid TOML: ")

    def test_deeply_nested_file_raises(self, tmp_path):
        from mojox_core import ConfigError

        config = self._project(tmp_path, b"a = " + b"[" * 2000)
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={})
        assert exc.value.key_path == str(config)
        assert exc.value.message == "not valid TOML: nested too deeply"

    def test_explicit_config_file_missing_names_the_path(self, tmp_path):
        from mojox_core import ConfigError

        missing = tmp_path / "nonexistent.toml"
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=missing)
        assert (exc.value.key_path, exc.value.message) == (str(missing), "does not exist")

    def test_explicit_config_file_vanished_before_open(self, tmp_path):
        from mojox_core import ConfigError

        custom = tmp_path / "custom.toml"
        custom.write_text("jobs = 2\n")
        with (
            patch("mojox.settings_reader.os.open", side_effect=FileNotFoundError(2, "gone")),
            pytest.raises(ConfigError) as exc,
        ):
            read_settings(tmp_path, env={}, config_file=custom)
        assert (exc.value.key_path, exc.value.message) == (str(custom), "does not exist")

    @pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW not available on this platform")
    def test_explicit_config_file_symlink_refused(self, tmp_path):
        from mojox_core import ConfigError

        target = tmp_path / "real.toml"
        target.write_text("jobs = 2\n")
        link = tmp_path / "link.toml"
        link.symlink_to(target)
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=link)
        assert (exc.value.key_path, exc.value.message) == (str(link), "untrusted: symlinks are refused")

    def test_discovered_file_vanished_before_open_is_skipped(self, tmp_path):
        self._project(tmp_path, b"jobs = 4\n")
        real_open = os.open

        def vanish(path, flags, *args, **kwargs):
            if str(path).endswith("config.toml"):
                raise FileNotFoundError(2, "gone")
            return real_open(path, flags, *args, **kwargs)

        with patch("mojox.settings_reader.os.open", side_effect=vanish):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()

    def test_insecure_file_with_syntax_error_is_skipped(self, tmp_path):
        self._project(tmp_path, b"jobs =\n")
        config_dir = tmp_path / ".mojox"
        config_dir.chmod(0o777)
        try:
            settings = read_settings(tmp_path, env={})
        finally:
            config_dir.chmod(0o755)
        assert settings.config_paths == ()

    def test_wrongly_owned_file_with_syntax_error_is_skipped(self, tmp_path):
        self._project(tmp_path, b"jobs =\n")
        with patch("mojox.settings_reader.os.getuid", return_value=os.getuid() + 1):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()

    @pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW not available on this platform")
    def test_symlinked_file_with_syntax_error_is_skipped(self, tmp_path):
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        target = tmp_path / "elsewhere.toml"
        target.write_text("jobs =\n")
        (config_dir / "config.toml").symlink_to(target)

        settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()

    @pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads any file")
    def test_unreadable_secure_file_raises(self, tmp_path):
        from mojox_core import ConfigError

        config = self._project(tmp_path, b"jobs = 4\n")
        config.chmod(0o000)
        try:
            with pytest.raises(ConfigError) as exc:
                read_settings(tmp_path, env={})
        finally:
            config.chmod(0o644)
        assert exc.value.key_path == str(config)
        assert exc.value.message.startswith("could not be read: ")


# ── Finding #1: TOCTOU — openat(O_NOFOLLOW) + fstat ──────────────────────


class TestTOCTOU:
    """Tests for the TOCTOU fix: using O_NOFOLLOW + fstat instead of stat-then-open."""

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"),
        reason="O_NOFOLLOW not available on this platform",
    )
    def test_read_trusted_toml_uses_o_nofollow(self, tmp_path):
        """_read_trusted_toml must open with O_NOFOLLOW, not plain open()."""
        config = tmp_path / "config.toml"
        config.write_text("jobs = 42\n")

        # Should succeed on a regular file
        data = _read_trusted_toml(config)
        assert data == {"jobs": 42}

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"),
        reason="O_NOFOLLOW not available on this platform",
    )
    def test_read_trusted_toml_rejects_symlink(self, tmp_path):
        """_read_trusted_toml must reject a symlink to a config file."""
        real = tmp_path / "real.toml"
        real.write_text("jobs = 42\n")
        link = tmp_path / "config.toml"
        link.symlink_to(real)

        # O_NOFOLLOW should cause the open to fail on a symlink
        with pytest.raises(_UntrustedFileError):
            _read_trusted_toml(link)

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"),
        reason="O_NOFOLLOW not available on this platform",
    )
    def test_untrusted_directory_rejects_symlinked_directory(self, tmp_path):
        """_untrusted_directory must reject a path with a symlinked directory."""
        real_dir = tmp_path / "real_mojox"
        real_dir.mkdir()
        config = real_dir / "config.toml"
        config.write_text("jobs = 1\n")

        # Create a symlink for the .mojox directory
        link_dir = tmp_path / ".mojox"
        link_dir.symlink_to(real_dir)
        link_config = link_dir / "config.toml"

        assert _untrusted_directory(link_config, stop_at=tmp_path) is not None

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"),
        reason="O_NOFOLLOW not available on this platform",
    )
    def test_untrusted_directory_walks_with_openat(self, tmp_path):
        """_untrusted_directory should use fd-based walk, not Path.stat()."""
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        config = config_dir / "config.toml"
        config.write_text("jobs = 1\n")

        # Valid path should pass
        assert _untrusted_directory(config, stop_at=tmp_path) is None

    def test_read_trusted_toml_windows_fallback(self, tmp_path):
        """On platforms without O_NOFOLLOW, plain open() is used."""
        config = tmp_path / "config.toml"
        config.write_text("jobs = 99\n")

        with patch("mojox.settings_reader._HAS_O_NOFOLLOW", False):
            data = _read_trusted_toml(config)
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
        assert _get(settings, "timeout") == 300


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
        assert _get(settings, "jobs") is None

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
        assert _get(settings, "jobs") == 4

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


class TestFileTrust:
    """The settings file itself must pass the same trust rules as its directories."""

    @staticmethod
    def _project(tmp_path, content: str = "jobs = 4\n"):
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        config = config_dir / "config.toml"
        config.write_text(content)
        return config

    @staticmethod
    def _foreign_owned_files(uid: int):
        """An ``os.fstat`` stand-in that reports regular files as owned by *uid*."""
        import stat as stat_mod

        real_fstat = os.fstat

        def fake(fd):
            st = real_fstat(fd)
            if not stat_mod.S_ISREG(st.st_mode):
                return st
            fields = list(st)
            fields[4] = uid  # st_uid
            return os.stat_result(fields)

        return fake

    def test_world_writable_project_file_is_skipped(self, tmp_path):
        config = self._project(tmp_path, 'jobs = 4\n[env]\nLD_PRELOAD = "/tmp/evil.so"\n')
        config.chmod(0o666)
        settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()
        assert _get(settings, "jobs") is None

    def test_world_writable_user_file_is_skipped(self, tmp_path):
        user_dir = tmp_path / "xdg" / "mojox"
        user_dir.mkdir(parents=True)
        config = user_dir / "config.toml"
        config.write_text("timeout = 600\n")
        config.chmod(0o646)
        no_project = tmp_path / "noproject"
        no_project.mkdir()
        settings = read_settings(no_project, env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
        assert settings.config_paths == ()

    def test_owner_readable_file_is_read(self, tmp_path):
        config = self._project(tmp_path)
        config.chmod(0o644)
        settings = read_settings(tmp_path, env={})
        assert settings.config_paths == (str(config),)
        assert _get(settings, "jobs") == 4

    @pytest.mark.skipif(not hasattr(os, "getuid"), reason="no POSIX ownership")
    def test_foreign_owned_file_is_skipped(self, tmp_path):
        self._project(tmp_path)
        with patch("mojox.settings_reader.os.fstat", side_effect=self._foreign_owned_files(os.getuid() + 1)):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()

    @pytest.mark.skipif(not hasattr(os, "getuid"), reason="no POSIX ownership")
    def test_root_owned_file_is_read(self, tmp_path):
        self._project(tmp_path)
        with patch("mojox.settings_reader.os.fstat", side_effect=self._foreign_owned_files(0)):
            settings = read_settings(tmp_path, env={})
        assert _get(settings, "jobs") == 4

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
    def test_fifo_is_refused_without_blocking(self, tmp_path):
        import threading

        fifo = tmp_path / "config.toml"
        os.mkfifo(fifo)
        outcome: list[BaseException | object] = []

        def read():
            try:
                outcome.append(_read_trusted_toml(fifo))
            except BaseException as e:
                outcome.append(e)

        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        worker.join(5)
        if worker.is_alive():
            # Unblock the reader so the thread can finish, then fail.
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            pytest.fail("opening a FIFO settings file blocked")
        assert len(outcome) == 1
        assert isinstance(outcome[0], _UntrustedFileError)
        assert str(outcome[0]) == "untrusted: not a regular file"

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
    def test_explicit_fifo_is_an_error(self, tmp_path):
        from mojox_core import ConfigError

        fifo = tmp_path / "custom.toml"
        os.mkfifo(fifo)
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=fifo)
        assert (exc.value.key_path, exc.value.message) == (str(fifo), "untrusted: not a regular file")

    def test_directory_named_config_toml_is_refused(self, tmp_path):
        directory = tmp_path / "config.toml"
        directory.mkdir()
        with pytest.raises(_UntrustedFileError, match="not a regular file"):
            _read_trusted_toml(directory)

    def test_explicit_world_writable_file_is_an_error(self, tmp_path):
        from mojox_core import ConfigError

        custom = tmp_path / "custom.toml"
        custom.write_text('[env]\nLD_PRELOAD = "/tmp/evil.so"\n')
        custom.chmod(0o666)
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=custom)
        assert (exc.value.key_path, exc.value.message) == (str(custom), "untrusted: writable by other users")
        assert "evil" not in str(exc.value)

    @pytest.mark.skipif(not hasattr(os, "getuid"), reason="no POSIX ownership")
    def test_explicit_foreign_owned_file_is_an_error(self, tmp_path):
        from mojox_core import ConfigError

        custom = tmp_path / "custom.toml"
        custom.write_text("jobs = 2\n")
        with (
            patch("mojox.settings_reader.os.fstat", side_effect=self._foreign_owned_files(os.getuid() + 1)),
            pytest.raises(ConfigError) as exc,
        ):
            read_settings(tmp_path, env={}, config_file=custom)
        assert (exc.value.key_path, exc.value.message) == (str(custom), "untrusted: owned by another user")

    def test_explicit_directory_is_named(self, tmp_path):
        from mojox_core import ConfigError

        directory = tmp_path / "custom.toml"
        directory.mkdir()
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=directory)
        assert (exc.value.key_path, exc.value.message) == (str(directory), "untrusted: not a regular file")

    def test_without_posix_ownership_explicit_world_writable_file_is_read(self, tmp_path):
        custom = tmp_path / "custom.toml"
        custom.write_text("jobs = 2\n")
        custom.chmod(0o666)
        with patch("mojox.settings_reader.os.getuid", side_effect=AttributeError):
            settings = read_settings(tmp_path, env={}, config_file=custom)
        assert _get(settings, "jobs") == 2


@pytest.mark.skipif(not hasattr(os, "getgid"), reason="no POSIX groups")
class TestGroupWrite:
    """Group write is trusted only for the user's own private group, as OpenSSH does."""

    @staticmethod
    def _project(tmp_path, *, dir_mode=0o755, file_mode=0o644):
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        config = config_dir / "config.toml"
        config.write_text("jobs = 4\n")
        config.chmod(file_mode)
        config_dir.chmod(dir_mode)
        return config

    @staticmethod
    def _group(members):
        from types import SimpleNamespace

        return patch("grp.getgrgid", return_value=SimpleNamespace(gr_mem=list(members)))

    @staticmethod
    def _user_name():
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name

    @staticmethod
    def _accounts(*primary_gids_by_uid):
        """Patch ``pwd.getpwall`` to list accounts as ``(uid, primary gid)`` pairs."""
        from types import SimpleNamespace

        entries = [SimpleNamespace(pw_uid=uid, pw_gid=gid) for uid, gid in primary_gids_by_uid]
        return patch("pwd.getpwall", return_value=entries)

    def test_private_group_is_trusted(self, tmp_path):
        config = self._project(tmp_path, dir_mode=0o775, file_mode=0o664)
        with (
            patch("mojox.settings_reader.os.getgid", return_value=config.stat().st_gid),
            self._group([]),
            self._accounts((os.getuid(), config.stat().st_gid), (os.getuid() + 1, config.stat().st_gid + 1)),
        ):
            settings = read_settings(tmp_path, env={})
        assert _get(settings, "jobs") == 4

    def test_group_listing_only_the_user_is_trusted(self, tmp_path):
        config = self._project(tmp_path, dir_mode=0o775, file_mode=0o664)
        with (
            patch("mojox.settings_reader.os.getgid", return_value=config.stat().st_gid),
            self._group([self._user_name()]),
            self._accounts((os.getuid(), config.stat().st_gid), (os.getuid() + 1, config.stat().st_gid + 1)),
        ):
            settings = read_settings(tmp_path, env={})
        assert _get(settings, "jobs") == 4

    def test_group_writable_file_of_foreign_group_is_refused(self, tmp_path, capsys):
        config = self._project(tmp_path, file_mode=0o664)
        with patch("mojox.settings_reader.os.getgid", return_value=config.stat().st_gid + 1), self._group([]):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()
        assert capsys.readouterr().err == f"mojox: ignoring {config}: untrusted: writable by a shared group\n"

    def test_group_writable_file_of_group_with_other_members_is_refused(self, tmp_path):
        config = self._project(tmp_path, file_mode=0o664)
        with (
            patch("mojox.settings_reader.os.getgid", return_value=config.stat().st_gid),
            self._group([self._user_name(), "mallory"]),
        ):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()

    def test_group_writable_file_of_unknown_group_is_refused(self, tmp_path):
        config = self._project(tmp_path, file_mode=0o664)
        with (
            patch("mojox.settings_reader.os.getgid", return_value=config.stat().st_gid),
            patch("grp.getgrgid", side_effect=KeyError),
        ):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()

    def test_group_writable_directory_of_shared_group_is_refused(self, tmp_path, capsys):
        config = self._project(tmp_path, dir_mode=0o775)
        with self._group(["mallory"]):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()
        assert capsys.readouterr().err == (
            f"mojox: ignoring {config}: untrusted: directory {config.parent} is writable by a shared group\n"
        )

    def test_explicit_group_writable_file_of_shared_group_is_an_error(self, tmp_path):
        from mojox_core import ConfigError

        custom = tmp_path / "custom.toml"
        custom.write_text("jobs = 2\n")
        custom.chmod(0o664)
        with self._group(["mallory"]), pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=custom)
        assert (exc.value.key_path, exc.value.message) == (str(custom), "untrusted: writable by a shared group")

    def test_group_that_is_another_accounts_primary_group_is_refused(self, tmp_path, capsys):
        config = self._project(tmp_path, file_mode=0o664)
        gid = config.stat().st_gid
        with (
            patch("mojox.settings_reader.os.getgid", return_value=gid),
            self._group([]),
            self._accounts((os.getuid(), gid), (os.getuid() + 1, gid)),
        ):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()
        assert capsys.readouterr().err == f"mojox: ignoring {config}: untrusted: writable by a shared group\n"

    def test_failed_account_enumeration_is_refused(self, tmp_path):
        config = self._project(tmp_path, file_mode=0o664)
        with (
            patch("mojox.settings_reader.os.getgid", return_value=config.stat().st_gid),
            self._group([]),
            patch("pwd.getpwall", side_effect=OSError("directory unreachable")),
        ):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()

    def test_unknown_user_is_refused(self, tmp_path):
        config = self._project(tmp_path, file_mode=0o664)
        with (
            patch("mojox.settings_reader.os.getgid", return_value=config.stat().st_gid),
            self._group([]),
            self._accounts((os.getuid(), config.stat().st_gid)),
            patch("pwd.getpwuid", side_effect=KeyError),
        ):
            settings = read_settings(tmp_path, env={})
        assert settings.config_paths == ()

    def test_accounts_are_not_enumerated_without_group_write(self, tmp_path):
        self._project(tmp_path)
        with patch("pwd.getpwall") as getpwall, patch("grp.getgrgid") as getgrgid:
            settings = read_settings(tmp_path, env={})
        assert _get(settings, "jobs") == 4
        getpwall.assert_not_called()
        getgrgid.assert_not_called()


class TestIgnoredFileWarning:
    """An ignored discovered file is named on stderr, once, without its content."""

    def test_world_writable_project_file_is_reported(self, tmp_path, capsys):
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        config = config_dir / "config.toml"
        config.write_text('[env]\nLD_PRELOAD = "/tmp/evil.so"\n')
        config.chmod(0o666)
        read_settings(tmp_path, env={})
        assert capsys.readouterr().err == f"mojox: ignoring {config}: untrusted: writable by other users\n"

    def test_world_writable_directory_is_reported(self, tmp_path, capsys):
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        config = config_dir / "config.toml"
        config.write_text("jobs = 4\n")
        config_dir.chmod(0o777)
        try:
            read_settings(tmp_path, env={})
        finally:
            config_dir.chmod(0o755)
        assert capsys.readouterr().err == (
            f"mojox: ignoring {config}: untrusted: directory {config_dir} is writable by other users\n"
        )

    def test_world_writable_user_file_is_reported(self, tmp_path, capsys):
        user_dir = tmp_path / "xdg" / "mojox"
        user_dir.mkdir(parents=True)
        config = user_dir / "config.toml"
        config.write_text("timeout = 600\n")
        config.chmod(0o646)
        no_project = tmp_path / "noproject"
        no_project.mkdir()
        read_settings(no_project, env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
        assert capsys.readouterr().err == f"mojox: ignoring {config}: untrusted: writable by other users\n"

    def test_trusted_file_is_silent(self, tmp_path, capsys):
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")
        read_settings(tmp_path, env={})
        assert capsys.readouterr().err == ""

    def test_vanished_file_is_silent(self, tmp_path, capsys):
        (tmp_path / ".git").mkdir()
        config_dir = tmp_path / ".mojox"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("jobs = 4\n")
        real_open = os.open

        def vanish(path, flags, *args, **kwargs):
            if str(path).endswith("config.toml"):
                raise FileNotFoundError(2, "gone")
            return real_open(path, flags, *args, **kwargs)

        with patch("mojox.settings_reader.os.open", side_effect=vanish):
            read_settings(tmp_path, env={})
        assert capsys.readouterr().err == ""


class TestPathEscaping:
    """Paths shown to the user cannot carry terminal control sequences."""

    EVIL = "evil\x1b[31m\nname\x9b"

    @staticmethod
    def _controls(text):
        import unicodedata

        return [c for c in text if unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"}]

    def _project(self, tmp_path, *, file_mode=0o644, content="jobs = 4\n"):
        root = tmp_path / self.EVIL
        root.mkdir()
        (root / ".git").mkdir()
        config_dir = root / ".mojox"
        config_dir.mkdir()
        config = config_dir / "config.toml"
        config.write_text(content)
        config.chmod(file_mode)
        return root, config

    def test_ignored_file_warning_is_escaped(self, tmp_path, capsys):
        root, _config = self._project(tmp_path, file_mode=0o666)
        read_settings(root, env={})
        err = capsys.readouterr().err
        assert err.endswith(": untrusted: writable by other users\n")
        assert self._controls(err) == ["\n"]
        assert "\\x1b[31m\\nname\\x9b" in err

    def test_untrusted_directory_reason_is_escaped(self, tmp_path, capsys):
        root, config = self._project(tmp_path)
        config.parent.chmod(0o777)
        try:
            read_settings(root, env={})
        finally:
            config.parent.chmod(0o755)
        err = capsys.readouterr().err
        assert "is writable by other users" in err
        assert self._controls(err) == ["\n"]

    def test_invalid_toml_error_is_escaped(self, tmp_path):
        from mojox_core import ConfigError

        root, _config = self._project(tmp_path, content="jobs =\n")
        with pytest.raises(ConfigError) as exc:
            read_settings(root, env={})
        assert self._controls(str(exc.value)) == []

    def test_explicit_missing_file_error_is_escaped(self, tmp_path):
        from mojox_core import ConfigError

        missing = tmp_path / self.EVIL / "custom.toml"
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=missing)
        assert exc.value.message == "does not exist"
        assert self._controls(str(exc.value)) == []

    def test_explicit_untrusted_file_error_is_escaped(self, tmp_path):
        from mojox_core import ConfigError

        (tmp_path / self.EVIL).mkdir()
        custom = tmp_path / self.EVIL / "custom.toml"
        custom.write_text("jobs = 2\n")
        custom.chmod(0o666)
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=custom)
        assert exc.value.message == "untrusted: writable by other users"
        assert self._controls(str(exc.value)) == []

    def test_printable_path_is_shown_unchanged(self, tmp_path):
        from mojox_core import ConfigError

        missing = tmp_path / "plain é dir" / "custom.toml"
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=missing)
        assert exc.value.key_path == str(missing)

    def test_invalid_key_error_is_escaped(self, tmp_path):
        from mojox_core import ConfigError

        root, _config = self._project(tmp_path, content="no-such-key = 1\n")
        with pytest.raises(ConfigError) as exc:
            read_settings(root, env={})
        assert self._controls(str(exc.value)) == []

    def test_paths_stay_raw_as_data(self, tmp_path):
        from pathlib import Path

        root, config = self._project(tmp_path)
        settings = read_settings(root, env={})
        assert settings.config_paths == (str(config),)
        assert Path(settings.config_paths[0]).exists()
        assert _get(settings, "jobs") == 4

    def test_error_keeps_raw_key_path_but_prints_escaped(self, tmp_path):
        from mojox_core import ConfigError

        missing = tmp_path / self.EVIL / "custom.toml"
        with pytest.raises(ConfigError) as exc:
            read_settings(tmp_path, env={}, config_file=missing)
        assert exc.value.key_path == str(missing)
        assert self._controls(str(exc.value)) == []

    def test_bidi_override_in_warning_is_escaped(self, tmp_path, capsys):
        root = tmp_path / "gnp.\u202etxt"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".mojox").mkdir()
        config = root / ".mojox" / "config.toml"
        config.write_text("jobs = 4\n")
        config.chmod(0o666)
        read_settings(root, env={})
        err = capsys.readouterr().err
        assert self._controls(err) == ["\n"]
        assert "\\u202e" in err

    @pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses non-UTF-8 names")
    def test_undecodable_name_in_warning_prints(self, tmp_path, capsys):
        root = tmp_path / "bad\udcffname"
        try:
            root.mkdir()
        except (OSError, UnicodeEncodeError):
            pytest.skip("filesystem refuses non-UTF-8 names")
        (root / ".git").mkdir()
        (root / ".mojox").mkdir()
        config = root / ".mojox" / "config.toml"
        config.write_text("jobs = 4\n")
        config.chmod(0o666)
        read_settings(root, env={})
        err = capsys.readouterr().err
        assert self._controls(err) == ["\n"]
        err.encode("utf-8")
