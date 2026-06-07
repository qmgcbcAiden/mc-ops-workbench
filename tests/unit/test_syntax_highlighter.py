from __future__ import annotations

from src.ui.components.syntax_highlighter import tokenize_line


def _roles(line: str, language: str) -> list[str]:
    return [token.role for token in tokenize_line(line, language) if token.text.strip()]


def test_highlights_properties_key_boolean_and_comment() -> None:
    assert _roles("online-mode=false", "properties") == ["key", "operator", "keyword"]
    assert _roles("# managed by server", "properties") == ["comment"]


def test_highlights_json_key_string_and_number() -> None:
    roles = _roles('  "name": "Aiden233", "level": 4', "json")

    assert roles == ["key", "operator", "string", "operator", "key", "operator", "number"]


def test_highlights_yaml_and_toml_configuration_lines() -> None:
    assert _roles("enabled: true", "yaml") == ["key", "operator", "keyword"]
    assert _roles("[server]", "toml") == ["section"]
    assert _roles("maxPlayers = 20", "toml") == ["key", "operator", "number"]


def test_highlights_shell_and_batch_scripts() -> None:
    assert _roles("# launch server", "shell") == ["comment"]
    assert _roles('java -jar "$SERVER_JAR" nogui', "shell") == [
        "keyword",
        "operator",
        "string",
        "plain",
    ]
    assert _roles("REM launch server", "batch") == ["comment"]
    assert _roles("java -jar %SERVER_JAR% nogui", "batch") == [
        "keyword",
        "operator",
        "key",
        "plain",
    ]
