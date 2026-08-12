"""mojox CLI: build, test, and run Mojo projects.

Subcommands:
  test      Run test targets (dev profile by default)
  run       Run a single Mojo file (dev profile by default)
  build     Compile binary targets (release profile by default)
  check     Validate manifest and run lints (no compiler needed)
  metadata  Output the build plan as JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="mojox",
        description="Build, test, and run Mojo projects.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    test_p = sub.add_parser("test", help="Run test targets")
    test_p.set_defaults(profile="dev")
    _add_common_flags(test_p)

    run_p = sub.add_parser("run", help="Run a Mojo file")
    run_p.add_argument("file", help="The .mojo file to run")
    run_p.set_defaults(profile="dev")
    _add_common_flags(run_p)

    build_p = sub.add_parser("build", help="Compile binary targets")
    build_p.set_defaults(profile="release")
    _add_common_flags(build_p)

    check_p = sub.add_parser("check", help="Check project: compile packages and run lints")
    check_p.set_defaults(profile="dev")
    _add_common_flags(check_p)

    meta_p = sub.add_parser("metadata", help="Output build plan as JSON")
    meta_p.set_defaults(profile="dev")
    _add_common_flags(meta_p)

    return parser


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Add flags shared across exec-capable subcommands."""
    parser.add_argument("--profile", default=argparse.SUPPRESS,
                        help="Build profile (dev, release, or user-defined)")
    parser.add_argument("--jobs", "-j", type=int, default=None,
                        help="Maximum concurrent compilations")
    parser.add_argument("--timeout", type=int, default=None,
                        help="Per-target timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Show planned commands without executing")
    parser.add_argument("--verbose", "-v", action="store_true", default=False,
                        help="Expand grouped output (e.g. in --dry-run)")
    parser.add_argument("--no-config", action="store_true", default=False,
                        help="Disable settings file discovery")
    parser.add_argument("--config-file", default=None,
                        help="Explicit config file path")
    parser.add_argument("-D", "--define", action="append", default=[], dest="defines",
                        help="Define a compile-time variable (KEY=VALUE)")
    parser.add_argument("--flag", action="append", default=[], dest="flags",
                        help="Extra flag passed through to the compiler"
                             " (use --flag=VALUE for dash-prefixed flags)")


def _build_cli_overrides(args: argparse.Namespace) -> dict:
    """Extract CLI override dict from parsed arguments."""
    overrides: dict = {}
    if getattr(args, "jobs", None) is not None:
        overrides["jobs"] = args.jobs
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


def _interrupted_summary(commands) -> str:
    """Build a short message for Ctrl+C interruption."""
    return f"Interrupted — {len(commands)} targets planned"


def _resolve_pipeline(args: argparse.Namespace):
    """Run the shared resolution pipeline.

    Returns (manifest, graph, env, policy, toolchain, host, settings, commands, include_paths).
    """
    from mojox_core import (
        ConfigError, discover, plan, resolve, parse_manifest,
    )
    from mojox_core.io.environment import read_distributions, read_host_facts, read_lockfile
    from mojox_core.io.manifest import read as read_manifest
    from mojox_core.io.toolchain import resolve as resolve_toolchain
    from mojox_core.environment import build_env

    from ._settings_reader import read_settings

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
    settings = read_settings(
        root, env=dict(os.environ),
        no_config=no_config,
        config_file=config_file,
    )

    try:
        toolchain = resolve_toolchain()
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    dists = read_distributions()
    lock_data = read_lockfile(root)
    host = read_host_facts(root)

    try:
        env = build_env(
            dists, lock_data,
            mojo_path=toolchain.mojo_path,
            mojo_version=toolchain.version,
        )
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    try:
        graph = discover(manifest, root)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    cli_overrides = _build_cli_overrides(args)
    profile_name = args.profile
    try:
        policy = resolve(manifest, profile_name, cli_overrides, settings)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    commands = plan(graph, env, policy, toolchain, host)

    include_paths = tuple(
        dict.fromkeys(
            list(policy.include_paths)
            + [d.include_dir for d in env.include_sequence]
        )
    )

    return manifest, graph, env, policy, toolchain, host, settings, commands, include_paths


def _cmd_test(args: argparse.Namespace) -> None:
    """Execute the test subcommand."""
    from ._exec import run_commands
    from ._output import (
        render_dry_run, render_summary, render_diagnostics,
        render_starting, make_progress_callback,
    )

    manifest, graph, env, policy, toolchain, host, settings, commands, include_paths = (
        _resolve_pipeline(args)
    )

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    if args.dry_run:
        render_dry_run(commands, compact=not args.verbose)
        return

    render_starting(len(commands))
    try:
        outcomes = run_commands(
            commands,
            max_workers=policy.jobs_tests,
            extra_env=settings.env if settings.env else None,
            include_paths=include_paths,
            on_complete=make_progress_callback(verbose=args.verbose),
        )
    except KeyboardInterrupt:
        print(f"\n{_interrupted_summary(commands)}", file=sys.stderr)
        sys.exit(130)
    render_summary(outcomes)

    from ._types import OutcomeKind
    if any(o.kind != OutcomeKind.PASS for o in outcomes):
        sys.exit(1)


def _cmd_run(args: argparse.Namespace) -> None:
    """Execute the run subcommand for a single file."""
    from mojox_core import (
        ConfigError, plan, resolve, parse_manifest,
    )
    from mojox_core._types import LintConfig, Policy, Target, TargetGraph, TargetKind
    from mojox_core.io.environment import read_distributions, read_host_facts, read_lockfile
    from mojox_core.io.manifest import read as read_manifest
    from mojox_core.io.toolchain import resolve as resolve_toolchain
    from mojox_core.environment import build_env

    from ._exec import run_command
    from ._output import render_diagnostics
    from ._settings_reader import read_settings

    root = Path.cwd()

    try:
        raw = read_manifest(root / "pyproject.toml")
        manifest = parse_manifest(raw)
    except Exception:
        manifest = None

    config_file = Path(args.config_file) if getattr(args, "config_file", None) else None
    no_config = getattr(args, "no_config", False)
    settings = read_settings(root, env=dict(os.environ), no_config=no_config, config_file=config_file)

    try:
        toolchain = resolve_toolchain()
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    dists = read_distributions()
    lock_data = read_lockfile(root)
    host = read_host_facts(root)
    env = build_env(dists, lock_data, mojo_path=toolchain.mojo_path, mojo_version=toolchain.version)

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    cli_overrides = _build_cli_overrides(args)

    if manifest is not None:
        policy = resolve(manifest, args.profile, cli_overrides, settings)
    else:
        from mojox_core.policy import BUILTIN_DEV
        jobs = cli_overrides.get("jobs") or settings.jobs or 1
        timeout = cli_overrides.get("timeout") or settings.timeout_s or 300
        policy = Policy(
            optimize=cli_overrides.get("optimize", BUILTIN_DEV.optimize),
            debug_level=BUILTIN_DEV.debug_level or "line-tables",
            defines=cli_overrides.get("defines", dict(BUILTIN_DEV.defines)),
            flags=tuple(cli_overrides.get("flags", ())),
            include_paths=(),
            lints=LintConfig(),
            jobs=jobs, jobs_compile=jobs, jobs_tests=jobs,
            timeout_s=timeout,
        )

    # Add project source package parents to include paths so mojo
    # finds them without precompilation.
    if manifest is not None and manifest.packages:
        source_dirs = []
        for pkg in manifest.packages:
            pkg_path = root / pkg
            if pkg_path.is_dir():
                source_dirs.append(str(pkg_path.parent))
        extra_includes = tuple(dict.fromkeys(source_dirs + list(policy.include_paths)))
        policy = Policy(
            optimize=policy.optimize,
            debug_level=policy.debug_level,
            defines=policy.defines,
            flags=policy.flags,
            include_paths=extra_includes,
            lints=policy.lints,
            jobs=policy.jobs,
            jobs_compile=policy.jobs_compile,
            jobs_tests=policy.jobs_tests,
            timeout_s=policy.timeout_s,
        )

    graph = TargetGraph(
        targets=(Target(TargetKind.TEST, args.file, args.file),),
        edges=(),
    )
    commands = plan(graph, env, policy, toolchain, host)

    if args.dry_run:
        from ._output import render_dry_run
        render_dry_run(commands, compact=not args.verbose)
        return

    include_paths = tuple(
        dict.fromkeys(
            list(policy.include_paths)
            + [d.include_dir for d in env.include_sequence]
        )
    )

    outcome = run_command(
        commands[0],
        extra_env=settings.env if settings.env else None,
        include_paths=include_paths,
    )
    print(outcome.stdout, end="")
    if outcome.stderr:
        print(outcome.stderr, end="", file=sys.stderr)
    sys.exit(outcome.exit_code if outcome.exit_code is not None else 1)


def _cmd_build(args: argparse.Namespace) -> None:
    """Execute the build subcommand."""
    from ._exec import run_commands
    from ._output import (
        render_dry_run, render_summary, render_diagnostics,
        render_starting, make_progress_callback,
    )
    from mojox_core import CommandKind

    manifest, graph, env, policy, toolchain, host, settings, commands, include_paths = (
        _resolve_pipeline(args)
    )

    build_commands = tuple(
        c for c in commands
        if c.kind in (CommandKind.COMPILE_PACKAGE, CommandKind.COMPILE_BINARY)
    )

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    if args.dry_run:
        render_dry_run(build_commands, compact=not args.verbose)
        return

    render_starting(len(build_commands))
    try:
        outcomes = run_commands(
            build_commands,
            max_workers=policy.jobs_compile,
            extra_env=settings.env if settings.env else None,
            include_paths=include_paths,
            on_complete=make_progress_callback(verbose=args.verbose),
        )
    except KeyboardInterrupt:
        print(f"\n{_interrupted_summary(build_commands)}", file=sys.stderr)
        sys.exit(130)
    render_summary(outcomes)

    from ._types import OutcomeKind
    if any(o.kind != OutcomeKind.PASS for o in outcomes):
        sys.exit(1)


def _cmd_check(args: argparse.Namespace) -> None:
    """Execute the check subcommand: compile packages + run lints."""
    from mojox_core import ConfigError, parse_manifest
    from mojox_core.io.manifest import read as read_manifest

    from ._lints import lint_bare_assert, lint_path_source
    from ._output import _c, _DIM, _GREEN, _BOLD, _RED

    root = Path.cwd()
    pyproject_path = root / "pyproject.toml"

    # --- Manifest validation ---
    try:
        raw = read_manifest(pyproject_path)
        manifest = parse_manifest(raw)
        print(_c(sys.stderr, _DIM, f"manifest: {manifest.name} {manifest.version}"),
              file=sys.stderr)
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
        _manifest, _graph, env, policy, _toolchain, _host, settings, commands, include_paths = (
            _resolve_pipeline(args)
        )
    except SystemExit:
        print(_c(sys.stderr, _DIM, "compiler not available, skipping compilation check"),
              file=sys.stderr)
        if findings:
            print(_c(sys.stderr, _BOLD, f"check: {len(findings)} warning(s)"),
                  file=sys.stderr)
        else:
            print(_c(sys.stderr, _GREEN, "check: OK (manifest only)"), file=sys.stderr)
        return

    from ._output import render_diagnostics, render_dry_run
    from mojox_core import CommandKind

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    compile_commands = tuple(
        c for c in commands
        if c.kind == CommandKind.COMPILE_PACKAGE
    )

    if not compile_commands:
        print(_c(sys.stderr, _DIM, "no packages to compile"), file=sys.stderr)
        if findings:
            print(_c(sys.stderr, _BOLD, f"check: {len(findings)} warning(s)"),
                  file=sys.stderr)
        else:
            print(_c(sys.stderr, _GREEN, "check: OK"), file=sys.stderr)
        return

    if args.dry_run:
        render_dry_run(compile_commands, compact=not args.verbose)
        return

    from ._exec import run_commands

    try:
        outcomes = run_commands(
            compile_commands,
            max_workers=policy.jobs_compile,
            extra_env=settings.env if settings.env else None,
            include_paths=include_paths,
        )
    except KeyboardInterrupt:
        print(f"\n{_interrupted_summary(compile_commands)}", file=sys.stderr)
        sys.exit(130)

    from ._types import OutcomeKind

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
    findings: list,
    root: Path,
    out,
) -> None:
    """Render lint findings, deduplicating repeated messages per file."""
    from ._output import _c, _YELLOW

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
            out.write(
                f"{prefix} {rel_file}: {message}"
                f" ({len(valid)} occurrences: lines {shown}{suffix})\n"
            )
        else:
            out.write(f"{prefix} {rel_file}: {message}\n")


def _cmd_metadata(args: argparse.Namespace) -> None:
    """Execute the metadata subcommand: output build plan as JSON."""
    from mojox_core import serialize
    from ._output import render_diagnostics

    manifest, graph, env, policy, toolchain, host, settings, commands, _include_paths = (
        _resolve_pipeline(args)
    )

    if env.diagnostics:
        render_diagnostics(env.diagnostics)

    doc = serialize(graph, env, policy, commands, toolchain, env.diagnostics)
    json.dump(doc, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


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


if __name__ == "__main__":
    main()
