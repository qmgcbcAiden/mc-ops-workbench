from __future__ import annotations

from src.mc.config_secret_redactor import (
    REDACTED_VALUE,
    _is_secret_key,
    redact_config_text,
    redact_for_display,
    redact_properties,
)


class TestRedactProperties:
    def test_redacts_rcon_password(self):
        content = "rcon.password=super-secret\nmax-players=10\n"
        result = redact_properties(content)
        assert "super-secret" not in result
        assert "rcon.password=<redacted>" in result
        assert "max-players=10" in result

    def test_redacts_key_containing_password(self):
        content = "some.password=abc123\n"
        result = redact_properties(content)
        assert "abc123" not in result
        assert "some.password=<redacted>" in result

    def test_redacts_key_containing_token(self):
        content = "api.token=xyz789\n"
        result = redact_properties(content)
        assert "xyz789" not in result
        assert "api.token=<redacted>" in result

    def test_redacts_key_containing_secret(self):
        content = "db.secret=hidden\n"
        result = redact_properties(content)
        assert "hidden" not in result
        assert "db.secret=<redacted>" in result

    def test_preserves_comments(self):
        content = "# This is a comment\nmax-players=10\n"
        result = redact_properties(content)
        assert "# This is a comment" in result
        assert "max-players=10" in result

    def test_preserves_blank_lines(self):
        content = "max-players=10\n\npvp=false\n"
        result = redact_properties(content)
        assert result.count("\n") >= 2

    def test_preserves_non_secret_values(self):
        content = "max-players=20\npvp=false\nview-distance=8\n"
        result = redact_properties(content)
        assert "max-players=20" in result
        assert "pvp=false" in result
        assert "view-distance=8" in result

    def test_handles_key_without_value(self):
        content = "some-key=\nmax-players=10\n"
        result = redact_properties(content)
        assert "max-players=10" in result

    def test_handles_line_without_equals(self):
        content = "not_a_property_line\nmax-players=10\n"
        result = redact_properties(content)
        assert "not_a_property_line" in result
        assert "max-players=10" in result

    def test_full_server_properties(self):
        content = (
            "#Minecraft server properties\n"
            "max-players=20\n"
            "pvp=true\n"
            "rcon.password=mysecret123\n"
            "online-mode=true\n"
        )
        result = redact_properties(content)
        assert "mysecret123" not in result
        assert "rcon.password=<redacted>" in result
        assert "max-players=20" in result
        assert "online-mode=true" in result


class TestRedactStructuredConfig:
    def test_redacts_nested_json_secrets(self):
        content = '{"name": "server", "auth": {"api_token": "abc123"}}\n'

        result = redact_config_text("config/auth.json", content)

        assert "abc123" not in result
        assert '"api_token": "<redacted>"' in result
        assert '"name": "server"' in result

    def test_redacts_yaml_and_toml_assignment_secrets(self):
        yaml_result = redact_config_text("config/auth.yml", "password: hidden\nport: 25565\n")
        toml_result = redact_config_text("config/auth.toml", 'secret = "hidden"\nport = 25565\n')

        assert "hidden" not in yaml_result
        assert "password: <redacted>" in yaml_result
        assert "hidden" not in toml_result
        assert 'secret = "<redacted>"' in toml_result


class TestIsSecretKey:
    def test_explicit_secret_key(self):
        assert _is_secret_key("rcon.password") is True

    def test_contains_password(self):
        assert _is_secret_key("db.password") is True
        assert _is_secret_key("database_password") is True

    def test_contains_token(self):
        assert _is_secret_key("api.token") is True

    def test_contains_secret(self):
        assert _is_secret_key("encryption.secret") is True

    def test_non_secret_key(self):
        assert _is_secret_key("max-players") is False
        assert _is_secret_key("pvp") is False
        assert _is_secret_key("view-distance") is False

    def test_key_is_not_secret_just_for_containing_key(self):
        # 'key' alone is not a secret indicator
        assert _is_secret_key("some-key") is False


class TestRedactForDisplay:
    def test_redacts_value(self):
        assert redact_for_display("abc") == REDACTED_VALUE

    def test_handles_none(self):
        assert redact_for_display(None) is None
