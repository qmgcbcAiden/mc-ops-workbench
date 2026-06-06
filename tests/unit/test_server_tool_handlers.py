from __future__ import annotations

import json
from pathlib import Path

from src.ai.server_tool_handlers import ServerToolHandlers
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.command_repository import CommandRepository
from src.service.command_service import CommandService


class FakePlayerService:
    def get_online_players(self) -> dict:
        return {"online_count": 0, "players": []}


class FakeLogService:
    def list_recent(self, level: str | None = None, limit: int = 20) -> list[dict]:
        return []


class FakeSystemService:
    def capture_metrics(self) -> dict:
        return {"cpu_percent": 0, "memory_percent": 0}


class FakeServerService:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.start_calls = 0

    def get_status(self) -> dict:
        return {"state": "running", "pid": 1234, "online_players": 0}

    def start_server(self) -> dict:
        self.start_calls += 1
        return {
            "state": "starting",
            "status": "starting",
            "pid": 4321,
            "message": "Server process is starting.",
        }

    def send_command(self, command: str) -> dict:
        self.commands.append(command)
        return {
            "status": "executed",
            "output": f"sent: {command}",
            "error_message": None,
        }


def _handlers(tmp_path: Path) -> tuple[ServerToolHandlers, FakeServerService, CommandRepository]:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    command_repo = CommandRepository(get_connection(db_path))
    server_service = FakeServerService()
    handlers = ServerToolHandlers(
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=server_service,
        command_service=CommandService(command_repo, server_service),
    )
    return handlers, server_service, command_repo


def test_start_server_calls_server_service_without_command_audit(tmp_path: Path) -> None:
    handlers, server_service, command_repo = _handlers(tmp_path)

    result = json.loads(handlers.start_server({}))

    assert result["status"] == "ok"
    assert result["server"]["state"] == "starting"
    assert result["server"]["pid"] == 4321
    assert "启动请求已提交" in result["message"]
    assert server_service.start_calls == 1
    assert command_repo.list_recent(limit=1) == []


def test_restart_server_returns_confirmation_without_starting(tmp_path: Path) -> None:
    handlers, server_service, command_repo = _handlers(tmp_path)

    result = json.loads(handlers.restart_server({}))

    assert result["status"] == "confirmation_required"
    assert result["action_type"] == "server_restart"
    assert result["command"] == "restart_server"
    assert result["risk_level"] == "HIGH"
    assert server_service.start_calls == 0
    assert command_repo.list_recent(limit=1) == []


def test_execute_server_command_runs_low_risk_command_and_audits(tmp_path: Path) -> None:
    handlers, server_service, command_repo = _handlers(tmp_path)

    result = json.loads(handlers.execute_server_command({"command": "list"}))

    assert result["status"] == "executed"
    assert result["risk_level"] == "LOW"
    assert result["output"] == "sent: list"
    assert server_service.commands == ["list"]
    audit = command_repo.list_recent(limit=1)[0]
    assert audit["requested_by"] == "ai_tool"
    assert audit["status"] == "executed"


def test_execute_server_command_does_not_run_high_risk_without_confirmation(tmp_path: Path) -> None:
    handlers, server_service, command_repo = _handlers(tmp_path)

    result = json.loads(handlers.execute_server_command({"command": "stop"}))

    assert result["status"] == "confirmation_required"
    assert result["risk_level"] == "HIGH"
    assert result["confirmation_required"] is True
    assert server_service.commands == []
    audit = command_repo.list_recent(limit=1)[0]
    assert audit["status"] == "confirmation_required"


def test_execute_server_command_blocks_non_minecraft_command(tmp_path: Path) -> None:
    handlers, server_service, command_repo = _handlers(tmp_path)

    result = json.loads(handlers.execute_server_command({"command": "cat server.properties"}))

    assert result["status"] == "blocked"
    assert result["risk_level"] == "BLOCKED"
    assert server_service.commands == []
    audit = command_repo.list_recent(limit=1)[0]
    assert audit["status"] == "blocked"
