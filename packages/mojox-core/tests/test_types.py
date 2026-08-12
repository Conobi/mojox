"""Frozen dataclasses: construction, equality, immutability."""

from __future__ import annotations

import pytest

from mojox_core._types import (
    BinaryEntry,
    Command,
    CommandKind,
    Diagnostic,
    DistEntry,
    DistKind,
    HostFacts,
    LintConfig,
    LocalSettings,
    Manifest,
    Policy,
    Profile,
    ResolvedEnv,
    Target,
    TargetGraph,
    TargetKind,
    Toolchain,
)


class TestEnums:
    def test_command_kind_values(self):
        assert CommandKind.COMPILE_PACKAGE.value == "compile-package"
        assert CommandKind.COMPILE_BINARY.value == "compile-binary"
        assert CommandKind.RUN_TEST.value == "run-test"
        assert CommandKind.CHECK_EXAMPLE.value == "check-example"
        assert CommandKind.RUN.value == "run"

    def test_target_kind_values(self):
        assert TargetKind.LIB.value == "lib"
        assert TargetKind.BIN.value == "bin"
        assert TargetKind.TEST.value == "test"
        assert TargetKind.EXAMPLE.value == "example"

    def test_dist_kind_values(self):
        assert DistKind.SOURCE.value == "source"
        assert DistKind.PRECOMPILED.value == "precompiled"


class TestFrozenDataclasses:
    def test_binary_entry_is_frozen(self):
        b = BinaryEntry(source="main.mojo", name="myapp")
        with pytest.raises(AttributeError):
            b.name = "other"  # type: ignore[misc]

    def test_manifest_stores_relative_paths(self):
        m = Manifest(
            name="mylib",
            version="1.0.0",
            packages=("src/mylib",),
            package_root="src",
            binaries=(BinaryEntry(source="main.mojo", name="myapp"),),
            test_roots=("tests",),
            test_parallel=False,
            defines={},
            flags=(),
            lints=LintConfig(),
            optimize=None,
            debug_level=None,
            pre_build=(),
            native_libs=(),
            source_include=None,
            source_exclude=(),
            wheel_exclude=(),
            readme="README.md",
            license_expr="MIT",
            license_files=(),
            requires_python=">=3.10",
            dependencies=("packaging>=23.0",),
            optional_dependencies={},
            description="A test library",
            keywords=(),
            authors=(),
            maintainers=(),
            urls={},
            classifiers=(),
            profiles={},
            ore_seed=None,
            build_profile="release",
        )
        assert m.name == "mylib"
        with pytest.raises(AttributeError):
            m.name = "other"  # type: ignore[misc]

    def test_command_is_frozen(self):
        from pathlib import PurePosixPath

        c = Command(
            argv=("/usr/bin/mojo", "run", "test.mojo"),
            cwd=PurePosixPath("/project"),
            env={"PATH": "/usr/bin"},
            kind=CommandKind.RUN_TEST,
            target_id="test_parser",
            timeout_s=300,
            outputs=(),
            depends_on=(),
        )
        assert c.argv[0] == "/usr/bin/mojo"
        with pytest.raises(AttributeError):
            c.kind = CommandKind.RUN  # type: ignore[misc]

    def test_host_facts_fields(self):
        from pathlib import PurePosixPath

        h = HostFacts(
            cpu_count=8,
            available_memory_mb=16384,
            manifest_dir=PurePosixPath("/workspace/project"),
        )
        assert h.cpu_count == 8
        assert h.available_memory_mb == 16384

    def test_toolchain_fields(self):
        t = Toolchain(
            mojo_path="/venv/bin/mojo",
            version="1.0.0b2",
            subcommand="precompile",
            extension=".mojoc",
        )
        assert t.version == "1.0.0b2"
        assert t.extension == ".mojoc"

    def test_diagnostic_fields(self):
        d = Diagnostic(
            kind="error",
            message="failed to parse",
            file=None,
            line=None,
            column=None,
            source_text=None,
        )
        assert d.kind == "error"

    def test_target_fields(self):
        t = Target(
            kind=TargetKind.TEST,
            path="tests/test_parser.mojo",
            target_id="tests/test_parser.mojo",
        )
        assert t.kind == TargetKind.TEST

    def test_profile_defaults(self):
        p = Profile(optimize=0, debug_level="line-tables", defines={"ASSERT": "all"}, flags=())
        assert p.optimize == 0
        assert p.defines["ASSERT"] == "all"

    def test_resolved_env_include_sequence_is_tuple(self):
        env = ResolvedEnv(
            include_sequence=(),
            mojo_path="/venv/bin/mojo",
            mojo_version="1.0.0b2",
            path_mojo=None,
            lock_version=None,
            diagnostics=(),
        )
        assert isinstance(env.include_sequence, tuple)

    def test_local_settings_empty(self):
        s = LocalSettings.EMPTY
        assert s.jobs is None
        assert s.config_paths == ()

    def test_policy_fields(self):
        p = Policy(
            optimize=0,
            debug_level="line-tables",
            defines={"ASSERT": "all"},
            flags=(),
            include_paths=(),
            lints=LintConfig(),
            jobs=1,
            jobs_compile=1,
            jobs_tests=1,
            timeout_s=300,
        )
        assert p.optimize == 0
        assert p.jobs == 1
