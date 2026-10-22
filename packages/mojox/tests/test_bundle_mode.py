"""Bundle mode: one harness binary for every test function, with honest flags and output."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest
from mojox.cli import _plan_bundle, build_parser
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


# Fake ``mojo``: each call logs ``<subcommand> <source>``. ``precompile``
# writes a package, or fails when the lib's ``__init__.mojo`` contains
# PRECOMPILE_ERROR. ``build`` reads the source, or for the harness every
# staged test file, and fails on COMPILE_ERROR; otherwise it writes a
# binary that prints the harness summary and passes, or with HARNESS_FAIL
# fails after the summary, or with HARNESS_CRASH aborts before it.
_FAKE_MOJO = """#!{python}
import pathlib, stat, sys
args = sys.argv[1:]
with open({log!r}, "a") as f:
    f.write(args[0] + " " + args[1] + "\\n")
out = pathlib.Path(args[args.index("-o") + 1])
out.parent.mkdir(parents=True, exist_ok=True)
if args[0] == "precompile":
    if "PRECOMPILE_ERROR" in (pathlib.Path(args[1]) / "__init__.mojo").read_text():
        print(args[1] + "/__init__.mojo:1:1: error: bad lib", file=sys.stderr)
        sys.exit(1)
    out.write_text("mojoc")
    sys.exit(0)
source = pathlib.Path(args[1])
if source.name == "harness.mojo":
    text = "".join(p.read_text() for p in sorted((source.parent / "stage").rglob("test_*.mojo")))
else:
    text = source.read_text()
if "COMPILE_ERROR" in text:
    print(args[1] + ":1:1: error: use of unknown declaration", file=sys.stderr)
    sys.exit(1)
if "HARNESS_CRASH" in text:
    body = "os.kill(os.getpid(), signal.SIGABRT)\\n"
elif "HARNESS_FAIL" in text:
    body = "print('FAIL t')\\nprint('---')\\nprint('0 passed, 1 failed, 0 skipped')\\nsys.exit(1)\\n"
else:
    body = "print('---')\\nprint('1 passed, 0 failed, 0 skipped')\\n"
out.write_text("#!{python}\\nimport os, signal, sys\\n" + body)
out.chmod(out.stat().st_mode | stat.S_IEXEC)
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Project with one lib, two tests and one example, wired to a fake ``mojo``; returns (root, log)."""
    from mojox_core import Toolchain

    proj = tmp_path / "proj"
    (proj / "tests").mkdir(parents=True)
    (proj / "pyproject.toml").write_text('[project]\nname = "testlib"\nversion = "0.1.0"\n')
    (proj / "tests" / "test_a.mojo").write_text("def test_a():\n    pass\n")
    (proj / "tests" / "test_b.mojo").write_text("def test_b():\n    pass\n\ndef test_c():\n    pass\n")
    (proj / "src" / "mylib").mkdir(parents=True)
    (proj / "src" / "mylib" / "__init__.mojo").write_text("fn lib(): pass\n")
    (proj / "examples" / "hello").mkdir(parents=True)
    (proj / "examples" / "hello" / "main.mojo").write_text("def main():\n    pass\n")

    log = tmp_path / "calls.log"
    log.touch()
    fake = tmp_path / "bin" / "mojo"
    fake.parent.mkdir()
    fake.write_text(_FAKE_MOJO.format(python=sys.executable, log=str(log)))
    fake.chmod(0o755)

    toolchain = Toolchain(mojo_path=str(fake), version="1.0.0", subcommand="precompile", extension=".mojoc")
    monkeypatch.setattr("mojox_core.io.toolchain.resolve", lambda: toolchain)
    monkeypatch.setattr("mojox_core.io.environment.read_distributions", list)
    for name in ("MOJOX_JOBS", "MOJOX_TIMEOUT", "MOJOX_COMPILE_THREADS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(proj)
    return proj, log


def _test(*argv: str) -> int:
    """Run ``mojox test --no-config *argv`` in-process and return its exit code (0 when it just returns)."""
    from mojox.cli import _cmd_test

    try:
        _cmd_test(build_parser().parse_args(["test", "--no-config", *argv]))
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _calls(log: Path) -> list[str]:
    """The fake compiler's calls, one ``<subcommand> <source>`` per line."""
    return log.read_text().splitlines()


def _set_mojox(proj: Path, body: str) -> None:
    """Replace the project's ``[tool.mojox]`` table with *body*."""
    (proj / "pyproject.toml").write_text(f'[project]\nname = "testlib"\nversion = "0.1.0"\n\n[tool.mojox]\n{body}')


_HARNESS_BUILD = "build .mojox/bundle/harness.mojo"


class TestTestBundleOption:
    def test_manifest_default_runs_bundle_mode(self, project):
        proj, log = project
        _set_mojox(proj, "test-bundle = true\n")
        assert _test() == 0
        assert _HARNESS_BUILD in _calls(log)

    def test_no_bundle_overrides_the_manifest(self, project):
        proj, log = project
        _set_mojox(proj, "test-bundle = true\n")
        assert _test("--no-bundle") == 0
        assert _HARNESS_BUILD not in _calls(log)
        assert "build tests/test_a.mojo" in _calls(log)

    def test_profile_default_applies_only_with_that_profile(self, project):
        proj, log = project
        _set_mojox(proj, "[tool.mojox.profile.ci]\ntest-bundle = true\n")
        assert _test() == 0
        assert _HARNESS_BUILD not in _calls(log)
        assert _test("--profile", "ci") == 0
        assert _HARNESS_BUILD in _calls(log)

    def test_bundle_flag_overrides_a_profile_that_turns_it_off(self, project):
        proj, log = project
        _set_mojox(proj, "test-bundle = true\n\n[tool.mojox.profile.ci]\ntest-bundle = false\n")
        assert _test("--profile", "ci") == 0
        assert _HARNESS_BUILD not in _calls(log)
        assert _test("--profile", "ci", "--bundle") == 0
        assert _HARNESS_BUILD in _calls(log)

    def test_a_string_value_exits_2_naming_the_key(self, project, capsys):
        proj, log = project
        _set_mojox(proj, 'test-bundle = "yes"\n')
        assert _test() == 2
        assert "tool.mojox.test-bundle: expected true or false, got 'yes'" in capsys.readouterr().err
        assert _calls(log) == []

    def test_a_settings_file_cannot_set_it(self, project, capsys):
        proj, log = project
        config = proj / "settings.toml"
        config.write_text("test-bundle = true\n")
        from mojox.cli import _cmd_test

        with pytest.raises(SystemExit) as exc:
            _cmd_test(build_parser().parse_args(["test", "--config-file", str(config)]))
        assert exc.value.code == 2
        assert f"{config}:test-bundle: `test-bundle` cannot be set in local settings" in capsys.readouterr().err
        assert _calls(log) == []


class TestMetadata:
    def test_reports_the_mode_and_its_origin_with_the_per_file_plan(self, project, capsys):
        import json

        from mojox.cli import _cmd_metadata

        proj, log = project
        _set_mojox(proj, "test-bundle = true\n")
        _cmd_metadata(build_parser().parse_args(["metadata", "--no-config"]))
        doc = json.loads(capsys.readouterr().out)
        assert (doc["policy"]["test_bundle"], doc["policy"]["origins"]["test-bundle"]) == (
            True,
            "tool.mojox.test-bundle",
        )
        assert doc["commands_mode"] == "files"
        assert {c["target_id"] for c in doc["commands"] if c["kind"] == "build-test"} == {
            "tests/test_a.mojo",
            "tests/test_b.mojo",
        }
        assert not (proj / ".mojox" / "bundle").exists()
        assert _calls(log) == []

    def test_no_bundle_flag_is_the_origin(self, project, capsys):
        import json

        from mojox.cli import _cmd_metadata

        proj, _log = project
        _set_mojox(proj, "test-bundle = true\n")
        _cmd_metadata(build_parser().parse_args(["metadata", "--no-config", "--no-bundle"]))
        policy = json.loads(capsys.readouterr().out)["policy"]
        assert (policy["test_bundle"], policy["origins"]["test-bundle"]) == (False, "--bundle")


class TestJsonMode:
    @pytest.mark.parametrize(("argv", "mode"), [(("--bundle",), "bundle"), ((), "files")], ids=["bundle", "files"])
    def test_suite_started_names_the_mode(self, project, capsys, argv, mode):
        import json

        assert _test("--output-format", "json", *argv) == 0
        started = json.loads(capsys.readouterr().out.splitlines()[0])
        assert (started["event"], started["mode"], started["schema_version"]) == ("started", mode, 1)

    def test_no_match_in_bundle_mode_names_the_mode(self, project, capsys):
        import json

        assert _test("--output-format", "json", "--bundle", "tests/nothing_here") == 0
        assert json.loads(capsys.readouterr().out.splitlines()[0])["mode"] == "bundle"


class TestHeader:
    def test_states_mode_counts_timeout_and_origins(self, project, capsys):
        assert _test("--bundle") == 0
        err = capsys.readouterr().err
        assert (
            "targets (jobs 1, compile threads 1)\n"
            "bundle: 1 binary, 3 test functions, 1 package (--bundle); timeout 300 s for the harness (default)\n"
        ) in err

    def test_counts_functions_after_path_filters_and_before_k(self, project, capsys):
        assert _test("--bundle", "tests/test_b.mojo", "-k", "test_c") == 0
        assert "bundle: 1 binary, 2 test functions, 1 package (--bundle)" in capsys.readouterr().err

    def test_shows_a_settings_file_timeout_and_a_manifest_mode(self, project, capsys):
        from mojox.cli import _cmd_test

        proj, _log = project
        _set_mojox(proj, "test-bundle = true\ntimeout = 900\n")
        config = proj / "settings.toml"
        config.write_text("timeout = 60\n")
        with pytest.raises(SystemExit) as exc:
            _cmd_test(build_parser().parse_args(["test", "--config-file", str(config)]))
        assert exc.value.code == 0
        assert f"(tool.mojox.test-bundle); timeout 60 s for the harness ({config}:timeout)\n" in capsys.readouterr().err

    def test_per_file_mode_has_no_bundle_line(self, project, capsys):
        assert _test() == 0
        assert "bundle:" not in capsys.readouterr().err

    def test_json_output_has_no_header(self, project, capsys):
        assert _test("--bundle", "--output-format", "json") == 0
        assert "bundle:" not in capsys.readouterr().err
