"""ConfigError carries a key path and a remediation sentence."""

from mojox_core.errors import ConfigError


def test_config_error_has_key_path_and_message():
    err = ConfigError("tool.mojox.optimize", "must be 0–3, got 'fast'")
    assert err.key_path == "tool.mojox.optimize"
    assert err.message == "must be 0–3, got 'fast'"
    assert "tool.mojox.optimize" in str(err)
    assert "must be 0–3" in str(err)


def test_config_error_is_an_exception():
    err = ConfigError("project.name", "required")
    assert isinstance(err, Exception)


def test_config_error_without_remediation():
    err = ConfigError("tool.mojox.packages", "expected a list of strings")
    assert str(err) == "tool.mojox.packages: expected a list of strings"


def test_config_error_renders_origin_then_message():
    assert str(ConfigError("MOJOX_JOBS", "jobs must not be 0")) == "MOJOX_JOBS: jobs must not be 0"


def _unsafe(text):
    import unicodedata

    return [c for c in text if unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"}]


def test_config_error_escapes_control_characters_in_the_origin():
    origin = "/srv/proj/evil\x1b[31m\nname\x9b/config.toml:jobs"
    err = ConfigError(origin, "expected an integer >= 1, got 0")
    assert err.key_path == origin
    assert _unsafe(str(err)) == []
    assert str(err).endswith(": expected an integer >= 1, got 0")


def test_config_error_escapes_bidi_overrides_and_separators():
    err = ConfigError("/p/\u202eexe.toml\u2028x\u2029:jobs", "bad \u202e value")
    assert _unsafe(str(err)) == []
    assert "\\u202e" in str(err)


def test_config_error_escapes_undecodable_filename_bytes():
    err = ConfigError("/p/\udcff.toml:jobs", "bad")
    assert _unsafe(str(err)) == []
    str(err).encode("utf-8")  # must not raise UnicodeEncodeError


def test_escape_for_display_keeps_printable_text_unchanged():
    from mojox_core.errors import escape_for_display

    assert escape_for_display("/srv/projet é/config.toml") == "/srv/projet é/config.toml"
    assert escape_for_display("a\x1bb") == "'a\\x1bb'"
