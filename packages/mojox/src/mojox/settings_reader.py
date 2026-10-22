"""Discover and securely read .mojox/config.toml files.

The reader walks from the manifest directory up to the .git boundary to
find project-level config, and checks $XDG_CONFIG_HOME/mojox/config.toml
for user-level config.  Security: directories between the config file and
a trusted boundary are checked for ownership and permissions using
``os.open(O_NOFOLLOW)`` and ``os.fstat`` to prevent TOCTOU races.

The walk stops early at ``$HOME`` or at a filesystem mount boundary
(``st_dev`` change) to avoid escaping the user's project area.

This module lives in mojox (not mojox-core) so that mojox-build cannot
load local config -- making "local settings never influence mojox build
output" a structural guarantee.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path
from typing import Any

from mojox_core import ConfigError, LocalSettings, parse_settings

# Platform gate: O_NOFOLLOW prevents following symlinks on open.
# Not available on Windows; fall back to plain open() there.
_HAS_O_NOFOLLOW: bool = hasattr(os, "O_NOFOLLOW")

# ELOOP/EMLINK: O_NOFOLLOW refused a symlink (EMLINK on FreeBSD). ENOENT: the
# file vanished between discovery and open.
_SKIPPED_OPEN_ERRNOS = frozenset({errno.ELOOP, errno.EMLINK, errno.ENOENT})


def read_settings(
    manifest_dir: Path,
    env: dict[str, str],
    *,
    no_config: bool = False,
    config_file: Path | None = None,
) -> LocalSettings:
    """Discover and parse the settings files: the user file, then the nearest project file.

    *env* only locates the user file (``$XDG_CONFIG_HOME``, ``$HOME``) and the
    walk boundary; it is never read from ``os.environ`` directly. ``MOJOX_*``
    variables are read later by :func:`mojox_core.policy.resolve`, so
    ``--no-config`` (*no_config*) keeps them. *config_file* replaces discovery
    with one explicit file.

    A discovered file is skipped silently only when unsafe to trust (insecure
    owner or permissions, or a symlink); one that cannot be read or parsed is
    an error, as a misspelled key is.

    Raises:
        ConfigError: *config_file* is missing, a symlink or unreadable, a settings
            file is unreadable or not valid TOML, or it sets an invalid, misplaced
            or unknown key.
    """
    if no_config:
        return LocalSettings.EMPTY

    if config_file is not None:
        if not config_file.is_file():
            raise ConfigError(str(config_file), "does not exist")
        data = _safe_read_toml(config_file)
        if data is None:
            reason = "symlinks are refused" if config_file.is_symlink() else "does not exist"
            raise ConfigError(str(config_file), f"could not be read: {reason}")
        return parse_settings([(str(config_file), data)])

    files: list[tuple[str, dict[str, Any]]] = []

    user_path, user_boundary = _user_config_path(env)
    if user_path is not None and user_path.is_file() and _is_path_secure(user_path, stop_at=user_boundary):
        user_data = _safe_read_toml(user_path)
        if user_data is not None:
            files.append((str(user_path), user_data))

    # Resolve $HOME for the walk boundary
    home_str = env.get("HOME")
    home = Path(home_str).resolve() if home_str else None

    project_path, repo_root = _find_project_config(manifest_dir, home=home)
    if project_path is not None and _is_path_secure(project_path, stop_at=repo_root):
        project_data = _safe_read_toml(project_path)
        if project_data is not None:
            files.append((str(project_path), project_data))

    return parse_settings(files)


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
    return _is_path_secure_fallback(path.resolve().parent, uid=uid, stop_at=stop_resolved)


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
    o_nofollow = os.O_NOFOLLOW
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


def _safe_read_toml(path: Path) -> dict[str, Any] | None:
    """Parse the TOML file at *path*, or return ``None`` when it is skipped for safety.

    Opens with ``O_NOFOLLOW`` where available, so the file read is exactly the
    one checked by :func:`_is_path_secure`: a symlinked file is refused
    (``None``), as is a file that vanished after discovery. Any other failure
    means the user's configuration would silently not apply, so it is an error.

    Raises:
        ConfigError: The file is not valid TOML (UTF-8 included), or could not
            be read for another reason (e.g. permission denied); ``key_path``
            is the file path.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]

    opener = _open_nofollow if _HAS_O_NOFOLLOW else None
    try:
        with open(path, "rb", opener=opener) as f:
            return tomllib.load(f)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(str(path), f"not valid TOML: {e}") from None
    except RecursionError:
        # tomllib recurses per nesting level; tomli raises a RecursionError subclass at 400.
        raise ConfigError(str(path), "not valid TOML: nested too deeply") from None
    except OSError as e:
        if e.errno in _SKIPPED_OPEN_ERRNOS:
            return None
        raise ConfigError(str(path), f"could not be read: {e.strerror or e}") from None


def _open_nofollow(path: str, flags: int) -> int:
    """``open()`` opener that refuses to follow a symlinked final component."""
    return os.open(path, flags | os.O_NOFOLLOW)
