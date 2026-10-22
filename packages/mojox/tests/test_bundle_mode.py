"""Bundle mode: one harness binary for every test function, with honest flags and output."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from mojox.cli import _plan_bundle
from mojox_core import (
    CommandKind,
    DistEntry,
    DistKind,
    HostFacts,
    LintConfig,
    Policy,
    ResolvedEnv,
    Target,
    TargetGraph,
    TargetKind,
    Toolchain,
)


def _bundle_inputs(root: Path, *, libs: tuple[str, ...]) -> dict:
    """Keyword arguments for :func:`_plan_bundle` on a project at *root* whose lib dirs are *libs*."""
    for lib in libs:
        (root / lib).mkdir(parents=True)
    graph = TargetGraph(
        targets=(
            *(Target(TargetKind.LIB, lib, lib) for lib in libs),
            Target(TargetKind.TEST, "tests/test_a.mojo", "tests/test_a.mojo"),
        ),
        edges=(),
    )
    policy = Policy(
        optimize=0,
        debug_level="none",
        defines={},
        flags=(),
        include_paths=(),
        lints=LintConfig(warnings_as_errors=True),
        jobs=1,
        jobs_compile=1,
        jobs_tests=1,
        timeout_s=300,
        compile_threads=1,
        jobs_spec="1",
    )
    env = ResolvedEnv(
        include_sequence=(DistEntry("dep", "/deps", DistKind.SOURCE, ("dep",), "1.0"),),
        mojo_path="/bin/mojo",
        mojo_version="1.0.0",
        path_mojo=None,
        lock_version=None,
    )
    return {
        "root": root,
        "graph": graph,
        "env": env,
        "policy": policy,
        "toolchain": Toolchain(mojo_path="/bin/mojo", version="1.0.0", subcommand="precompile", extension=".mojoc"),
        "host": HostFacts(cpu_count=4, available_memory_mb=1024, manifest_dir=PurePosixPath(root)),
        "harness_rel": ".mojox/bundle/harness.mojo",
        "staging_include": str(root / ".mojox" / "bundle" / "stage"),
    }


def _includes(argv: tuple[str, ...]) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == "-I"]


class TestPlanBundle:
    def test_every_lib_is_precompiled_before_the_harness(self, tmp_path):
        commands = _plan_bundle(**_bundle_inputs(tmp_path, libs=("src/mylib", "src/other")))
        assert [(c.kind, c.target_id) for c in commands] == [
            (CommandKind.COMPILE_PACKAGE, "src/mylib"),
            (CommandKind.COMPILE_PACKAGE, "src/other"),
            (CommandKind.BUILD_TEST, "bundle::harness"),
        ]
        assert commands[-1].depends_on == ("src/mylib", "src/other")

    def test_harness_sees_packages_then_staging_then_lib_parents(self, tmp_path):
        commands = _plan_bundle(**_bundle_inputs(tmp_path, libs=("src/mylib",)))
        assert _includes(commands[-1].argv) == [
            f"{tmp_path}/.mojox/build/pkg",
            f"{tmp_path}/.mojox/bundle/stage",
            f"{tmp_path}/src",
            "/deps",
        ]

    def test_libs_under_one_parent_add_it_once(self, tmp_path):
        commands = _plan_bundle(**_bundle_inputs(tmp_path, libs=("src/mylib", "src/other")))
        assert _includes(commands[-1].argv) == [
            f"{tmp_path}/.mojox/build/pkg",
            f"{tmp_path}/.mojox/bundle/stage",
            f"{tmp_path}/src",
            "/deps",
        ]

    def test_lib_that_is_not_a_dir_adds_no_source_dir(self, tmp_path):
        inputs = _bundle_inputs(tmp_path, libs=())
        (tmp_path / "single").mkdir()
        (tmp_path / "single" / "mod.mojo").write_text("")
        lib = Target(TargetKind.LIB, "single/mod.mojo", "single/mod.mojo")
        inputs["graph"] = TargetGraph(targets=(lib, *inputs["graph"].targets), edges=())
        commands = _plan_bundle(**inputs)
        assert _includes(commands[-1].argv) == [
            f"{tmp_path}/.mojox/build/pkg",
            f"{tmp_path}/.mojox/bundle/stage",
            "/deps",
        ]

    def test_harness_is_built_without_werror(self, tmp_path):
        commands = _plan_bundle(**_bundle_inputs(tmp_path, libs=("src/mylib",)))
        assert all("--Werror" not in c.argv for c in commands)

    def test_only_the_harness_is_a_test(self, tmp_path):
        commands = _plan_bundle(**_bundle_inputs(tmp_path, libs=()))
        assert [(c.kind, c.target_id) for c in commands] == [(CommandKind.BUILD_TEST, "bundle::harness")]
