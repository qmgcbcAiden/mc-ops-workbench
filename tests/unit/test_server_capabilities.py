from __future__ import annotations

import zipfile
from pathlib import Path

from src.mc.server_capabilities import detect_server_capabilities


def test_detects_paper_core_from_server_jar(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    jar = server_dir / "paper-1.21.4.jar"
    jar.write_bytes(b"not a real jar")

    capabilities = detect_server_capabilities(server_dir, jar).to_dict()

    assert capabilities["server_core"]["name"] == "Paper"
    assert capabilities["server_core"]["version"] == "1.21.4"
    assert capabilities["supports_plugins"] is True


def test_detects_tempban_commands_from_plugin_yml(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    plugins_dir = server_dir / "plugins"
    plugins_dir.mkdir(parents=True)
    with zipfile.ZipFile(plugins_dir / "CustomPunish.jar", "w") as archive:
        archive.writestr(
            "plugin.yml",
            "\n".join(
                [
                    "name: CustomPunish",
                    "version: 1.0.0",
                    "commands:",
                    "  tempban:",
                    "    description: temporary ban",
                    "  tempipban:",
                    "    description: temporary ip ban",
                ]
            ),
        )

    capabilities = detect_server_capabilities(server_dir, server_dir / "server.jar").to_dict()

    assert capabilities["supports_temp_ban"] is True
    assert capabilities["supports_temp_ip_ban"] is True
    assert capabilities["temp_ban_command"] == "tempban"
    assert capabilities["temp_ip_ban_command"] == "tempipban"
    assert capabilities["temp_ban_provider"] == "CustomPunish"


def test_known_plugin_filename_enables_tempban_when_metadata_is_missing(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    plugins_dir = server_dir / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "EssentialsX-2.20.1.jar").write_bytes(b"not a real jar")

    capabilities = detect_server_capabilities(server_dir, server_dir / "server.jar").to_dict()

    assert capabilities["supports_temp_ban"] is True
    assert capabilities["supports_temp_ip_ban"] is True
    assert capabilities["temp_ban_command"] == "tempban"
    assert capabilities["temp_ip_ban_command"] == "tempbanip"


def test_no_tempban_without_known_plugin_or_declared_command(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    plugins_dir = server_dir / "plugins"
    plugins_dir.mkdir(parents=True)
    with zipfile.ZipFile(plugins_dir / "ChatOnly.jar", "w") as archive:
        archive.writestr(
            "plugin.yml",
            "\n".join(["name: ChatOnly", "version: 1.0.0", "commands:", "  chat:"]),
        )

    capabilities = detect_server_capabilities(server_dir, server_dir / "server.jar").to_dict()

    assert capabilities["supports_temp_ban"] is False
    assert capabilities["supports_temp_ip_ban"] is False
