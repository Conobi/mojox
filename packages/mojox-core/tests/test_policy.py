"""Policy resolution: merge profiles, lints, and CLI into the flag set."""

from __future__ import annotations

import pytest

from mojox_core._errors import ConfigError
from mojox_core._types import LintConfig, LocalSettings, Manifest, Policy, Profile
from mojox_core.policy import resolve, BUILTIN_DEV, BUILTIN_RELEASE


def _minimal_manifest(**overrides) -> Manifest:
    """Build a minimal Manifest, overriding specific fields."""
    from mojox_core._types import BinaryEntry

    defaults = dict(
        name="x",
        version="1.0.0",
        description=None,
        readme=None,
        license_expr=None,
        license_files=(),
        requires_python=None,
        dependencies=(),
        optional_dependencies={},
        keywords=(),
        authors=(),
        maintainers=(),
        urls={},
        classifiers=(),
        packages=None,
        package_root="src",
        binaries=(),
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
        profiles={},
        rlib_seed=None,
    )
    defaults.update(overrides)
    return Manifest(**defaults)


class TestBuiltinDefaults:
    def test_dev_assert_is_all(self):
        assert BUILTIN_DEV.defines["ASSERT"] == "all"

    def test_dev_optimize_is_zero(self):
        assert BUILTIN_DEV.optimize == 0

    def test_dev_debug_is_line_tables(self):
        assert BUILTIN_DEV.debug_level == "line-tables"

    def test_release_assert_is_safe(self):
        assert BUILTIN_RELEASE.defines["ASSERT"] == "safe"

    def test_release_optimize_is_three(self):
        assert BUILTIN_RELEASE.optimize == 3

    def test_release_debug_is_none(self):
        assert BUILTIN_RELEASE.debug_level == "none"


class TestPrecedence:
    def test_builtin_defaults_when_nothing_configured(self):
        m = _minimal_manifest()
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.optimize == 0
        assert p.defines["ASSERT"] == "all"
        assert p.debug_level == "line-tables"

    def test_manifest_toplevel_overrides_builtin(self):
        m = _minimal_manifest(optimize=2)
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.optimize == 2

    def test_manifest_profile_overrides_toplevel(self):
        m = _minimal_manifest(
            optimize=2,
            profiles={"dev": Profile(optimize=1, defines={}, flags=())},
        )
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.optimize == 1

    def test_cli_overrides_everything(self):
        m = _minimal_manifest(
            optimize=2,
            profiles={"dev": Profile(optimize=1, defines={}, flags=())},
        )
        p = resolve(m, "dev", {"optimize": 0}, LocalSettings.EMPTY)
        assert p.optimize == 0

    def test_release_is_default_for_build(self):
        m = _minimal_manifest()
        p = resolve(m, "release", {}, LocalSettings.EMPTY)
        assert p.optimize == 3
        assert p.defines["ASSERT"] == "safe"


class TestMergeSemantics:
    def test_optimize_replaces(self):
        m = _minimal_manifest(optimize=1)
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.optimize == 1

    def test_defines_map_replace_by_key(self):
        m = _minimal_manifest(defines={"FAST": "true", "ASSERT": "none"})
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.defines["FAST"] == "true"
        assert p.defines["ASSERT"] == "none"

    def test_flags_append_higher_layer_last(self):
        m = _minimal_manifest(
            flags=("-Xlinker", "-rpath"),
            profiles={"dev": Profile(flags=("--extra",), defines={})},
        )
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.flags == ("-Xlinker", "-rpath", "--extra")

    def test_assert_follows_normal_define_precedence(self):
        m = _minimal_manifest(defines={"ASSERT": "none"})
        p = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p.defines["ASSERT"] == "none"

    def test_no_profile_table_is_byte_identical_to_builtin(self):
        m = _minimal_manifest()
        p1 = resolve(m, "dev", {}, LocalSettings.EMPTY)
        p2 = resolve(m, "dev", {}, LocalSettings.EMPTY)
        assert p1 == p2


class TestTestParallel:
    def test_test_parallel_false_clamps_jobs_to_one(self):
        m = _minimal_manifest(test_parallel=False)
        p = resolve(m, "dev", {"jobs": 8}, LocalSettings.EMPTY)
        assert p.jobs_tests == 1
        assert p.jobs_compile == 8

    def test_test_parallel_true_allows_jobs(self):
        m = _minimal_manifest(test_parallel=True)
        p = resolve(m, "dev", {"jobs": 4}, LocalSettings.EMPTY)
        assert p.jobs_tests == 4


class TestErrors:
    def test_unknown_profile_name(self):
        m = _minimal_manifest()
        with pytest.raises(ConfigError, match="nonexistent"):
            resolve(m, "nonexistent", {}, LocalSettings.EMPTY)
