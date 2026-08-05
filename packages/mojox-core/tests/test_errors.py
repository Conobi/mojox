"""ConfigError carries a key path and a remediation sentence."""

from mojox_core._errors import ConfigError


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
    assert str(err).startswith("[tool.mojox.packages]")
