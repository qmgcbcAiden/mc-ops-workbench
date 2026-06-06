from __future__ import annotations

from pathlib import Path

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.chat_repository import ChatRepository
from src.service.chat_service import ChatService


class FakePlayerService:
    def get_online_players(self) -> dict:
        return {"online_count": 0, "players": []}


class FakeLogService:
    def list_recent(self, level: str = "ANY", limit: int = 100) -> list[dict]:
        return []


class FakeSystemService:
    def capture_metrics(self) -> dict:
        return {"cpu_percent": 0, "memory_percent": 0}


class FakeAddonService:
    def __init__(self) -> None:
        self.refresh_online_values: list[bool] = []

    def scan_addons(self, refresh_online: bool = False) -> dict:
        self.refresh_online_values.append(refresh_online)
        return {
            "status": "completed",
            "message": "已扫描 1 个组件，发现 1 个阻断问题。",
            "assets": [{"relative_path": "mods/client.jar"}],
            "diagnostics": [
                {
                    "id": "d1",
                    "severity": "BLOCKER",
                    "category": "client_only_mod",
                    "message": "客户端 mod 不能放进服务端。",
                    "evidence_type": "metadata",
                    "confidence": "metadata",
                    "affected_files": ["mods/client.jar"],
                    "suggested_actions": [],
                }
            ],
            "summary": {"blockers": 1, "high": 0, "medium": 0, "low": 0},
        }


def test_chat_routes_addon_diagnostic_request_to_local_scan(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    addon_service = FakeAddonService()
    chat = ChatService(
        chat_repository=ChatRepository(get_connection(db_path)),
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        addon_service=addon_service,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "帮我检查 mods 和插件冲突")

    assert result["source"] == "local"
    assert result["tool_name"] == "scan_server_addons"
    assert addon_service.refresh_online_values == [False]
    assert "模型知识不会作为冲突判定依据" in result["assistant"]


def test_chat_allows_explicit_online_metadata_refresh(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    addon_service = FakeAddonService()
    chat = ChatService(
        chat_repository=ChatRepository(get_connection(db_path)),
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        addon_service=addon_service,
    )
    session_id = chat.create_session("test")

    chat.send_message(session_id, "联网刷新元数据并检查插件冲突")

    assert addon_service.refresh_online_values == [True]
