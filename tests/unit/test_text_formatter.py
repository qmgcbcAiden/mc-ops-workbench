from __future__ import annotations

from src.mc.text_formatter import format_text, language_for_path


def test_language_for_common_minecraft_configuration_files() -> None:
    assert language_for_path("server.properties") == "properties"
    assert language_for_path("eula.txt") == "properties"
    assert language_for_path("ops.json") == "json"
    assert language_for_path("pack.mcmeta") == "json"
    assert language_for_path("plugins/example/config.yml") == "yaml"
    assert language_for_path("config/forge-server.toml") == "toml"
    assert language_for_path("config/mod.json5") == "json5"
    assert language_for_path("config/module.ini") == "ini"
    assert language_for_path("start.sh") == "shell"
    assert language_for_path("start.bat") == "batch"


def test_formats_json_with_indentation() -> None:
    result = format_text("ops.json", '[{"name":"Aiden233","level":4}]')

    assert result.error is None
    assert result.changed is True
    assert result.content == '[\n  {\n    "name": "Aiden233",\n    "level": 4\n  }\n]\n'


def test_formats_properties_without_removing_comments() -> None:
    result = format_text("server.properties", "# server\nmax-players = 20 \npvp:true\n")

    assert result.content == "# server\nmax-players=20\npvp=true\n"


def test_formats_minecraft_eula_as_properties() -> None:
    result = format_text("eula.txt", "# EULA acceptance\neula = true \n")

    assert result.content == "# EULA acceptance\neula=true\n"


def test_formats_yaml_toml_and_ini_conservatively() -> None:
    yaml_result = format_text("plugins/example/config.yml", "enabled:true  \nitems:\n\t- stone\n")
    toml_result = format_text("config/forge-server.toml", "[server]\nmaxPlayers= 20 \n")
    ini_result = format_text("config/server.ini", "[world]\nname = spawn  \n")

    assert yaml_result.content == "enabled: true\nitems:\n  - stone\n"
    assert toml_result.content == "[server]\nmaxPlayers = 20\n"
    assert ini_result.content == "[world]\nname = spawn\n"


def test_invalid_structured_file_returns_error_without_rewriting() -> None:
    result = format_text("ops.json", "{invalid json")

    assert result.error is not None
    assert result.content == "{invalid json"
