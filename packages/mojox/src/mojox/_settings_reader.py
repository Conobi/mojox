"""Discover and securely read .mojox/config.toml files.

The reader walks from the manifest directory up to the .git boundary to
find project-level config, and checks $XDG_CONFIG_HOME/mojox/config.toml
for user-level config.  Security: directories between the config file and
a trusted boundary are checked for ownership and permissions using
``openat(O_NOFOLLOW)`` and ``fstat`` to prevent TOCTOU races.

The walk stops early at ``$HOME`` or at a filesystem mount boundary
(``st_dev`` change) to avoid escaping the user's project area.

This module lives in mojox (not mojox-core) so that mojox-build cannot
load local config -- making "local settings never influence mojox build
output" a structural guarantee.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import replace
from pathlib import Path

from mojox_core import LocalSettings, parse_settings

# Platform gate: O_NOFOLLOW prevents following symlinks on open.
# Not available on Windows; fall back to plain open() there.
_HAS_O_NOFOLLOW: bool = hasattr(os, "O_NOFOLLOW")


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

    The ``env`` dict is threaded through to ``_user_config_path`` and
    ``_find_project_config`` so that ``$XDG_CONFIG_HOME``, ``$HOME``,
    and other environment variables are never read from ``os.environ``
    directly --- keeping the reader testable and pure.

    After parsing, ``config_paths`` on the returned ``LocalSettings``
    is populated with the resolved paths of every config file that was
    actually loaded, so ``mojox check`` and ``--dry-run`` can print them.

    Args:
        manifest_dir: The directory containing pyproject.toml.
        env: Environment variables (typically ``os.environ``).
        no_config: If True, skip all config file discovery.
        config_file: Explicit config file path (replaces discovery).

    Returns:
        A parsed ``LocalSettings`` instance with ``config_paths`` set.
    """
    if no_config:
        return parse_settings(None, None, env)

    if config_file is not None:
        data = _safe_read_toml(config_file)
        result = parse_settings(None, data, env)
        loaded: list[str] = []
        if data is not None:
            loaded.append(str(config_file))
        return replace(result, config_paths=tuple(loaded))

    user_data: dict | None = None
    project_data: dict | None = None
    loaded_paths: list[str] = []

    user_path, user_boundary = _user_config_path(env)
    if user_path is not None and user_path.is_file():
        if _is_path_secure(user_path, stop_at=user_boundary):
            user_data = _safe_read_toml(user_path)
            if user_data is not None:
                loaded_paths.append(str(user_path))

    # Resolve $HOME for the walk boundary
    home_str = env.get("HOME")
    home = Path(home_str).resolve() if home_str else None

    project_path, repo_root = _find_project_config(manifest_dir, home=home)
    if project_path is not None:
        if _is_path_secure(project_path, stop_at=repo_root):
            project_data = _safe_read_toml(project_path)
            if project_data is not None:
                loaded_paths.append(str(project_path))

    result = parse_settings(user_data, project_data, env)
    return replace(result, config_paths=tuple(loaded_paths))


def _user_config_path(
    env: dict[str, str],
) -> tuple[Path | None, Path | None]:
    """Return the user-level config file path and its trust boundary.

    Uses ``$XDG_CONFIG_HOME/mojox/config.toml`` from *env* if set,
    otherwise falls back to ``$HOME/.config/mojox/config.toml`` from
    *env*.  Never reads ``os.environ`` directly.

    Args:
        env: Environment variables dict to read XDG_CONFIG_HOME and
            HOME from.

    Returns:
        A tuple of (config_path, boundary_dir).  The boundary is the
        XDG_CONFIG_HOME or HOME directory -- security checks stop there.
        Returns ``(None, None)`` when neither variable is present.
    """
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        boundary = Path(xdg)
        return boundary / "mojox" / "config.toml", boundary
    home = env.get("HOME")
    if home:
        boundary = Path(home)
        return boundary / ".config" / "mojox" / "config.toml", boundary
    return None, None


def _find_project_config(
    manifest_dir: Path,
    *,
    home: Path | None = None,
) -> tuple[Path | None, Path | None]:
    """Walk up from *manifest_dir* to find the nearest ``.mojox/config.toml``.

    The walk stops at the first ``.git`` boundary (directory **or** file,
    since worktrees use a ``.git`` file).  If no ``.git`` ancestor is
    found, returns ``(None, None)`` -- no project config without a repo root.

    The walk also stops early at:
    - ``$HOME`` (resolved *home* parameter) -- config above the user's
      home directory is outside the project area.
    - A filesystem mount boundary -- if ``os.stat(current).st_dev``
      differs from the previous directory's device, the walk halts.

    When multiple config files exist along the path, the one nearest to
    *manifest_dir* wins (first found during the upward walk).

    Args:
        manifest_dir: Starting directory for the upward walk.
        home: Resolved ``$HOME`` path.  The walk stops if it reaches
            this directory (before checking for ``.git``).

    Returns:
        A tuple of (config_path, repo_root).  *repo_root* is the directory
        containing ``.git`` -- security checks stop there.
    """
    current = manifest_dir.resolve()
    found_config: Path | None = None

    # Track st_dev to detect mount boundaries.
    try:
        prev_dev = os.stat(current).st_dev
    except OSError:
        return None, None

    while True:
        # Stop early at $HOME boundary (before checking .git).
        if home is not None and current == home:
            # We've reached $HOME without finding .git inside it.
            # Return whatever config we found so far only if there's
            # also a .git here.
            git_marker = current / ".git"
            if git_marker.exists():
                config = current / ".mojox" / "config.toml"
                if config.is_file() and found_config is None:
                    found_config = config
                return found_config, current
            return None, None

        # Check for mount boundary (st_dev change).
        try:
            current_dev = os.stat(current).st_dev
        except OSError:
            return None, None
        if current_dev != prev_dev:
            # Crossed a mount boundary -- stop the walk.
            return None, None
        prev_dev = current_dev

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

    On platforms with ``O_NOFOLLOW``, walks using ``os.open()`` with
    ``O_NOFOLLOW | O_DIRECTORY`` and ``os.fstat()`` to prevent TOCTOU
    races (symlink swap between stat and open).  Rejects:

    * Symlinked directories in the path -- an attacker could redirect
      the path to a world-writable location.
    * World-writable directories -- an attacker could replace the config
      file or its parent ``.mojox/`` directory.
    * Directories owned by a user other than the current user or root.

    Only directories within the project/user boundary are checked;
    system directories above *stop_at* are not inspected.

    On platforms without ``O_NOFOLLOW`` (e.g. Windows), falls back to
    ``Path.stat()`` (no symlink protection) and skips ownership checks
    if ``os.getuid`` is unavailable.

    The walk uses the **original** (non-resolved) path so that symlinked
    directory components are not silently followed by ``Path.resolve()``.
    ``O_NOFOLLOW`` on each component catches the symlink.

    Args:
        path: The config file path to validate.
        stop_at: Trust boundary directory; checks stop here (inclusive).

    Returns:
        ``True`` if the path is secure, ``False`` otherwise.
    """
    try:
        uid = os.getuid()
    except AttributeError:
        # Windows -- skip ownership checks
        uid = None

    # Use Path.absolute() (not .resolve()) to avoid following symlinks.
    # On the O_NOFOLLOW path, the open() call itself will reject symlinks.
    p = path.absolute().parent
    stop_abs = stop_at.absolute() if stop_at is not None else None

    if _HAS_O_NOFOLLOW:
        return _is_path_secure_nofollow(p, uid=uid, stop_at=stop_abs)
    # Fallback: resolve is needed to compare paths, but loses symlink info.
    stop_resolved = stop_at.resolve() if stop_at is not None else None
    return _is_path_secure_fallback(
        path.resolve().parent, uid=uid, stop_at=stop_resolved
    )


def _is_path_secure_nofollow(
    start: Path,
    *,
    uid: int | None,
    stop_at: Path | None,
) -> bool:
    """Secure directory walk using ``O_NOFOLLOW`` to prevent TOCTOU races.

    Opens each directory with ``O_RDONLY | O_NOFOLLOW | O_DIRECTORY``
    and uses ``os.fstat()`` on the resulting fd, so the stat and the
    identity of the directory are atomically bound.  If any component
    is a symlink, ``O_NOFOLLOW`` causes ``open()`` to fail with
    ``ELOOP``, which we treat as insecure.

    Args:
        start: The starting directory (config file's parent).
        uid: Current user's uid, or ``None`` to skip ownership checks.
        stop_at: Trust boundary (inclusive); walk stops here.

    Returns:
        ``True`` if every directory from *start* up to *stop_at*
        passes ownership and permission checks.
    """
    o_nofollow = os.O_NOFOLLOW  # type: ignore[attr-defined]
    o_flags = os.O_RDONLY | o_nofollow | os.O_DIRECTORY

    p = start
    while True:
        try:
            fd = os.open(str(p), o_flags)
        except OSError:
            # Symlink or missing directory
            return False
        try:
            st = os.fstat(fd)
        finally:
            os.close(fd)

        if stat.S_ISDIR(st.st_mode) and (st.st_mode & stat.S_IWOTH):
            return False

        if uid is not None and st.st_uid != uid and st.st_uid != 0:
            return False

        if stop_at is not None and p == stop_at:
            break

        parent = p.parent
        if parent == p:
            break
        p = parent

    return True


def _is_path_secure_fallback(
    start: Path,
    *,
    uid: int | None,
    stop_at: Path | None,
) -> bool:
    """Fallback directory walk for platforms without ``O_NOFOLLOW``.

    Uses ``Path.stat()`` -- vulnerable to symlink races, but the best
    available on Windows.  Ownership checks are skipped when *uid* is
    ``None`` (``os.getuid`` unavailable).

    Args:
        start: The starting directory (config file's parent).
        uid: Current user's uid, or ``None`` to skip ownership checks.
        stop_at: Trust boundary (inclusive); walk stops here.

    Returns:
        ``True`` if every directory passes the checks.
    """
    p = start
    while True:
        try:
            st = p.stat()
        except OSError:
            return False

        if stat.S_ISDIR(st.st_mode) and (st.st_mode & stat.S_IWOTH):
            return False

        if uid is not None and st.st_uid != uid and st.st_uid != 0:
            return False

        if stop_at is not None and p == stop_at:
            break

        parent = p.parent
        if parent == p:
            break
        p = parent

    return True


def _safe_read_toml(path: Path) -> dict | None:
    """Read a TOML file, returning ``None`` on any error.

    On platforms with ``O_NOFOLLOW``, opens the file with
    ``os.open(path, O_RDONLY | O_NOFOLLOW)`` and wraps the fd with
    ``os.fdopen()`` so the file opened is exactly the one checked --
    no symlink race between security check and read.

    On platforms without ``O_NOFOLLOW``, falls back to plain
    ``open(path, 'rb')``.

    Uses ``tomllib`` on Python 3.11+ and ``tomli`` on older versions.

    Args:
        path: Path to the TOML config file.

    Returns:
        Parsed dict on success, ``None`` on any error (missing file,
        parse error, symlink on a ``O_NOFOLLOW`` platform, etc.).
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]

    try:
        if _HAS_O_NOFOLLOW:
            o_nofollow = os.O_NOFOLLOW  # type: ignore[attr-defined]
            fd = os.open(str(path), os.O_RDONLY | o_nofollow)
            try:
                with os.fdopen(fd, "rb") as f:
                    return tomllib.load(f)
            except Exception:
                # fd is consumed by fdopen; don't close again
                return None
        else:
            with open(path, "rb") as f:
                return tomllib.load(f)
    except Exception:
        return None
