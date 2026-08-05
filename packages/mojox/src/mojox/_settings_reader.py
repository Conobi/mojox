"""Discover and securely read .mojox/config.toml files.

The reader walks from the manifest directory up to the .git boundary to
find project-level config, and checks $XDG_CONFIG_HOME/mojox/config.toml
for user-level config.  Security: directories between the config file and
a trusted boundary are checked for ownership and permissions.

This module lives in mojox (not mojox-core) so that mojox-build cannot
load local config -- making "local settings never influence mojox build
output" a structural guarantee.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from mojox_core import LocalSettings, parse_settings


def read_settings(
    manifest_dir: Path,
    env: dict[str, str],
    *,
    no_config: bool = False,
    config_file: Path | None = None,
) -> LocalSettings:
    """Read settings from config files and environment variables.

    Discovers config files by walking from *manifest_dir* up to the
    nearest ``.git`` boundary, and by checking the XDG user config
    directory.  The ``--no-config`` flag disables all discovery;
    ``--config-file`` replaces discovery with an explicit path.

    Args:
        manifest_dir: The directory containing pyproject.toml.
        env: Environment variables (typically ``os.environ``).
        no_config: If True, skip all config file discovery.
        config_file: Explicit config file path (replaces discovery).

    Returns:
        A parsed ``LocalSettings`` instance.
    """
    if no_config:
        return parse_settings(None, None, env)

    if config_file is not None:
        data = _safe_read_toml(config_file)
        return parse_settings(None, data, env)

    user_data: dict | None = None
    project_data: dict | None = None

    user_path, user_boundary = _user_config_path()
    if user_path is not None and user_path.is_file():
        if _is_path_secure(user_path, stop_at=user_boundary):
            user_data = _safe_read_toml(user_path)

    project_path, repo_root = _find_project_config(manifest_dir)
    if project_path is not None:
        if _is_path_secure(project_path, stop_at=repo_root):
            project_data = _safe_read_toml(project_path)

    return parse_settings(user_data, project_data, env)


def _user_config_path() -> tuple[Path | None, Path | None]:
    """Return the user-level config file path and its trust boundary.

    Uses ``$XDG_CONFIG_HOME/mojox/config.toml`` if set, otherwise
    falls back to ``~/.config/mojox/config.toml``.

    Returns:
        A tuple of (config_path, boundary_dir).  The boundary is the
        XDG_CONFIG_HOME or HOME directory -- security checks stop there.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        boundary = Path(xdg)
        return boundary / "mojox" / "config.toml", boundary
    home = os.environ.get("HOME")
    if home:
        boundary = Path(home)
        return boundary / ".config" / "mojox" / "config.toml", boundary
    return None, None


def _find_project_config(
    manifest_dir: Path,
) -> tuple[Path | None, Path | None]:
    """Walk up from *manifest_dir* to find the nearest ``.mojox/config.toml``.

    The walk stops at the first ``.git`` boundary (directory **or** file,
    since worktrees use a ``.git`` file).  If no ``.git`` ancestor is
    found, returns ``(None, None)`` -- no project config without a repo root.

    When multiple config files exist along the path, the one nearest to
    *manifest_dir* wins (first found during the upward walk).

    Returns:
        A tuple of (config_path, repo_root).  *repo_root* is the directory
        containing ``.git`` -- security checks stop there.
    """
    current = manifest_dir.resolve()
    found_config: Path | None = None

    while True:
        git_marker = current / ".git"
        has_git = git_marker.exists()

        config = current / ".mojox" / "config.toml"
        if config.is_file() and found_config is None:
            found_config = config

        if has_git:
            return found_config, current

        parent = current.parent
        if parent == current:
            # Reached the filesystem root without finding .git
            return None, None

        current = parent


def _is_path_secure(path: Path, *, stop_at: Path | None = None) -> bool:
    """Check that directories between *path* and *stop_at* are secure.

    Walks from the config file's parent directory up to *stop_at*
    (inclusive).  Refuses:

    * World-writable directories -- an attacker could replace the config
      file or its parent ``.mojox/`` directory.
    * Directories owned by a user other than the current user or root.

    Only directories within the project/user boundary are checked;
    system directories above *stop_at* are not inspected.

    Returns ``True`` on platforms without ``os.getuid`` (Windows).
    """
    try:
        uid = os.getuid()
    except AttributeError:
        # Windows -- skip ownership checks
        return True

    stop_resolved = stop_at.resolve() if stop_at is not None else None
    p = path.resolve().parent

    while True:
        try:
            st = p.stat()
        except OSError:
            return False

        if stat.S_ISDIR(st.st_mode) and (st.st_mode & stat.S_IWOTH):
            return False

        if st.st_uid != uid and st.st_uid != 0:
            return False

        if stop_resolved is not None and p == stop_resolved:
            break

        parent = p.parent
        if parent == p:
            break
        p = parent

    return True


def _safe_read_toml(path: Path) -> dict | None:
    """Read a TOML file, returning ``None`` on any error.

    Uses ``tomllib`` on Python 3.11+ and ``tomli`` on older versions.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]

    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return None
