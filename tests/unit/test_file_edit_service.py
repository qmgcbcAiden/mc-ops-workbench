from __future__ import annotations

from pathlib import Path

from src.mc.server_files import (
    is_editable,
    is_versioned_text_config,
    validate_text_file,
    save_text_file,
    content_hash,
)


class TestIsEditable:
    def test_properties_is_editable(self):
        assert is_editable("server.properties") is True

    def test_json_is_editable(self):
        assert is_editable("config.json") is True

    def test_yaml_is_editable(self):
        assert is_editable("config.yml") is True
        assert is_editable("config.yaml") is True

    def test_common_mod_config_types_are_editable(self):
        assert is_editable("config.toml") is True
        assert is_editable("config.json5") is True
        assert is_editable("pack.mcmeta") is True
        assert is_editable("settings.ini") is True

    def test_shell_and_batch_scripts_are_editable(self):
        assert is_editable("start.sh") is True
        assert is_editable("start.bat") is True

    def test_jar_is_not_editable(self):
        assert is_editable("server.jar") is False

    def test_dat_is_not_editable(self):
        assert is_editable("level.dat") is False

    def test_unknown_extension_is_not_editable(self):
        assert is_editable("unknown.xyz") is False

    def test_config_like_text_files_are_versioned(self):
        assert is_versioned_text_config("ops.json") is True
        assert is_versioned_text_config("config/server.toml") is True
        assert is_versioned_text_config("eula.txt") is True
        assert is_versioned_text_config("notes.txt") is False
        assert is_versioned_text_config("README.md") is False


class TestValidateTextFile:
    def test_valid_json_passes(self):
        result = validate_text_file("config.json", '{"key": "value"}')
        assert result["valid"] is True

    def test_invalid_json_fails(self):
        result = validate_text_file("config.json", '{key: "value"}')
        assert result["valid"] is False
        assert "JSON" in result["error"]

    def test_properties_always_passes(self):
        result = validate_text_file("server.properties", "anything=here")
        assert result["valid"] is True


class TestSaveTextFile:
    def test_save_text_file_creates_file(self, tmp_path: Path):
        root = tmp_path / "mc_server"
        root.mkdir()
        file_path = root / "test.properties"
        file_path.write_text("key=old_value")

        result = save_text_file(
            root,
            "test.properties",
            "key=new_value",
            max_bytes=1048576,
            create_backup=True,
        )

        assert result["status"] == "saved"
        assert file_path.read_text() == "key=new_value\n"
        assert result.get("backup_path") is not None
        assert result["formatted"] is True

    def test_save_rejects_non_editable(self, tmp_path: Path):
        root = tmp_path / "mc_server"
        root.mkdir()
        file_path = root / "test.jar"
        file_path.write_text("binary")

        result = save_text_file(root, "test.jar", "new", create_backup=False)
        assert result["status"] == "failed"

    def test_save_rejects_invalid_json(self, tmp_path: Path):
        root = tmp_path / "mc_server"
        root.mkdir()
        file_path = root / "config.json"
        file_path.write_text('{"valid": true}')

        result = save_text_file(root, "config.json", "not json", create_backup=False)
        assert result["status"] == "failed"
        assert "JSON" in result.get("error_message", "")

    def test_save_formats_json(self, tmp_path: Path):
        root = tmp_path / "mc_server"
        root.mkdir()
        file_path = root / "ops.json"
        file_path.write_text("[]\n", encoding="utf-8")

        result = save_text_file(
            root,
            "ops.json",
            '[{"name":"Aiden233","level":4}]',
            create_backup=False,
        )

        assert result["status"] == "saved"
        assert file_path.read_text(encoding="utf-8") == (
            '[\n  {\n    "name": "Aiden233",\n    "level": 4\n  }\n]\n'
        )

    def test_save_script_without_auto_formatting(self, tmp_path: Path):
        root = tmp_path / "mc_server"
        root.mkdir()
        file_path = root / "start.sh"
        file_path.write_text("#!/usr/bin/env sh\n", encoding="utf-8")
        content = "java   -jar server.jar nogui"

        result = save_text_file(root, "start.sh", content, create_backup=False)

        assert result["status"] == "saved"
        assert result["formatted"] is False
        assert file_path.read_text(encoding="utf-8") == content

    def test_save_creates_backup(self, tmp_path: Path):
        root = tmp_path / "mc_server"
        root.mkdir()
        file_path = root / "server.properties"
        file_path.write_text("original content")

        result = save_text_file(
            root,
            "server.properties",
            "updated content",
            create_backup=True,
        )

        assert result["status"] == "saved"
        # Check backup exists
        backups = list(root.glob("server.properties.*.bak"))
        assert len(backups) > 0

    def test_path_traversal_prevented(self, tmp_path: Path):
        root = tmp_path / "mc_server"
        root.mkdir()
        # Create a file outside root
        outside = tmp_path / "outside.txt"
        outside.write_text("sensitive")

        import pytest
        with pytest.raises(ValueError, match="Access denied"):
            save_text_file(root, "../outside.txt", "evil", create_backup=False)


class TestContentHash:
    def test_same_content_same_hash(self):
        h1 = content_hash("hello")
        h2 = content_hash("hello")
        assert h1 == h2

    def test_different_content_different_hash(self):
        h1 = content_hash("hello")
        h2 = content_hash("world")
        assert h1 != h2
