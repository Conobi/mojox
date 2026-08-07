"""Ore acceleration: LLVM bitcode splitting for 7x dev-build speedup.

Ore pre-compiles the library portion of a program's LLVM output into a
.ore file (a standard relocatable object). Subsequent targets link their
user-specific code against it, skipping the expensive LLVM codegen for
the library. The .ore is cached per compiler version and dependency tree.

This module lives in mojox (not mojox-core) because it is an exec-layer
optimization that shells out to LLVM tools.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from mojox_core import Command, CommandKind

from ._diagnostics import parse_diagnostics
from ._types import Outcome, OutcomeKind

# Tools checked by probe_llvm_tools(), in probe order.
_LLVM_TOOLS: tuple[str, ...] = ("llvm-extract", "llc", "llvm-nm", "clang")


@dataclass(frozen=True)
class OreContext:
    """Configuration snapshot for ore acceleration on a single build.

    All fields needed to decide whether a cached .ore file is still valid
    and to drive the LLVM bitcode splitting pipeline.

    Attributes:
        enabled: Whether ore acceleration is active for this invocation.
        seed: Path to the ore-seed .mojo source file, or None for first-target-as-seed.
        include_paths: Extra include directories passed to the compiler.
        compiler_version: Version string of the active Mojo compiler.
        mojo_path: Absolute path to the ``mojo`` binary.
        runtime_lib_dir: Directory containing the Mojo runtime library.
        dep_versions: Sorted (name, version) pairs for all dependencies.
    """

    enabled: bool
    seed: Path | None
    include_paths: tuple[str, ...]
    compiler_version: str
    mojo_path: str
    runtime_lib_dir: Path
    dep_versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class OreProbeResult:
    """Result of probing the host for required LLVM tools.

    Attributes:
        available: True when every required tool was found on PATH.
        missing_tool: Name of the first tool that was not found, or None.
        llvm_extract: Resolved path to ``llvm-extract``, or None.
        llc: Resolved path to ``llc``, or None.
        llvm_nm: Resolved path to ``llvm-nm``, or None.
        clang: Resolved path to ``clang``, or None.
    """

    available: bool
    missing_tool: str | None
    llvm_extract: str | None
    llc: str | None
    llvm_nm: str | None
    clang: str | None


def probe_llvm_tools() -> OreProbeResult:
    """Check PATH for the LLVM tools required by ore acceleration.

    Uses :func:`shutil.which` to locate each tool. The probe order is
    deterministic: ``llvm-extract``, ``llc``, ``llvm-nm``, ``clang``.
    If any tool is missing, ``available`` is False and ``missing_tool``
    names the first absent tool.

    Returns:
        An OreProbeResult describing tool availability.
    """
    paths: dict[str, str | None] = {}
    first_missing: str | None = None

    for tool in _LLVM_TOOLS:
        resolved = shutil.which(tool)
        paths[tool] = resolved
        if resolved is None and first_missing is None:
            first_missing = tool

    return OreProbeResult(
        available=first_missing is None,
        missing_tool=first_missing,
        llvm_extract=paths["llvm-extract"],
        llc=paths["llc"],
        llvm_nm=paths["llvm-nm"],
        clang=paths["clang"],
    )


def compute_cache_key(
    compiler_version: str,
    dep_versions: tuple[tuple[str, str], ...],
    seed_path: Path | None = None,
    defines: dict[str, str] | None = None,
) -> str:
    """Compute a 24-character hex cache key from build inputs.

    The key is the first 24 hex characters of a SHA-256 digest built from:

    1. ``"compiler:{version}\\n"``
    2. Sorted ``"dep:{name}=={ver}\\n"`` lines for each dependency
    3. Sorted ``"define:{key}={value}\\n"`` lines for active defines
    4. The seed file's raw bytes if *seed_path* is a regular file,
       otherwise the literal ``"seed:implicit\\n"``

    Args:
        compiler_version: The Mojo compiler version string.
        dep_versions: Sorted (name, version) pairs for all dependencies.
        seed_path: Optional path to the ore-seed source file.
        defines: Active profile defines (e.g. ``{"ASSERT": "all"}``).

    Returns:
        A 24-character lowercase hex digest string.
    """
    h = hashlib.sha256()
    h.update(f"compiler:{compiler_version}\n".encode())

    for name, ver in sorted(dep_versions):
        h.update(f"dep:{name}=={ver}\n".encode())

    if defines:
        for key, value in sorted(defines.items()):
            h.update(f"define:{key}={value}\n".encode())

    if seed_path is not None and seed_path.is_file():
        h.update(seed_path.read_bytes())
    else:
        h.update(b"seed:implicit\n")

    return h.hexdigest()[:24]


class OreCache:
    """Manages cached .ore files in a directory, keyed by content hash.

    Each cached artifact is stored at ``<cache_dir>/<key>/lib.ore``.
    Writes are atomic: the source file is first copied to a temporary name
    inside the key directory, then renamed into place via :func:`os.rename`.

    Attributes:
        cache_dir: Root directory for all cached .ore artifacts.
    """

    def __init__(self, cache_dir: Path) -> None:
        """Initialise the cache at the given directory.

        Args:
            cache_dir: Root directory for cached .ore files. Created lazily
                on the first :meth:`put` call.
        """
        self.cache_dir = cache_dir

    def get(self, key: str) -> Path | None:
        """Look up a cached .ore file by key.

        Args:
            key: The cache key (typically a 24-char hex digest).

        Returns:
            The path to ``lib.ore`` if it exists, or None on a cache miss.
        """
        candidate = self.cache_dir / key / "lib.ore"
        if candidate.is_file():
            return candidate
        return None

    def put(self, key: str, source: Path) -> Path:
        """Atomically store a .ore file in the cache.

        The *source* file is copied to a temporary name inside the key
        directory, then atomically renamed to ``lib.ore``.  This ensures
        concurrent readers never see a partially-written file.

        Args:
            key: The cache key to store under.
            source: Path to the .ore file to cache.

        Returns:
            The final path to the cached ``lib.ore`` file.
        """
        key_dir = self.cache_dir / key
        key_dir.mkdir(parents=True, exist_ok=True)

        tmp_name = f".lib.ore.{os.getpid()}.tmp"
        tmp_path = key_dir / tmp_name
        final_path = key_dir / "lib.ore"

        shutil.copy2(source, tmp_path)
        os.rename(tmp_path, final_path)

        return final_path


# -- Ore eligibility ---------------------------------------------------------

# CommandKinds that benefit from ore acceleration. Compile-only commands
# (COMPILE_PACKAGE, COMPILE_BINARY) do not execute, so splitting/linking
# the library portion would add overhead with no payoff.
_ORE_ELIGIBLE_KINDS: frozenset[CommandKind] = frozenset(
    {CommandKind.RUN_TEST, CommandKind.CHECK_EXAMPLE, CommandKind.RUN}
)


def is_ore_eligible(kind: CommandKind) -> bool:
    """Return True if *kind* benefits from ore acceleration.

    Only commands that compile **and** execute a single-file target are
    eligible. Compile-only commands (``COMPILE_PACKAGE``,
    ``COMPILE_BINARY``) do not benefit because they never run the binary.

    Args:
        kind: The command kind to check.

    Returns:
        True for ``RUN_TEST``, ``CHECK_EXAMPLE``, and ``RUN``; False
        otherwise.
    """
    return kind in _ORE_ELIGIBLE_KINDS


# -- LLVM pipeline helpers ---------------------------------------------------

# Runtime libraries linked when producing the final executable.
_RUNTIME_LIBS: tuple[str, ...] = (
    "-lKGENCompilerRTShared",
    "-lAsyncRTMojoBindings",
    "-lMSupportGlobals",
    "-lAsyncRTRuntimeGlobals",
    "-lstdc++",
    "-lm",
    "-ldl",
    "-lpthread",
)


def _platform_link_flags() -> list[str]:
    """Return platform-specific linker flags for ore linking.

    On Linux, ``--allow-multiple-definition`` suppresses duplicate symbol
    errors from the ore/user split.  On macOS, the equivalent is
    ``-multiply_defined,suppress``.

    Returns:
        A list of linker flags appropriate for ``sys.platform``.
    """
    if sys.platform == "linux":
        return ["-Wl,--allow-multiple-definition"]
    if sys.platform == "darwin":
        return ["-Wl,-multiply_defined,suppress"]
    return []


def _build_ore(
    seed_bc: Path,
    probe: OreProbeResult,
    output: Path,
    *,
    seed_module: str = "",
) -> tuple[bool, str]:
    """Compile a library .ore from seed bitcode.

    Steps:
    1. ``llvm-extract --delete`` strips user entry-point code, leaving
       only library functions in a temporary bitcode file.
    2. ``llc -O3 -filetype=obj -relocation-model=pic`` compiles the
       library bitcode into a relocatable object (``.ore``).

    Args:
        seed_bc: Path to the seed LLVM bitcode file.
        probe: A successful probe result with tool paths.
        output: Destination path for the compiled .ore object.

    Returns:
        A ``(success, stderr_on_failure)`` tuple. On success the second
        element is the empty string.
    """
    assert probe.available, "probe must report all tools available"
    assert probe.llvm_extract is not None
    assert probe.llc is not None

    with tempfile.TemporaryDirectory(prefix="ore-build-") as td:
        lib_bc = Path(td) / "lib.bc"

        # Step 1: strip user entry-point code from seed bitcode.
        extract_args = [
            probe.llvm_extract,
            "--delete",
            "--func=main",
            "--rfunc=__wrap_and_execute_raising_main",
        ]
        if seed_module:
            extract_args.append(f"--func={seed_module}::main()")
        extract_args.extend([str(seed_bc), "-o", str(lib_bc)])

        extract_result = subprocess.run(
            extract_args,
            capture_output=True,
            text=True,
        )
        if extract_result.returncode != 0:
            return False, extract_result.stderr

        # Step 2: compile library bitcode to relocatable object.
        llc_result = subprocess.run(
            [
                probe.llc,
                "-O3",
                "-filetype=obj",
                "-relocation-model=pic",
                str(lib_bc),
                "-o",
                str(output),
            ],
            capture_output=True,
            text=True,
        )
        if llc_result.returncode != 0:
            return False, llc_result.stderr

    return True, ""


def _extract_user_symbols(
    full_bc: Path,
    ore_path: Path,
    probe: OreProbeResult,
) -> set[str]:
    """Compute the set of user-defined symbols not present in the ore.

    Uses ``llvm-nm`` on the user bitcode and ``nm`` (via ``llvm-nm``) on
    the pre-compiled .ore to find the difference. Symbols defined in the
    user bitcode but absent from the ore are user-specific and must be
    extracted for the final link.

    This is the primary extraction method -- robust against naming changes
    because it operates on the actual symbol tables rather than relying on
    heuristics.

    Args:
        full_bc: Path to the full program LLVM bitcode.
        ore_path: Path to the pre-compiled .ore object file.
        probe: A successful probe result with tool paths.

    Returns:
        The set of symbol names present in the user bitcode but not in
        the ore.
    """
    assert probe.llvm_nm is not None

    def _collect_defined_symbols(path: Path) -> set[str]:
        """Run llvm-nm and collect defined (non-undefined) symbol names."""
        result = subprocess.run(
            [probe.llvm_nm, "--defined-only", str(path)],
            capture_output=True,
            text=True,
        )
        symbols: set[str] = set()
        for line in result.stdout.splitlines():
            # llvm-nm output: "<address> <type> <name>"
            # Address is 16 chars, then space, then 1-char type, then space,
            # then the full symbol name (which may contain spaces).
            parts = line.strip().split(maxsplit=2)
            if len(parts) >= 3:
                symbols.add(parts[2])
        return symbols

    user_syms = _collect_defined_symbols(full_bc)
    ore_syms = _collect_defined_symbols(ore_path)

    return user_syms - ore_syms


def run_ore_pipeline(
    cmd: "Command",
    ore_context: OreContext,
    probe: OreProbeResult,
    ore_path: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> Outcome:
    """Execute a 6-step ore-accelerated build-and-run pipeline.

    The pipeline splits a Mojo program's LLVM output into library
    (pre-compiled in the .ore) and user portions, links them, and
    executes the result. This avoids the expensive LLVM codegen for
    library code on every invocation.

    Steps:
        1. ``mojo build --emit llvm-bitcode`` to produce full.bc
        2. ``_extract_user_symbols`` to find user-only symbols
        3. ``llvm-extract`` to isolate user functions into user.bc
        4. ``llc`` to compile user.bc into user.o
        5. ``clang`` to link user.o + .ore + runtime libs into a binary
        6. Execute the binary and capture output

    The source file is taken from ``cmd.argv[-1]``. ``-D`` defines are
    forwarded from ``cmd.argv``, and include paths come from
    ``ore_context.include_paths``.

    Args:
        cmd: The original command whose last argv element is the .mojo source.
        ore_context: The ore configuration snapshot.
        probe: A successful probe result with all LLVM tool paths.
        ore_path: Path to the pre-compiled .ore object file.
        extra_env: Additional environment variables (from LocalSettings.env).

    Returns:
        An :class:`Outcome` matching ``mojo run`` semantics: stdout/stderr
        from step 6 on success, ``COMPILE_ERROR`` on steps 1-5 failure,
        ``TIMEOUT`` or ``CRASH`` as appropriate.
    """
    env = dict(cmd.env)
    if extra_env:
        merged = dict(extra_env)
        merged.update(env)
        env = merged

    source_file = cmd.argv[-1]
    start = time.monotonic()

    # Forward relevant flags from the original command.
    defines: list[str] = []
    extra_flags: list[str] = []
    argv_list = list(cmd.argv)
    for i, arg in enumerate(argv_list):
        if arg == "-D" and i + 1 < len(argv_list):
            defines.extend(["-D", argv_list[i + 1]])
        elif arg.startswith("-D") and len(arg) > 2:
            defines.append(arg)
        elif arg == "--num-threads" and i + 1 < len(argv_list):
            extra_flags.extend(["--num-threads", argv_list[i + 1]])

    # Include paths from ore_context.
    include_args: list[str] = []
    for inc in ore_context.include_paths:
        include_args.extend(["-I", inc])

    with tempfile.TemporaryDirectory(prefix="ore-run-") as td:
        work = Path(td)
        full_bc = work / "full.bc"
        user_bc = work / "user.bc"
        user_o = work / "user.o"
        binary = work / "program"

        # Step 1: mojo build --emit llvm-bitcode → full.bc
        print(f"ore: [1/6] emitting bitcode for {source_file}", file=sys.stderr)
        step1_start = time.monotonic()
        mojo_args = [
            ore_context.mojo_path,
            "build",
            "--emit",
            "llvm-bitcode",
            "-o",
            str(full_bc),
        ]
        mojo_args.extend(defines)
        mojo_args.extend(extra_flags)
        mojo_args.extend(include_args)
        mojo_args.append(source_file)

        step1 = subprocess.run(
            mojo_args,
            cwd=str(cmd.cwd),
            env=env,
            capture_output=True,
            text=True,
        )
        if step1.returncode != 0:
            elapsed = time.monotonic() - start
            return Outcome(
                command=cmd,
                kind=OutcomeKind.COMPILE_ERROR,
                exit_code=step1.returncode,
                stdout=step1.stdout,
                stderr=step1.stderr,
                diagnostics=parse_diagnostics(step1.stderr),
                elapsed_s=elapsed,
            )

        step1_elapsed = time.monotonic() - step1_start
        print(f"ore: [1/6] bitcode emitted in {step1_elapsed:.1f}s", file=sys.stderr)

        # Step 2: symbol diff
        user_symbols = _extract_user_symbols(full_bc, ore_path, probe)

        print(f"ore: [2/6] extracted {len(user_symbols)} user symbols", file=sys.stderr)

        # Step 3: llvm-extract user functions + globals → user.bc
        # static_string_* and global_constant_* are globals, not functions —
        # use --rglob (regex global) instead of --func.
        assert probe.llvm_extract is not None
        extract_args = [probe.llvm_extract]
        for sym in sorted(user_symbols):
            if sym.startswith("static_string") or sym.startswith("global_constant"):
                continue
            extract_args.append(f"--func={sym}")
        extract_args.append("--rglob=static_string")
        extract_args.append("--rglob=global_constant")
        extract_args.extend([str(full_bc), "-o", str(user_bc)])

        step3 = subprocess.run(
            extract_args,
            capture_output=True,
            text=True,
        )
        if step3.returncode != 0:
            elapsed = time.monotonic() - start
            return Outcome(
                command=cmd,
                kind=OutcomeKind.COMPILE_ERROR,
                exit_code=step3.returncode,
                stdout=step3.stdout,
                stderr=step3.stderr,
                diagnostics=parse_diagnostics(step3.stderr),
                elapsed_s=elapsed,
            )

        # Step 4: llc → user.o
        print("ore: [4/6] compiling user bitcode", file=sys.stderr)
        assert probe.llc is not None
        step4 = subprocess.run(
            [
                probe.llc,
                "-O3",
                "-filetype=obj",
                "-relocation-model=pic",
                str(user_bc),
                "-o",
                str(user_o),
            ],
            capture_output=True,
            text=True,
        )
        if step4.returncode != 0:
            elapsed = time.monotonic() - start
            return Outcome(
                command=cmd,
                kind=OutcomeKind.COMPILE_ERROR,
                exit_code=step4.returncode,
                stdout=step4.stdout,
                stderr=step4.stderr,
                diagnostics=parse_diagnostics(step4.stderr),
                elapsed_s=elapsed,
            )

        # Step 5: clang link → binary
        print("ore: [5/6] linking binary", file=sys.stderr)
        assert probe.clang is not None
        link_args = [
            probe.clang,
            str(user_o),
            str(ore_path),
            "-o",
            str(binary),
            f"-L{ore_context.runtime_lib_dir}",
            f"-Wl,-rpath,{ore_context.runtime_lib_dir}",
        ]
        # Add -L and -rpath for native lib directories in include paths.
        for inc in ore_context.include_paths:
            lib_dir = Path(inc) / "lib"
            if lib_dir.is_dir():
                link_args.append(f"-L{lib_dir}")
                link_args.append(f"-Wl,-rpath,{lib_dir}")
        link_args.extend(_RUNTIME_LIBS)
        link_args.extend(_platform_link_flags())

        step5 = subprocess.run(
            link_args,
            capture_output=True,
            text=True,
        )
        if step5.returncode != 0:
            elapsed = time.monotonic() - start
            return Outcome(
                command=cmd,
                kind=OutcomeKind.COMPILE_ERROR,
                exit_code=step5.returncode,
                stdout=step5.stdout,
                stderr=step5.stderr,
                diagnostics=parse_diagnostics(step5.stderr),
                elapsed_s=elapsed,
            )

        # Step 6: execute binary
        print("ore: [6/6] running", file=sys.stderr)
        try:
            step6 = subprocess.run(
                [str(binary)],
                cwd=str(cmd.cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=cmd.timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            elapsed = time.monotonic() - start
            stdout = e.stdout or ""
            stderr = e.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return Outcome(
                command=cmd,
                kind=OutcomeKind.TIMEOUT,
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                diagnostics=parse_diagnostics(stderr),
                elapsed_s=elapsed,
            )

        elapsed = time.monotonic() - start

        if step6.returncode < 0:
            kind = OutcomeKind.CRASH
            exit_code = step6.returncode
        elif step6.returncode == 0:
            kind = OutcomeKind.PASS
            exit_code = 0
        else:
            kind = OutcomeKind.FAIL
            exit_code = step6.returncode

        print(f"ore: done in {elapsed:.1f}s", file=sys.stderr)

        return Outcome(
            command=cmd,
            kind=kind,
            exit_code=exit_code,
            stdout=step6.stdout,
            stderr=step6.stderr,
            diagnostics=parse_diagnostics(step6.stderr),
            elapsed_s=elapsed,
        )


# -- Exec-layer integration ---------------------------------------------------

# Module-level cached probe result, populated lazily by _try_ore_run.
_cached_probe: OreProbeResult | None = None
# Serializes the cache-miss → build → put path so concurrent workers
# don't redundantly build the .ore (~29s each).
_ore_build_lock = threading.Lock()


def _try_ore_run(
    cmd: Command,
    ctx: OreContext,
    *,
    extra_env: dict[str, str] | None = None,
) -> Outcome | None:
    """Attempt to run *cmd* through the ore pipeline.

    Returns an :class:`Outcome` on success, or ``None`` to signal that
    the caller should fall back to the standard ``mojo run`` path.

    Guards:
    - If ``ctx.include_paths`` is empty, returns None (no deps means
      the .ore would be empty and the overhead is not worthwhile).
    - If the LLVM probe fails, returns None.
    - On any build/cache failure, returns None (fallback).

    The LLVM tool probe result is cached at module level so subsequent
    calls within the same process skip the ``shutil.which`` overhead.

    Args:
        cmd: The command to execute via ore acceleration.
        ctx: The ore configuration snapshot.
        extra_env: Additional environment variables (from LocalSettings.env).

    Returns:
        An Outcome if the ore pipeline succeeded, or None to fall back.
    """
    global _cached_probe  # noqa: PLW0603

    # No include paths → no library code to pre-compile.
    if not ctx.include_paths:
        print("ore: skipped (no include paths)", file=sys.stderr)
        return None

    # Probe LLVM tools (cached across calls within the process).
    if _cached_probe is None:
        _cached_probe = probe_llvm_tools()
    probe = _cached_probe

    if not probe.available:
        print(
            f"ore: unavailable — LLVM tool '{probe.missing_tool}' not found, "
            "falling back to mojo run",
            file=sys.stderr,
        )
        return None

    # Extract defines from the command for cache key computation.
    cmd_defines: dict[str, str] = {}
    argv_list = list(cmd.argv)
    for i, arg in enumerate(argv_list):
        if arg == "-D" and i + 1 < len(argv_list):
            parts = argv_list[i + 1].split("=", 1)
            if len(parts) == 2:
                cmd_defines[parts[0]] = parts[1]

    key = compute_cache_key(
        ctx.compiler_version, ctx.dep_versions,
        seed_path=ctx.seed, defines=cmd_defines,
    )
    cache = OreCache(cache_dir=Path(str(cmd.cwd)) / ".mojox" / "cache" / "ore")
    cached_path = cache.get(key)

    if cached_path is None:
        print(f"ore: cache miss [{key[:12]}…], building .ore", file=sys.stderr)
        with _ore_build_lock:
            cached_path = cache.get(key)
            if cached_path is None:
                build_start = time.monotonic()
                cached_path = _build_and_cache_ore(
                    cmd, ctx, probe, cache, key, extra_env=extra_env,
                )
                if cached_path is None:
                    print("ore: build failed, falling back to mojo run", file=sys.stderr)
                    return None
                build_elapsed = time.monotonic() - build_start
                print(f"ore: cached [{key[:12]}…] in {build_elapsed:.1f}s", file=sys.stderr)
    else:
        print(f"ore: cache hit [{key[:12]}…]", file=sys.stderr)

    # Run through the ore pipeline with the cached .ore.
    return run_ore_pipeline(cmd, ctx, probe, cached_path, extra_env=extra_env)


def _build_and_cache_ore(
    cmd: Command,
    ctx: OreContext,
    probe: OreProbeResult,
    cache: OreCache,
    key: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> Path | None:
    """Build a .ore from the seed and store it in the cache.

    If an explicit seed is configured, its include paths are verified
    against the targets. On mismatch a warning is printed and None is
    returned.

    The seed is compiled to LLVM bitcode via ``mojo build --emit
    llvm-bitcode``, then :func:`_build_ore` produces the .ore object.
    The result is stored via :meth:`OreCache.put`.

    Args:
        cmd: The command whose source is used as implicit seed when
            ``ctx.seed`` is None.
        ctx: The ore configuration snapshot.
        probe: A successful LLVM probe result.
        cache: The ore cache to store into.
        key: The cache key for this build.
        extra_env: Additional environment variables (from LocalSettings.env).

    Returns:
        The path to the cached .ore file, or None on failure.
    """
    seed_source = ctx.seed if ctx.seed is not None else Path(cmd.argv[-1])

    # If explicit seed: verify it uses the same -I paths as targets.
    if ctx.seed is not None:
        cmd_includes = tuple(
            cmd.argv[i + 1] for i, a in enumerate(cmd.argv[:-1]) if a == "-I"
        )
        if cmd_includes and set(cmd_includes) != set(ctx.include_paths):
            print(
                "ore-seed-include-mismatch: seed include paths differ from target, "
                "falling back to mojo run",
                file=sys.stderr,
            )
            return None

    env = dict(cmd.env)
    if extra_env:
        merged = dict(extra_env)
        merged.update(env)
        env = merged

    include_args: list[str] = []
    for inc in ctx.include_paths:
        include_args.extend(["-I", inc])

    # Forward -D defines from the original command so the seed is
    # compiled with the same profile flags (e.g. -D ASSERT=all).
    define_args: list[str] = []
    argv_list = list(cmd.argv)
    for i, arg in enumerate(argv_list):
        if arg == "-D" and i + 1 < len(argv_list):
            define_args.extend(["-D", argv_list[i + 1]])
        elif arg.startswith("-D") and len(arg) > 2:
            define_args.append(arg)

    with tempfile.TemporaryDirectory(prefix="ore-seed-") as td:
        work = Path(td)
        seed_bc = work / "seed.bc"
        ore_output = work / "lib.ore"

        # Compile seed to LLVM bitcode.
        mojo_args = [
            ctx.mojo_path,
            "build",
            "--emit",
            "llvm-bitcode",
            "-o",
            str(seed_bc),
        ]
        mojo_args.extend(define_args)
        mojo_args.extend(include_args)
        mojo_args.append(str(seed_source))

        compile_result = subprocess.run(
            mojo_args,
            cwd=str(cmd.cwd),
            env=env,
            capture_output=True,
            text=True,
        )
        if compile_result.returncode != 0:
            print(
                f"ore-seed-compile-failed: seed compilation failed "
                f"(exit {compile_result.returncode}), falling back to mojo run",
                file=sys.stderr,
            )
            return None

        # Build .ore from seed bitcode.
        seed_module = Path(seed_source).stem
        success, stderr = _build_ore(
            seed_bc, probe, ore_output, seed_module=seed_module,
        )
        if not success:
            print(f"ore-unavailable: .ore build failed: {stderr}, falling back to mojo run", file=sys.stderr)
            return None

        # Cache the result.
        return cache.put(key, ore_output)
