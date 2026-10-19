"""mojox CLI: build, test, and run Mojo projects.

Subcommands:
  test      Run test targets (dev profile by default)
  run       Run a single Mojo file (dev profile by default)
  build     Compile binary targets (the manifest's build-profile by default, else release)
  check     Validate manifest and run lints (compiles libs if a compiler is available)
  metadata  Output the build plan as JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from mojox_core import CommandKind, ConfigError
from mojox_core.policy import parse_count_text, parse_jobs

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from mojox_core import (
        Command,
        HostFacts,
        LocalSettings,
        Manifest,
        Policy,
        ResolvedEnv,
        TargetGraph,
        Toolchain,
    )

    from .exec import CacheContext
    from .lints import LintFinding
    from .types import Outcome, OutputFormat, OutputMode

_TEST_KINDS = frozenset({CommandKind.RUN_TEST, CommandKind.BUILD_TEST})


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="mojox",
        description="Build, test, and run Mojo projects.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    test_p = sub.add_parser("test", help="Run test targets")
    test_p.set_defaults(profile="dev")
    _add_common_flags(test_p, profile_default="dev")
    _add_test_flags(test_p)

    run_p = sub.add_parser("run", help="Run a Mojo file")
    run_p.add_argument("file", help="The .mojo file to run")
    run_p.set_defaults(profile="dev")
    _add_common_flags(run_p, profile_default="dev")
    _add_no_cache_flag(run_p)

    build_p = sub.add_parser("build", help="Compile binary targets")
    build_p.set_defaults(profile=None)  # None: fall back to the manifest's build-profile
    _add_common_flags(build_p, profile_default="build-profile from pyproject.toml, else release")

    check_p = sub.add_parser("check", help="Check project: compile packages and run lints")
    check_p.set_defaults(profile="dev")
    _add_common_flags(check_p, profile_default="dev")

    meta_p = sub.add_parser("metadata", help="Output build plan as JSON")
    meta_p.set_defaults(profile="dev")
    _add_common_flags(meta_p, profile_default="dev")

    cache_p = sub.add_parser("cache", help="Cache management")
    cache_sub = cache_p.add_subparsers(dest="cache_action", required=True)
    cache_sub.add_parser("clean", help="Remove cached test binaries and metadata")

    return parser


def _add_common_flags(parser: argparse.ArgumentParser, *, profile_default: str) -> None:
    """Add flags shared across exec-capable subcommands.

    *profile_default* only documents the subcommand's default profile in
    ``--help``; the default itself comes from ``set_defaults``.
    """
    parser.add_argument(
        "--profile",
        default=argparse.SUPPRESS,
        help=f"Build profile: dev, release, or user-defined (default: {profile_default})",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=_jobs_arg,
        default=None,
        help='Maximum concurrent compilations: N, "num-cpus", or -K for all CPUs but K',
    )
    parser.add_argument(
        "--compile-threads",
        type=_count_arg("--compile-threads"),
        default=None,
        metavar="N",
        help="Threads each mojo build may use (--num-threads; default from the profile: dev 1, release 8)",
    )
    parser.add_argument("--timeout", type=_count_arg("--timeout"), default=None, help="Per-target timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Show planned commands without executing")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Expand grouped output (e.g. in --dry-run) and show the resolved parallelism",
    )
    parser.add_argument("--no-config", action="store_true", default=False, help="Disable settings file discovery")
    parser.add_argument("--config-file", default=None, help="Explicit config file path")
    parser.add_argument(
        "-D", "--define", action="append", default=[], dest="defines", help="Define a compile-time variable (KEY=VALUE)"
    )
    parser.add_argument(
        "--flag",
        action="append",
        default=[],
        dest="flags",
        help="Extra flag passed through to the compiler (use --flag=VALUE for dash-prefixed flags)",
    )


def _jobs_arg(text: str) -> int | str:
    """``type=`` for ``-j``: a jobs spec, so ``0`` and junk exit 2 before any work starts."""
    try:
        return parse_jobs(text, "--jobs")
    except ConfigError as e:
        raise argparse.ArgumentTypeError(e.message) from None


def _count_arg(flag: str) -> Callable[[str], int]:
    """``type=`` for a flag that takes an integer >= 1; anything else exits 2."""

    def parse(text: str) -> int:
        try:
            return parse_count_text(text, flag)
        except ConfigError as e:
            raise argparse.ArgumentTypeError(e.message) from None

    return parse


def _add_test_flags(parser: argparse.ArgumentParser) -> None:
    """Add flags specific to the test subcommand."""
    parser.add_argument(
        "--output-format",
        default=None,
        choices=["human", "json"],
        help="Output format: human (default) or json (NDJSON to stdout)",
    )
    parser.add_argument(
        "--fail-fast",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stop after first failure (default: --fail-fast)",
    )
    parser.add_argument(
        "--success-output",
        default=None,
        choices=["immediate", "final", "never"],
        help="When to show passing test output (default: never)",
    )
    parser.add_argument(
        "--failure-output",
        default=None,
        choices=["immediate", "final", "never"],
        help="When to show failing test output (default: immediate)",
    )
    _add_no_cache_flag(parser)
    parser.add_argument(
        "--bundle",
        action="store_true",
        default=False,
        help="Compile all tests into a single binary",
    )
    parser.add_argument(
        "-k",
        "--filter",
        default=None,
        dest="filter",
        help="Filter tests by name pattern (case-insensitive substring)",
    )
    parser.add_argument(
        "--examples",
        action="store_true",
        default=False,
        help="Also compile-check examples/*/main.mojo (implied by an examples/ path)",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[],
        help="Filter tests by path (file or directory prefix)",
    )


def _add_no_cache_flag(parser: argparse.ArgumentParser) -> None:
    """Add ``--no-cache``, shared by the subcommands that build-then-execute."""
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="Disable the compiled-binary cache (always rebuild)",
    )


def _build_cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Extract CLI override dict from parsed arguments."""
    overrides: dict[str, Any] = {}
    if getattr(args, "jobs", None) is not None:
        overrides["jobs"] = args.jobs
    if getattr(args, "compile_threads", None) is not None:
        overrides["compile_threads"] = args.compile_threads
    if getattr(args, "timeout", None) is not None:
        overrides["timeout"] = args.timeout
    if getattr(args, "defines", None):
        defines = {}
        for d in args.defines:
            if "=" in d:
                k, v = d.split("=", 1)
                defines[k] = v
            else:
                defines[d] = ""
        overrides["defines"] = defines
    if getattr(args, "flags", None):
        overrides["flags"] = tuple(args.flags)
    return overrides


def determine_exit_code(outcomes: tuple[Outcome, ...]) -> int:
    """Determine process exit code from command outcomes.

    Returns 0 (success), 1 (test failure), or 2 (compilation failure).
    SKIPPED outcomes are ignored. Test failures (RUN_TEST and BUILD_TEST)
    take precedence over compilation failures.
    """
    from .types import OutcomeKind

    has_test_failure = any(
        o.kind not in (OutcomeKind.PASS, OutcomeKind.SKIPPED) and o.command.kind in _TEST_KINDS for o in outcomes
    )
    if has_test_failure:
        return 1

    has_compile_failure = any(
        o.kind not in (OutcomeKind.PASS, OutcomeKind.SKIPPED)
        and o.command.kind
        in (
            CommandKind.COMPILE_PACKAGE,
            CommandKind.COMPILE_BINARY,
            CommandKind.CHECK_EXAMPLE,
        )
        for o in outcomes
    )
    if has_compile_failure:
        return 2

    return 0


def apply_filters(
    commands: tuple[Command, ...],
    *,
    paths: tuple[str, ...],
    pattern: str | None,
    project_root: Path,
) -> tuple[Command, ...]:
    """Filter commands by path prefixes and/or name pattern.

    Only test commands (RUN_TEST and BUILD_TEST) are filtered; compile
    commands pass through. Path arguments are resolved against cwd,
    relativized against project_root, and normalized.
    """
    if not paths and pattern is None:
        return commands

    normalized_paths = _relativize_paths(paths, project_root)

    def _matches_test(cmd: Command) -> bool:
        if cmd.kind not in _TEST_KINDS:
            return True

        tid = cmd.target_id

        if paths and normalized_paths:
            path_match = any(
                tid == np if np.endswith(".mojo") else (tid.startswith(np + "/") or tid == np or np == ".")
                for np in normalized_paths
            )
            if not path_match:
                return False
        elif paths and not normalized_paths:
            return False

        return not (pattern is not None and pattern.lower() not in tid.lower())

    return tuple(cmd for cmd in commands if _matches_test(cmd))


def select_examples(
    commands: tuple[Command, ...],
    *,
    examples: bool,
    paths: tuple[str, ...],
    project_root: Path,
) -> tuple[Command, ...]:
    """Drop CHECK_EXAMPLE commands unless examples were asked for.

    Examples are opt-in for ``mojox test`` because compiling them can dwarf
    the test run itself. They are kept when *examples* is set or when any
    path in *paths* is the project's ``examples`` directory or inside it.
    Other command kinds always pass through.
    """
    wanted = examples or any(
        np == "examples" or np.startswith("examples/") for np in _relativize_paths(paths, project_root)
    )
    if wanted:
        return commands
    return tuple(cmd for cmd in commands if cmd.kind != CommandKind.CHECK_EXAMPLE)


def _relativize_paths(paths: tuple[str, ...], project_root: Path) -> list[str]:
    """Resolve *paths* against cwd and express them relative to *project_root*.

    Paths outside the root are silently dropped; a trailing separator is
    stripped so ``tests/unit/`` and ``tests/unit`` compare equal.
    """
    normalized: list[str] = []
    root = project_root.resolve()
    for p in paths:
        resolved = Path(p).resolve()
        try:
            rel = str(resolved.relative_to(root))
        except ValueError:
            continue
        normalized.append(os.path.normpath(rel).rstrip(os.sep))
    return normalized


def _jobs_detail(policy: Policy, host: HostFacts, jobs: int) -> str | None:
    """Explain how *jobs* came from a CPU-relative spec; None when *jobs* is a forced count (e.g. serial tests)."""
    from .output import describe_jobs

    if jobs != policy.jobs:
        return None
    return describe_jobs(policy.jobs_spec, host.cpu_count, host.cpu_count_source)


def _report_parallelism(policy: Policy, host: HostFacts, jobs: int) -> None:
    """Print the ``Using jobs J, compile threads T`` line (``-v`` on subcommands without a header)."""
    from .output import render_parallelism

    render_parallelism(jobs=jobs, compile_threads=policy.compile_threads, jobs_detail=_jobs_detail(policy, host, jobs))


def _interrupted_summary(commands: tuple[Command, ...]) -> str:
    """Build a short message for Ctrl+C interruption."""
    return f"Interrupted — {len(commands)} targets planned"


class _ToolchainUnavailable(SystemExit):
    """Exit 2 because no usable Mojo compiler is installed; the message is already printed."""


def _resolve_pipeline(
    args: argparse.Namespace,
) -> tuple[
    Manifest,
    TargetGraph,
    ResolvedEnv,
    Policy,
    Toolchain,
    HostFacts,
    LocalSettings,
    tuple[Command, ...],
    tuple[str, ...],
]:
    """Run the shared resolution pipeline, exiting 2 with the message on any error.

    The toolchain is resolved after every configuration step, so a missing
    compiler never hides a configuration error, and it exits with
    :class:`_ToolchainUnavailable`, which only ``mojox check`` catches.

    Returns (manifest, graph, env, policy, toolchain, host, settings, commands, include_paths).
    """
    from mojox_core import (
        discover,
        parse_manifest,
        plan,
        resolve,
    )
    from mojox_core.environment import build_env
    from mojox_core.io.environment import read_distributions, read_host_facts, read_lockfile
    from mojox_core.io.manifest import read as read_manifest
    from mojox_core.io.toolchain import resolve as resolve_toolchain

    from .settings_reader import read_settings

    root = Path.cwd()
    pyproject_path = root / "pyproject.toml"

    try:
        raw = read_manifest(pyproject_path)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    try:
        manifest = parse_manifest(raw)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    config_file = Path(args.config_file) if getattr(args, "config_file", None) else None
    no_config = getattr(args, "no_config", False)
    try:
        settings = read_settings(
            root,
            env=dict(os.environ),
            no_config=no_config,
            config_file=config_file,
        )
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    try:
        host = read_host_facts(root, environ=os.environ)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    try:
        graph = discover(manifest, root)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    cli_overrides = _build_cli_overrides(args)
    profile_name = manifest.build_profile if args.profile is None else args.profile
    try:
        policy = resolve(manifest, profile_name, cli_overrides, settings, cpu_count=host.cpu_count)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    try:
        toolchain = resolve_toolchain()
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        raise _ToolchainUnavailable(2) from None

    dists = read_distributions()
    lock_data = read_lockfile(root)
    try:
        env = build_env(
            dists,
            lock_data,
            mojo_path=toolchain.mojo_path,
            mojo_version=toolchain.version,
        )
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    commands = plan(graph, env, policy, toolchain, host)

    include_paths = tuple(dict.fromkeys(list(policy.include_paths) + [d.include_dir for d in env.include_sequence]))

    return manifest, graph, env, policy, toolchain, host, settings, commands, include_paths


def _make_cache_context(
    *,
    lib_paths: Iterable[str],
    test_roots: Iterable[str],
    implicit_dirs: Iterable[str],
    compiler_version: str,
    no_cache: bool,
) -> CacheContext:
    """Compute the per-invocation inputs of the AOT binary cache key.

    Paths are relative to the working directory, which is the project root.
    Lib and test trees are content-hashed; *implicit_dirs*, the dirs the
    compiler searches without an ``-I`` flag, are stat-stamped in order.
    Every ``-I`` dir is stamped per command from its argv instead (see
    :class:`~mojox.exec.CacheContext`), except the precompile output dir
    (``.mojox/build/pkg``): its packages are a function of the lib sources,
    the include dirs, the environment and the compiler, which this key
    covers too, and each is only replaced under its own precompile cache key.
    """
    import hashlib

    from .cache import hash_directory_tree, stamp_include_dirs
    from .exec import CacheContext

    root = Path.cwd()
    pkg_hashes = sorted(hash_directory_tree(root / p) for p in lib_paths)
    test_hashes = [hash_directory_tree(root / t) for t in test_roots]
    return CacheContext(
        project_hash=hashlib.sha256("".join(pkg_hashes).encode()).hexdigest(),
        tests_tree_hash=hashlib.sha256("".join(test_hashes).encode()).hexdigest(),
        deps_stamp=stamp_include_dirs(implicit_dirs),
        compiler_version=compiler_version,
        meta_dir=root / ".mojox" / "cache" / "meta",
        enabled=not no_cache,
        unstamped_dirs=frozenset({os.path.normpath(root / ".mojox" / "build" / "pkg")}),
    )


def _precompile_cache_context(compiler_version: str) -> CacheContext:
    """Cache context for subcommands that plan no test: only precompiles consult it.

    ``build`` and ``check`` rewrite the same ``.mojox/build/pkg`` packages as
    ``mojox test``, so they must go through the same entries; a plain
    precompile would replace a package behind the key that vouches for it.
    Precompile keys are computed per command, so the tree hashes are skipped.
    """
    return _make_cache_context(
        lib_paths=(), test_roots=(), implicit_dirs=(), compiler_version=compiler_version, no_cache=False
    )


def _cmd_test(args: argparse.Namespace) -> None:
    """Execute the test subcommand."""
    import time

    from .exec import run_commands
    from .output import (
        make_progress_callback,
        render_diagnostics,
        render_dry_run,
        render_final_output,
        render_starting,
        render_summary,
    )
    from .types import OutputFormat, OutputMode

    manifest, graph, env, policy, toolchain, host, settings, commands, include_paths = _resolve_pipeline(args)

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    # --- Output format (resolved early for zero-match JSON events) ---
    output_format = OutputFormat(args.output_format) if args.output_format else OutputFormat.HUMAN

    # --- Filtering ---
    filter_paths = tuple(getattr(args, "paths", []))
    filter_pattern = getattr(args, "filter", None)
    is_bundle = getattr(args, "bundle", False)
    commands = select_examples(
        commands,
        examples=getattr(args, "examples", False),
        paths=filter_paths,
        project_root=Path.cwd(),
    )
    # In bundle mode, -k filters at runtime inside the harness binary,
    # not at the file level. Path filters still apply to scope the bundle.
    effective_pattern = None if is_bundle else filter_pattern
    if filter_paths or effective_pattern is not None:
        commands = apply_filters(
            commands,
            paths=filter_paths,
            pattern=effective_pattern,
            project_root=Path.cwd(),
        )
        test_count = sum(1 for c in commands if c.kind in _TEST_KINDS)
        example_count = sum(1 for c in commands if c.kind == CommandKind.CHECK_EXAMPLE)
        if test_count == 0 and example_count == 0:
            print("No tests match the filter", file=sys.stderr)
            if output_format == OutputFormat.JSON:
                from .json import JsonEventWriter, serialize_suite_finished, serialize_suite_started

                writer = JsonEventWriter(sys.stdout)
                writer.write_event(
                    serialize_suite_started(0, jobs=policy.jobs_tests, compile_threads=policy.compile_threads)
                )
                writer.write_event(serialize_suite_finished((), elapsed_s=0.0))
            return
    fail_fast = getattr(args, "fail_fast", True)

    # --- Resolve verbosity ---
    if args.success_output is not None:
        success_output = OutputMode(args.success_output)
    elif args.verbose:
        success_output = OutputMode.IMMEDIATE
    else:
        success_output = OutputMode.NEVER

    if args.failure_output is not None:
        failure_output = OutputMode(args.failure_output)
    elif args.verbose:
        failure_output = OutputMode.IMMEDIATE
    else:
        failure_output = OutputMode.IMMEDIATE

    # --- Dry-run ---
    if args.dry_run:
        if output_format == OutputFormat.JSON:
            import json as json_mod

            dry_commands = [{"target_id": c.target_id, "kind": c.kind.value, "argv": list(c.argv)} for c in commands]
            json_mod.dump({"type": "dry-run", "commands": dry_commands}, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            render_dry_run(commands, compact=not args.verbose)
        return

    # --- Cache context ---
    from mojox_core import TargetKind

    no_cache = getattr(args, "no_cache", False)
    cache_ctx = _make_cache_context(
        lib_paths=[t.path for t in graph.targets if t.kind == TargetKind.LIB],
        test_roots=manifest.test_roots,
        implicit_dirs=(),
        compiler_version=toolchain.version,
        no_cache=no_cache,
    )

    # --- Bundle mode ---
    if is_bundle:
        _run_bundle_test(
            graph=graph,
            env=env,
            policy=policy,
            toolchain=toolchain,
            host=host,
            settings=settings,
            commands=commands,
            include_paths=include_paths,
            cache_ctx=cache_ctx,
            output_format=output_format,
            filter_pattern=filter_pattern,
            fail_fast=fail_fast,
            success_output=success_output,
            failure_output=failure_output,
        )
        return

    # --- Build callbacks ---
    on_start = None
    on_complete = None

    if output_format == OutputFormat.JSON:
        from .json import JsonEventWriter, make_json_callbacks, serialize_suite_finished, serialize_suite_started

        test_count = sum(1 for c in commands if c.kind in _TEST_KINDS)
        writer = JsonEventWriter(sys.stdout)
        writer.write_event(
            serialize_suite_started(test_count, jobs=policy.jobs_tests, compile_threads=policy.compile_threads)
        )

        json_on_start, json_on_complete = make_json_callbacks(writer)
        on_start = json_on_start
        on_complete = json_on_complete
    else:
        render_starting(
            len(commands),
            jobs=policy.jobs_tests,
            compile_threads=policy.compile_threads,
            jobs_detail=_jobs_detail(policy, host, policy.jobs_tests),
        )
        on_complete = make_progress_callback(
            success_output=success_output,
            failure_output=failure_output,
        )

    # --- Execute ---
    wall_start = time.monotonic()
    try:
        outcomes = run_commands(
            commands,
            max_workers=policy.jobs_tests,
            extra_env=settings.env if settings.env else None,
            include_paths=include_paths,
            on_start=on_start,
            on_complete=on_complete,
            fail_fast=fail_fast,
            cache_context=cache_ctx,
        )
    except KeyboardInterrupt:
        print(f"\n{_interrupted_summary(commands)}", file=sys.stderr)
        sys.exit(130)
    wall_elapsed = time.monotonic() - wall_start

    # --- Render results ---
    if output_format == OutputFormat.JSON:
        writer.write_event(serialize_suite_finished(outcomes, elapsed_s=wall_elapsed))
    else:
        render_final_output(
            outcomes,
            success_output=success_output,
            failure_output=failure_output,
        )
        render_summary(outcomes)

    sys.exit(determine_exit_code(outcomes))


def _run_bundle_test(
    *,
    graph: TargetGraph,
    env: ResolvedEnv,
    policy: Policy,
    toolchain: Toolchain,
    host: HostFacts,
    settings: LocalSettings,
    commands: tuple[Command, ...],
    include_paths: tuple[str, ...],
    cache_ctx: CacheContext,
    output_format: OutputFormat,
    filter_pattern: str | None,
    fail_fast: bool,
    success_output: OutputMode,
    failure_output: OutputMode,
) -> None:
    """Execute tests in bundle mode: single binary, all test functions.

    Discovers ``def test_*`` functions from test targets, generates a
    Mojo harness with aliased imports, creates a staging area with
    ``__init__.mojo`` stubs, and compiles+executes the harness as a
    single ``BUILD_TEST`` command through the existing cache pipeline.

    Pattern A test files (no ``test_*`` functions) are excluded with
    a diagnostic printed to stderr.

    Bundle mode adds two kinds of include dirs ahead of the dependency
    dirs. The parents of lib targets expose more than the lib packages
    ``project_hash`` covers (sibling modules such as ``src/util.mojo``,
    non-target packages); being on the harness's ``-I``, they are stamped
    with the others. The staging tree is not stamped: it is regenerated
    each run from the test files ``tests_tree_hash`` covers.
    """
    import time
    from dataclasses import replace

    from mojox_core import TargetKind
    from mojox_core import plan as plan_fn
    from mojox_core.bundle import (
        TestModule,
        discover_test_functions,
        file_path_to_module_path,
        generate_harness,
    )
    from mojox_core.types import Target
    from mojox_core.types import TargetGraph as TG

    from .exec import run_commands
    from .output import (
        make_progress_callback,
        render_final_output,
        render_starting,
        render_summary,
    )
    from .staging import cleanup_staging, create_bundle_staging
    from .types import OutputFormat

    root = Path.cwd()

    # --- Discover test functions from target files ---
    test_cmds = [c for c in commands if c.kind in _TEST_KINDS]
    bundle_modules: list[TestModule] = []
    diagnostics: list[str] = []

    for cmd in test_cmds:
        source_path = root / cmd.target_id
        try:
            source_content = source_path.read_text()
        except OSError:
            diagnostics.append(f"cannot read {cmd.target_id}, excluding from bundle")
            continue

        funcs = discover_test_functions(source_content)
        if not funcs:
            diagnostics.append(f"{cmd.target_id}: no test_* functions found, excluding from bundle")
            continue

        module_path = file_path_to_module_path(cmd.target_id)
        bundle_modules.append(TestModule(module_path, cmd.target_id, funcs))

    if not bundle_modules:
        print("No test functions found for bundling", file=sys.stderr)
        return

    for diag in diagnostics:
        print(f"bundle: {diag}", file=sys.stderr)

    # --- Generate harness and staging area ---
    harness_source = generate_harness(bundle_modules)
    test_file_paths = [m.file_path for m in bundle_modules]
    staging_result = create_bundle_staging(root, test_file_paths, harness_source)

    try:
        harness_rel = str(staging_result.harness_path.relative_to(root))

        # Build a graph with lib targets + one bundle test target
        lib_targets = tuple(t for t in graph.targets if t.kind == TargetKind.LIB)
        bundle_target = Target(TargetKind.TEST, harness_rel, "bundle::harness")
        bundle_graph = TG(
            targets=lib_targets + (bundle_target,),
            edges=(),
        )

        # Add lib source parent dirs so Mojo finds project packages
        # even when precompilation is skipped (bundle has 1 test target,
        # below _PRECOMPILE_THRESHOLD).
        source_dirs: list[str] = []
        for lt in lib_targets:
            pkg_path = root / lt.path
            if pkg_path.is_dir():
                parent = str(pkg_path.parent)
                if parent not in source_dirs:
                    source_dirs.append(parent)

        # Disable --Werror for the generated harness: try/except around
        # non-raising functions triggers "unreachable except" warnings.
        from mojox_core.types import LintConfig as LintConfigT

        bundle_lints = LintConfigT(
            warnings_as_errors=False,
            check_doc_strings=policy.lints.check_doc_strings,
            missing_doc_strings=policy.lints.missing_doc_strings,
            unstable_apis=policy.lints.unstable_apis,
        )

        bundle_policy = replace(
            policy,
            include_paths=(staging_result.include_path, *source_dirs, *policy.include_paths),
            lints=bundle_lints,
        )

        bundle_commands = plan_fn(bundle_graph, env, bundle_policy, toolchain, host)

        # Runtime -k filter passed as argv to the binary
        runtime_args: tuple[str, ...] = ()
        if filter_pattern:
            runtime_args = (filter_pattern,)

        bundle_include = (staging_result.include_path, *include_paths)

        cache_ctx = replace(
            cache_ctx,
            runtime_args=runtime_args,
            unstamped_dirs=cache_ctx.unstamped_dirs | {os.path.normpath(staging_result.include_path)},
        )

        # --- Callbacks ---
        on_start = None
        on_complete = None

        if output_format == OutputFormat.JSON:
            from .json import (
                JsonEventWriter,
                make_json_callbacks,
                serialize_suite_finished,
                serialize_suite_started,
            )

            writer = JsonEventWriter(sys.stdout)
            test_count = sum(1 for c in bundle_commands if c.kind in _TEST_KINDS)
            writer.write_event(serialize_suite_started(test_count, jobs=1, compile_threads=policy.compile_threads))
            json_on_start, json_on_complete = make_json_callbacks(writer)
            on_start = json_on_start
            on_complete = json_on_complete
        else:
            render_starting(
                len(bundle_commands),
                jobs=1,
                compile_threads=policy.compile_threads,
                jobs_detail=_jobs_detail(policy, host, 1),
            )
            on_complete = make_progress_callback(
                success_output=success_output,
                failure_output=failure_output,
            )

        # --- Execute ---
        wall_start = time.monotonic()
        try:
            outcomes = run_commands(
                bundle_commands,
                max_workers=1,
                extra_env=settings.env if settings.env else None,
                include_paths=bundle_include,
                on_start=on_start,
                on_complete=on_complete,
                fail_fast=fail_fast,
                cache_context=cache_ctx,
            )
        except KeyboardInterrupt:
            print(f"\n{_interrupted_summary(bundle_commands)}", file=sys.stderr)
            sys.exit(130)
        wall_elapsed = time.monotonic() - wall_start

        # --- Render ---
        if output_format == OutputFormat.JSON:
            writer.write_event(serialize_suite_finished(outcomes, elapsed_s=wall_elapsed))
        else:
            render_final_output(
                outcomes,
                success_output=success_output,
                failure_output=failure_output,
            )
            render_summary(outcomes)

        sys.exit(determine_exit_code(outcomes))

    finally:
        cleanup_staging(staging_result.staging_dir)


def _cmd_run(args: argparse.Namespace) -> None:
    """Build the file to a cached binary, run it, and exit with its exit code.

    Takes the ``mojox test`` build-then-execute path, so an unchanged file
    and unchanged imports reuse the binary. The program's captured stdout
    and stderr (build diagnostics first) are replayed once it exits. A
    build failure or a timeout exits 1, even when the compiler itself
    exited 0; a signal death exits 128 + the signal number, as a shell does.
    """
    from dataclasses import replace

    from mojox_core import (
        parse_manifest,
        plan,
        resolve,
    )
    from mojox_core.environment import build_env
    from mojox_core.io.environment import read_distributions, read_host_facts, read_lockfile
    from mojox_core.io.manifest import read as read_manifest
    from mojox_core.io.toolchain import resolve as resolve_toolchain
    from mojox_core.policy import resolve_without_manifest
    from mojox_core.types import Target, TargetGraph, TargetKind

    from .exec import run_commands
    from .output import render_diagnostics
    from .settings_reader import read_settings
    from .types import OutcomeKind

    root = Path.cwd()

    try:
        raw = read_manifest(root / "pyproject.toml")
        manifest = parse_manifest(raw)
    except Exception:
        manifest = None

    config_file = Path(args.config_file) if getattr(args, "config_file", None) else None
    no_config = getattr(args, "no_config", False)
    try:
        settings = read_settings(root, env=dict(os.environ), no_config=no_config, config_file=config_file)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    try:
        toolchain = resolve_toolchain()
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    dists = read_distributions()
    lock_data = read_lockfile(root)
    try:
        host = read_host_facts(root, environ=os.environ)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    env = build_env(dists, lock_data, mojo_path=toolchain.mojo_path, mojo_version=toolchain.version)

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    cli_overrides = _build_cli_overrides(args)

    try:
        if manifest is not None:
            policy = resolve(manifest, args.profile, cli_overrides, settings, cpu_count=host.cpu_count)
        else:
            policy = resolve_without_manifest(cli_overrides, settings, cpu_count=host.cpu_count)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    # Add project source package parents to include paths so mojo
    # finds them without precompilation.
    if manifest is not None and manifest.packages:
        source_dirs = []
        for pkg in manifest.packages:
            pkg_path = root / pkg
            if pkg_path.is_dir():
                source_dirs.append(str(pkg_path.parent))
        extra_includes = tuple(dict.fromkeys(source_dirs + list(policy.include_paths)))
        policy = replace(policy, include_paths=extra_includes)

    if args.verbose:
        _report_parallelism(policy, host, 1)  # the lone program builds and runs on one worker

    graph = TargetGraph(
        targets=(Target(TargetKind.TEST, args.file, args.file),),
        edges=(),
    )
    commands = plan(graph, env, policy, toolchain, host)

    if args.dry_run:
        from .output import render_dry_run

        render_dry_run(commands, compact=not args.verbose)
        return

    include_paths = tuple(dict.fromkeys(list(policy.include_paths) + [d.include_dir for d in env.include_sequence]))

    # The compiler resolves imports from the file's own directory too, so
    # sibling modules are stamped along with the -I dirs.
    cache_ctx = _make_cache_context(
        lib_paths=(manifest.packages or ()) if manifest is not None else (),
        test_roots=(),
        implicit_dirs=(str((root / args.file).parent),),
        compiler_version=toolchain.version,
        no_cache=args.no_cache,
    )
    outcomes = run_commands(
        commands,
        extra_env=settings.env if settings.env else None,
        include_paths=include_paths,
        cache_context=cache_ctx,
    )
    for outcome in outcomes:
        print(outcome.stdout, end="")
        if outcome.stderr:
            print(outcome.stderr, end="", file=sys.stderr)
    # The program is planned last: anything before it is a prerequisite.
    final = outcomes[-1]
    if final.kind == OutcomeKind.PASS:
        sys.exit(0)
    if final.kind == OutcomeKind.COMPILE_ERROR:
        # Even a compiler killed by a signal is a build failure, not the
        # program's death: its negative returncode must not reach sys.exit.
        sys.exit(1)
    if final.kind == OutcomeKind.TIMEOUT:
        print(f"mojox: program timed out after {final.command.timeout_s}s", file=sys.stderr)
        sys.exit(1)
    if final.kind == OutcomeKind.CRASH and final.exit_code is not None:
        # A signal death is a negative returncode; report it as a shell does.
        sys.exit(128 - final.exit_code)
    sys.exit(final.exit_code or 1)


def _cmd_build(args: argparse.Namespace) -> None:
    """Execute the build subcommand."""
    from .exec import run_commands
    from .output import (
        make_progress_callback,
        render_diagnostics,
        render_dry_run,
        render_starting,
        render_summary,
    )
    from .types import OutputMode

    _manifest, _graph, env, policy, toolchain, host, settings, commands, include_paths = _resolve_pipeline(args)

    build_commands = tuple(c for c in commands if c.kind in (CommandKind.COMPILE_PACKAGE, CommandKind.COMPILE_BINARY))

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    if args.dry_run:
        if args.verbose:
            _report_parallelism(policy, host, policy.jobs_compile)
        render_dry_run(build_commands, compact=not args.verbose)
        return

    render_starting(
        len(build_commands),
        jobs=policy.jobs_compile,
        compile_threads=policy.compile_threads,
        jobs_detail=_jobs_detail(policy, host, policy.jobs_compile),
    )
    try:
        outcomes = run_commands(
            build_commands,
            max_workers=policy.jobs_compile,
            extra_env=settings.env if settings.env else None,
            include_paths=include_paths,
            on_complete=make_progress_callback(
                success_output=OutputMode.IMMEDIATE if args.verbose else OutputMode.NEVER,
                failure_output=OutputMode.IMMEDIATE,
            ),
            cache_context=_precompile_cache_context(toolchain.version),
        )
    except KeyboardInterrupt:
        print(f"\n{_interrupted_summary(build_commands)}", file=sys.stderr)
        sys.exit(130)
    render_summary(outcomes)

    from .types import OutcomeKind

    if any(o.kind != OutcomeKind.PASS for o in outcomes):
        sys.exit(1)


def _cmd_check(args: argparse.Namespace) -> None:
    """Execute the check subcommand: compile packages + run lints."""
    from mojox_core import parse_manifest
    from mojox_core.io.manifest import read as read_manifest

    from .lints import lint_bare_assert, lint_path_source
    from .output import _BOLD, _DIM, _GREEN, _RED, _c

    root = Path.cwd()
    pyproject_path = root / "pyproject.toml"

    # --- Manifest validation ---
    try:
        raw = read_manifest(pyproject_path)
        manifest = parse_manifest(raw)
        print(_c(sys.stderr, _DIM, f"manifest: {manifest.name} {manifest.version}"), file=sys.stderr)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    # --- Pre-flight text lints ---
    findings = []
    findings.extend(lint_path_source(pyproject_path))

    files_checked = 0
    for test_root in manifest.test_roots:
        tr_path = root / test_root
        if tr_path.is_dir():
            for mojo_file in tr_path.rglob("test_*.mojo"):
                files_checked += 1
                findings.extend(lint_bare_assert(mojo_file))

    if findings:
        _render_lint_findings(findings, root, sys.stderr)

    # --- Compiler check: resolve pipeline and precompile ---
    try:
        _manifest, _graph, env, policy, toolchain, host, settings, commands, include_paths = _resolve_pipeline(args)
    except _ToolchainUnavailable:
        print(_c(sys.stderr, _DIM, "compiler not available, skipping compilation check"), file=sys.stderr)
        if findings:
            print(_c(sys.stderr, _BOLD, f"check: {len(findings)} warning(s)"), file=sys.stderr)
        else:
            print(_c(sys.stderr, _GREEN, "check: OK (manifest only)"), file=sys.stderr)
        return

    from .output import render_diagnostics, render_dry_run

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    if args.verbose:
        _report_parallelism(policy, host, policy.jobs_compile)

    compile_commands = tuple(c for c in commands if c.kind == CommandKind.COMPILE_PACKAGE)

    if not compile_commands:
        print(_c(sys.stderr, _DIM, "no packages to compile"), file=sys.stderr)
        if findings:
            print(_c(sys.stderr, _BOLD, f"check: {len(findings)} warning(s)"), file=sys.stderr)
        else:
            print(_c(sys.stderr, _GREEN, "check: OK"), file=sys.stderr)
        return

    if args.dry_run:
        render_dry_run(compile_commands, compact=not args.verbose)
        return

    from .exec import run_commands

    try:
        outcomes = run_commands(
            compile_commands,
            max_workers=policy.jobs_compile,
            extra_env=settings.env if settings.env else None,
            include_paths=include_paths,
            cache_context=_precompile_cache_context(toolchain.version),
        )
    except KeyboardInterrupt:
        print(f"\n{_interrupted_summary(compile_commands)}", file=sys.stderr)
        sys.exit(130)

    from .types import OutcomeKind

    for o in outcomes:
        if o.kind != OutcomeKind.PASS and o.stderr:
            for line in o.stderr.splitlines():
                print(f"  {_c(sys.stderr, _DIM, line)}", file=sys.stderr)

    compile_ok = all(o.kind == OutcomeKind.PASS for o in outcomes)
    parts: list[str] = []
    if compile_ok:
        parts.append(_c(sys.stderr, _GREEN, "compiled"))
    else:
        parts.append(_c(sys.stderr, _RED, "compile failed"))
    if findings:
        parts.append(f"{len(findings)} warning(s)")
    print(_c(sys.stderr, _BOLD, f"check: {', '.join(parts)}"), file=sys.stderr)

    if not compile_ok:
        sys.exit(1)


def _render_lint_findings(
    findings: list[LintFinding],
    root: Path,
    out: IO[str],
) -> None:
    """Render lint findings, deduplicating repeated messages per file."""
    from .output import _YELLOW, _c

    groups: dict[tuple[str, str], list[int]] = {}
    for f in findings:
        try:
            rel = os.path.relpath(f.file, root)
        except ValueError:
            rel = f.file
        key = (rel, f.message)
        groups.setdefault(key, []).append(f.line)

    for (rel_file, message), lines in groups.items():
        prefix = _c(out, _YELLOW, "lint:")
        if len(lines) == 1 and lines[0]:
            out.write(f"{prefix} {rel_file}:{lines[0]}: {message}\n")
        elif len(lines) > 1:
            valid = [ln for ln in lines if ln]
            shown = ", ".join(str(ln) for ln in valid[:5])
            suffix = f", ... +{len(valid) - 5}" if len(valid) > 5 else ""
            out.write(f"{prefix} {rel_file}: {message} ({len(valid)} occurrences: lines {shown}{suffix})\n")
        else:
            out.write(f"{prefix} {rel_file}: {message}\n")


def _cmd_metadata(args: argparse.Namespace) -> None:
    """Execute the metadata subcommand: output build plan as JSON."""
    from mojox_core import serialize

    from .output import render_diagnostics

    _manifest, graph, env, policy, toolchain, host, _settings, commands, _include_paths = _resolve_pipeline(args)

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    doc = serialize(graph, env, policy, commands, toolchain, env.diagnostics, host=host)
    json.dump(doc, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


def _cmd_cache_clean(args: argparse.Namespace) -> None:
    """Remove all cached test binaries and metadata.

    Deletes the ``.mojox/cache`` directory tree under the current
    working directory.  Safe to call when no cache exists.

    Args:
        args: Parsed CLI arguments (unused but required by the
            subcommand dispatch signature).
    """
    import shutil

    cache_dir = Path.cwd() / ".mojox" / "cache"
    if cache_dir.is_dir():
        shutil.rmtree(cache_dir)
        print(f"Removed {cache_dir}")
    else:
        print("Nothing to clean")


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "check":
        _cmd_check(args)
    elif args.subcommand == "metadata":
        _cmd_metadata(args)
    elif args.subcommand == "test":
        _cmd_test(args)
    elif args.subcommand == "run":
        _cmd_run(args)
    elif args.subcommand == "build":
        _cmd_build(args)
    elif args.subcommand == "cache":
        _cmd_cache_clean(args)


if __name__ == "__main__":
    main()
