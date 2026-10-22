"""Discover and securely read .mojox/config.toml files.

The reader walks from the manifest directory up to the .git boundary to
find project-level config, and checks $XDG_CONFIG_HOME/mojox/config.toml
for user-level config.

Security: a settings file can inject environment variables into every
command, so the directories between it and a trusted boundary, then the
opened file itself, must be owned by the current user or root, must not be
world-writable, and may be group-writable only when the group is the user's
own private group (Debian's patched OpenSSH rule, user-group-modes; upstream
OpenSSH refuses any group-writable path).  The file must also be a regular file.
Checks use ``os.open(O_NOFOLLOW)`` and ``os.fstat`` on the fd to prevent
TOCTOU races.  Ownership and group rules are POSIX-only: where
``os.getuid`` is unavailable they are skipped, as are the file's mode checks.

The walk stops early at ``$HOME`` or at a filesystem mount boundary
(``st_dev`` change) to avoid escaping the user's project area.

This module lives in mojox (not mojox-core) so that mojox-build cannot
load local config -- making "local settings never influence mojox build
output" a structural guarantee.
"""

from __future__ import annotations

import errno
import functools
import os
import stat
import sys
from pathlib import Path
from typing import Any

from mojox_core import ConfigError, LocalSettings, parse_settings
from mojox_core.errors import escape_for_display

# Platform gate: O_NOFOLLOW prevents following symlinks on open.
# Not available on Windows; fall back to plain open() there.
_HAS_O_NOFOLLOW: bool = hasattr(os, "O_NOFOLLOW")

# O_NOFOLLOW refused a symlink (EMLINK on FreeBSD).
_SYMLINK_ERRNOS = frozenset({errno.ELOOP, errno.EMLINK})

# A FIFO would block open() until a writer appears; regular files ignore the flag.
_O_NONBLOCK: int = getattr(os, "O_NONBLOCK", 0)


class _UntrustedFileError(Exception):
    """A settings file refused before parsing; the message names the reason, never file content."""


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

    A discovered file that is unsafe to trust (see the module docstring) is
    ignored with one ``mojox: ignoring <path>: untrusted: <reason>`` line on
    stderr; one that vanished after discovery is skipped silently. One that
    cannot be read or parsed is an error, as a misspelled key is.

    Raises:
        ConfigError: *config_file* is missing, untrusted or unreadable; a
            settings file is unreadable or not valid TOML; or it sets an
            invalid, misplaced or unknown key.
    """
    if no_config:
        return LocalSettings.EMPTY
    _is_private_group.cache_clear()

    if config_file is not None:
        try:
            data = _read_trusted_toml(config_file)
        except FileNotFoundError:
            raise ConfigError(str(config_file), "does not exist") from None
        except _UntrustedFileError as e:
            raise ConfigError(str(config_file), str(e)) from None
        return parse_settings([(str(config_file), data)])

    files: list[tuple[str, dict[str, Any]]] = []

    user_path, user_boundary = _user_config_path(env)
    if user_path is not None and user_path.is_file():
        user_data = _read_discovered(user_path, stop_at=user_boundary)
        if user_data is not None:
            files.append((str(user_path), user_data))

    # Resolve $HOME for the walk boundary
    home_str = env.get("HOME")
    home = Path(home_str).resolve() if home_str else None

    project_path, repo_root = _find_project_config(manifest_dir, home=home)
    if project_path is not None:
        project_data = _read_discovered(project_path, stop_at=repo_root)
        if project_data is not None:
            files.append((str(project_path), project_data))

    return parse_settings(files)


def _read_discovered(path: Path, *, stop_at: Path | None) -> dict[str, Any] | None:
    """Read a discovered settings file, or ``None`` when it is ignored.

    An untrusted file is reported on stderr (path and reason only); a file
    that vanished after discovery is skipped silently.

    Raises:
        ConfigError: As :func:`_read_trusted_toml`.
    """
    problem = _untrusted_directory(path, stop_at=stop_at)
    if problem is None:
        try:
            return _read_trusted_toml(path)
        except FileNotFoundError:
            return None
        except _UntrustedFileError as e:
            problem = str(e)
    print(f"mojox: ignoring {escape_for_display(str(path))}: {problem}", file=sys.stderr)
    return None


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


def _current_uid() -> int | None:
    """The current uid, or ``None`` where POSIX ownership does not exist (Windows)."""
    try:
        return os.getuid()
    except AttributeError:
        return None


def _write_problem(st: os.stat_result, uid: int | None) -> str | None:
    """Why another user could write or own the inode behind *st*, or ``None``.

    World-write is always refused. Group-write is allowed only for the user's
    private group (see :func:`_is_private_group`), as Debian's patched OpenSSH
    does; upstream OpenSSH refuses it outright. The owner must be *uid* or
    root. Group and owner checks are skipped when *uid* is ``None``.
    """
    if st.st_mode & stat.S_IWOTH:
        return "writable by other users"
    if uid is None:
        return None
    if st.st_mode & stat.S_IWGRP and not _is_private_group(st.st_gid, uid):
        return "writable by a shared group"
    if st.st_uid not in (uid, 0):
        return "owned by another user"
    return None


@functools.cache
def _is_private_group(gid: int, uid: int) -> bool:
    """Whether *gid* is the user's own group, shared with no other account.

    ``gr_mem`` lists only supplementary members, so every other account whose
    primary group is *gid* is also searched for. That enumerates the account
    database (possibly LDAP/NSS), so callers check this only for a
    group-writable inode, and the answer is cached for one
    :func:`read_settings` call (umask-002 trees make every directory
    group-writable). Any failed lookup counts as shared (fail closed).
    Partly visible directory services (e.g. sssd with ``enumerate = false``
    or ``ignore_group_members``) can hide other members, so there the check
    is best-effort.
    """
    if gid != os.getgid():
        return False
    try:
        import grp
        import pwd

        members = grp.getgrgid(gid).gr_mem
        name = pwd.getpwuid(uid).pw_name
        accounts = pwd.getpwall()
    except (ImportError, KeyError, OSError):
        return False
    if any(member != name for member in members):
        return False
    return not any(account.pw_gid == gid and account.pw_uid != uid for account in accounts)


def _untrusted_directory(path: Path, *, stop_at: Path | None = None) -> str | None:
    """Why a directory between *path* and *stop_at* is untrusted, or ``None``.

    Rejects symlinked or unopenable directories (an attacker could redirect
    the path) and any directory failing :func:`_write_problem` (an attacker
    could replace the config file or its parent ``.mojox/``). Only
    directories within the boundary (*stop_at*, inclusive) are checked.

    On platforms with ``O_NOFOLLOW`` the walk binds each stat to an fd opened
    without following symlinks, using the **original** (non-resolved) path so
    a symlinked component is caught rather than resolved. Elsewhere (Windows)
    it falls back to ``Path.stat()`` with no symlink protection.

    Returns:
        ``"untrusted: directory <dir> is <reason>"`` for the first bad
        directory, or ``None`` if all pass.
    """
    uid = _current_uid()

    # Use Path.absolute() (not .resolve()) to avoid following symlinks.
    # On the O_NOFOLLOW path, the open() call itself will reject symlinks.
    p = path.absolute().parent
    stop_abs = stop_at.absolute() if stop_at is not None else None

    if _HAS_O_NOFOLLOW:
        return _untrusted_directory_nofollow(p, uid=uid, stop_at=stop_abs)
    # Fallback: resolve is needed to compare paths, but loses symlink info.
    stop_resolved = stop_at.resolve() if stop_at is not None else None
    return _untrusted_directory_fallback(path.resolve().parent, uid=uid, stop_at=stop_resolved)


def _untrusted_directory_nofollow(start: Path, *, uid: int | None, stop_at: Path | None) -> str | None:
    """Walk up from *start* opening each directory ``O_NOFOLLOW | O_DIRECTORY`` and checking its fd.

    A symlinked component makes ``open()`` fail with ``ELOOP``, reported as
    untrusted.
    """
    o_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY

    p = start
    while True:
        try:
            fd = os.open(str(p), o_flags)
        except OSError:
            return f"untrusted: directory {escape_for_display(str(p))} is a symlink or cannot be opened"
        try:
            st = os.fstat(fd)
        finally:
            os.close(fd)

        problem = _write_problem(st, uid)
        if problem is not None:
            return f"untrusted: directory {escape_for_display(str(p))} is {problem}"

        if stop_at is not None and p == stop_at:
            return None
        parent = p.parent
        if parent == p:
            return None
        p = parent


def _untrusted_directory_fallback(start: Path, *, uid: int | None, stop_at: Path | None) -> str | None:
    """Walk up from *start* with ``Path.stat()``: racy, but the best available without ``O_NOFOLLOW``."""
    p = start
    while True:
        try:
            st = p.stat()
        except OSError:
            return f"untrusted: directory {escape_for_display(str(p))} cannot be read"

        problem = _write_problem(st, uid)
        if problem is not None:
            return f"untrusted: directory {escape_for_display(str(p))} is {problem}"

        if stop_at is not None and p == stop_at:
            return None
        parent = p.parent
        if parent == p:
            return None
        p = parent


def _read_trusted_toml(path: Path) -> dict[str, Any]:
    """Parse the TOML file at *path* after checking the opened file can be trusted.

    Opens with ``O_NOFOLLOW`` where available, so a symlinked final component
    is refused, and ``O_NONBLOCK`` so a FIFO cannot stall the open. The trust
    checks then run on ``os.fstat`` of that same fd (no TOCTOU): a regular
    file, and :func:`_write_problem` where POSIX ownership exists. Directories
    above the file are not checked here; see :func:`_untrusted_directory`.

    Raises:
        FileNotFoundError: The file does not exist (or vanished after discovery).
        _UntrustedFileError: The file is a symlink, not a regular file, or
            writable or owned by another user.
        ConfigError: The file is not valid TOML (UTF-8 included), or could not
            be read for another reason (e.g. permission denied); ``key_path``
            is the file path.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]

    flags = os.O_RDONLY | _O_NONBLOCK | getattr(os, "O_BINARY", 0)
    if _HAS_O_NOFOLLOW:
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as e:
        if e.errno in _SYMLINK_ERRNOS:
            raise _UntrustedFileError("untrusted: symlinks are refused") from None
        raise ConfigError(str(path), f"could not be read: {e.strerror or e}") from None
    try:
        _check_file_trusted(os.fstat(fd))
        with open(fd, "rb", closefd=False) as f:
            return tomllib.load(f)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(str(path), f"not valid TOML: {e}") from None
    except RecursionError:
        # tomllib recurses per nesting level; tomli raises a RecursionError subclass at 400.
        raise ConfigError(str(path), "not valid TOML: nested too deeply") from None
    except OSError as e:
        raise ConfigError(str(path), f"could not be read: {e.strerror or e}") from None
    finally:
        os.close(fd)


def _check_file_trusted(st: os.stat_result) -> None:
    """Refuse an opened settings file that is not a regular file or that another user could write.

    Raises:
        _UntrustedFileError: The message is ``untrusted: <reason>``.
    """
    if not stat.S_ISREG(st.st_mode):
        raise _UntrustedFileError("untrusted: not a regular file")
    uid = _current_uid()
    if uid is None:
        return
    problem = _write_problem(st, uid)
    if problem is not None:
        raise _UntrustedFileError(f"untrusted: {problem}")
