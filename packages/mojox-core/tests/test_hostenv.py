"""The host-variable allowlist and the lexical PATH rules."""

from __future__ import annotations

import pytest
from mojox_core.hostenv import absolute_path_entries, construct_path, is_forwarded_name, select_forwarded_env


class TestForwardedNames:
    @pytest.mark.parametrize(
        "name",
        [
            "CC",
            "CXX",
            "AR",
            "LD",
            "PKG_CONFIG_PATH",
            "CARGO_HOME",
            "CARGO_TARGET_DIR",
            "RUSTFLAGS",
            "SSL_CERT_FILE",
            "TMPDIR",
            "SOURCE_DATE_EPOCH",
            "NIX_CC",
            "NIX_BINTOOLS",
            "NIX_CFLAGS_COMPILE",
            "NIX_LDFLAGS",
            "NIX_HARDENING_ENABLE",
            "NIX_SSL_CERT_FILE",
            "NIX_BUILD_CORES",
            "NIX_STORE",
            "NIX_ENFORCE_NO_NATIVE",
        ],
    )
    def test_listed_names_are_forwarded(self, name):
        assert is_forwarded_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "NIX_CFLAGS_COMPILE_x86_64_unknown_linux_gnu",
            "NIX_LDFLAGS_x86_64_unknown_linux_gnu",
            "NIX_CC_WRAPPER_TARGET_HOST_x86_64_unknown_linux_gnu",
            "NIX_BINTOOLS_WRAPPER_TARGET_HOST_x86_64_unknown_linux_gnu",
        ],
    )
    def test_target_suffixed_variants_are_forwarded(self, name):
        assert is_forwarded_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "NIX_CONFIG",
            "NIX_PATH",
            "NIX_REMOTE",
            "NIX_FOO",
            "NIX_",
            "NIX_LDFLAGS_",
            "NIX_CC_WRAPPER",
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "PYTHONPATH",
            "FOO",
            "PATH",
            "HOME",
            "LANG",
            "NM",
            "OBJCOPY",
            "STRIP",
            "nix_cc",
            "CC ",
        ],
    )
    def test_other_names_are_not(self, name):
        assert not is_forwarded_name(name)


class TestSelectForwardedEnv:
    def test_keeps_listed_non_empty_values(self):
        environ = {
            "NIX_CC": "/nix/store/x-gcc",
            "TMPDIR": "/run/t",
            "FOO": "bar",
            "PKG_CONFIG_PATH": "/p",
            "CARGO_HOME": "/c",
        }
        assert select_forwarded_env(environ) == {
            "NIX_CC": "/nix/store/x-gcc",
            "TMPDIR": "/run/t",
            "PKG_CONFIG_PATH": "/p",
            "CARGO_HOME": "/c",
        }

    def test_empty_values_count_as_unset(self):
        assert select_forwarded_env({"NIX_CC": "", "TMPDIR": "/t"}) == {"TMPDIR": "/t"}

    def test_a_nix_config_token_is_never_selected(self):
        environ = {
            "NIX_CONFIG": "access-tokens = github.com=ghp_SECRET",
            "NIX_PATH": "nixpkgs=/secret",
            "NIX_REMOTE": "daemon",
            "NIX_FOO": "x",
            "NIX_CC": "/nix/store/x-gcc",
        }
        selected = select_forwarded_env(environ)
        assert selected == {"NIX_CC": "/nix/store/x-gcc"}
        assert "ghp_SECRET" not in repr(selected)

    @pytest.mark.parametrize("value", ["./cc", "bin/cc", "../x/cc", "sub/dir/gcc"])
    def test_relative_compiler_paths_are_dropped(self, value):
        assert select_forwarded_env({"CC": value, "CXX": value}) == {}

    @pytest.mark.parametrize("value", ["gcc", "/nix/store/x-gcc/bin/gcc", "clang++"])
    def test_bare_names_and_absolute_paths_survive_the_shape_check(self, value):
        assert select_forwarded_env({"CC": value, "CXX": value}) == {"CC": value, "CXX": value}


class TestAbsolutePathEntries:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("/a:/b", ("/a", "/b")),
            ("/a::/b", ("/a", "/b")),
            (":/a", ("/a",)),
            ("/a:", ("/a",)),
            ("::", ()),
            ("", ()),
            ("/a:.:/b", ("/a", "/b")),
            ("/a:bin:./bin:../x:/b", ("/a", "/b")),
            ("/a:/b:/a:/b", ("/a", "/b")),
            ("bin:/a", ("/a",)),
        ],
    )
    def test_only_absolute_entries_survive_first_occurrence_wins(self, raw, expected):
        assert absolute_path_entries(raw) == expected


class TestConstructPath:
    def test_mojo_dir_then_entries_then_standard_dirs(self):
        assert construct_path("/venv/bin", ("/nix/store/a/bin", "/opt/x/bin")) == (
            "/venv/bin:/nix/store/a/bin:/opt/x/bin:/usr/local/bin:/usr/bin:/bin"
        )

    def test_duplicates_collapse_first_wins(self):
        assert construct_path("/usr/bin", ("/venv/bin", "/usr/bin", "/venv/bin")) == (
            "/usr/bin:/venv/bin:/usr/local/bin:/bin"
        )

    def test_no_entries_gives_the_standard_directories(self):
        assert construct_path("/venv/bin", ()) == "/venv/bin:/usr/local/bin:/usr/bin:/bin"

    def test_no_mojo_dir_is_allowed(self):
        assert construct_path(None, ("/a",)) == "/a:/usr/local/bin:/usr/bin:/bin"

    @pytest.mark.parametrize("bad", ["", ".", "bin", "../x", "/a:b", "/a::b"])
    def test_hand_built_relative_or_multi_entries_are_dropped(self, bad):
        assert construct_path("/venv/bin", (bad, "/ok")) == "/venv/bin:/ok:/usr/local/bin:/usr/bin:/bin"
