from __future__ import annotations

from pathlib import Path

from tests.fixtures.fake_mc_server import create_fake_mc_server
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.config_version_repository import ConfigVersionRepository
from src.service.config_version_service import ConfigVersionService
from src.service.file_service import FileService
from src.interface.file_interface import FileInterface


def _make_settings(tmp_path: Path, server_dir: Path) -> object:
    class FakeSettings:
        mc_server_dir = server_dir
        mc_file_tree_max_depth = 32
        mc_file_preview_max_bytes = 1048576
        mc_editable_file_max_bytes = 1048576
        mc_config_backup_on_save = True
    return FakeSettings()


def test_file_interface_returns_tree(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    create_fake_mc_server(server_dir)

    settings = _make_settings(tmp_path, server_dir)
    interface = FileInterface(FileService(settings))

    tree = interface.get_tree()
    assert tree["name"] == "mc_server"
    assert tree["kind"] == "directory"
    children = tree.get("children", [])
    child_names = [c["name"] for c in children]
    assert "server.properties" in child_names


def test_file_interface_preview_text_file(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    create_fake_mc_server(server_dir)

    settings = _make_settings(tmp_path, server_dir)
    interface = FileInterface(FileService(settings))

    preview = interface.preview_file("server.properties")
    assert "server-port" in preview["content"]
    assert preview["truncated"] is False


def test_file_interface_list_directory(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    create_fake_mc_server(server_dir)

    settings = _make_settings(tmp_path, server_dir)
    interface = FileInterface(FileService(settings))

    nodes = interface.list_directory("")
    names = [n["name"] for n in nodes]
    assert "logs" in names
    assert "server.jar" in names


def test_file_preview_rejects_binary(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    create_fake_mc_server(server_dir)

    settings = _make_settings(tmp_path, server_dir)
    interface = FileInterface(FileService(settings))

    preview = interface.preview_file("server.jar")
    assert "不是安全文本文件" in preview["content"]


def test_file_interface_formats_structured_config_text(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    settings = _make_settings(tmp_path, server_dir)
    interface = FileInterface(FileService(settings))

    result = interface.format_text_file("ops.json", '[{"name":"TestOp"}]')

    assert result["status"] == "formatted"
    assert result["content"] == '[\n  {\n    "name": "TestOp"\n  }\n]\n'


def test_versioned_json_save_uses_git_history_instead_of_backup(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "ops.json").write_text("[]\n", encoding="utf-8")
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    try:
        version_service = ConfigVersionService(
            repo_dir=tmp_path / "config_versions" / "repo",
            version_repo=ConfigVersionRepository(connection),
        )
        interface = FileInterface(
            FileService(
                _make_settings(tmp_path, server_dir),
                version_service=version_service,
            )
        )

        result = interface.save_text_file("ops.json", '[{"name":"Aiden233","level":4}]')

        assert result["status"] == "saved"
        assert result["backup_path"] is None
        assert result["version_status"] == "committed"
        assert list(server_dir.glob("ops.json.*.bak")) == []
        history = version_service.list_history("ops.json")
        assert len(history) == 2
        assert {entry["message"].split("(", 1)[0] for entry in history} == {
            "baseline config",
            "edit",
        }
    finally:
        connection.close()
