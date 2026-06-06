from __future__ import annotations

import json
from pathlib import Path

from src.ai.config_tool_handlers import ConfigToolHandlers
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.config_change_repository import ConfigChangeRepository
from src.service.config_edit_service import ConfigEditService
from src.service.file_service import FileService


class FakeSettings:
    def __init__(self, server_dir: Path) -> None:
        self.mc_server_dir = server_dir
        self.mc_file_tree_max_depth = 32
        self.mc_file_preview_max_bytes = 1048576
        self.mc_editable_file_max_bytes = 1048576
        self.mc_config_backup_on_save = True


def _handlers(tmp_path: Path) -> ConfigToolHandlers:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "server.properties").write_text(
        "max-players=10\npvp=true\nrcon.password=secret\n", encoding="utf-8"
    )
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    service = ConfigEditService(
        FileService(FakeSettings(server_dir)),
        ConfigChangeRepository(conn),
    )
    return ConfigToolHandlers(service)


def test_config_tool_handlers_list_capabilities(tmp_path: Path) -> None:
    handlers = _handlers(tmp_path)

    result = json.loads(handlers.list_config_capabilities({}))

    assert result["status"] == "ok"
    assert result["files"][0]["relative_path"] == "server.properties"


def test_config_tool_handlers_read_config_file_returns_safe_snapshot(tmp_path: Path) -> None:
    handlers = _handlers(tmp_path)

    result = json.loads(handlers.read_config_file({"file": "server.properties"}))

    assert result["status"] == "ok"
    assert "max-players=10" in result["visible_content"]
    assert "rcon.password" not in result["visible_content"]
    assert "secret" not in result["visible_content"]


def test_config_tool_handlers_get_values_and_propose(tmp_path: Path) -> None:
    handlers = _handlers(tmp_path)

    values = json.loads(
        handlers.get_config_values({"file": "server.properties", "keys": ["max-players"]})
    )
    proposal = json.loads(
        handlers.propose_config_change({
            "file": "server.properties",
            "changes": [{"key": "pvp", "value": "false"}],
            "user_request": "关闭 PVP",
        })
    )

    assert values["values"][0]["value"] == "10"
    assert proposal["status"] == "proposal_created"
    assert proposal["changes"][0]["new_value"] == "false"
