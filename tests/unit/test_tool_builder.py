from __future__ import annotations

import json
from pathlib import Path

from src.ai.tool_builder import build_tool_registry
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
        self.start_calls = 0

    def get_status(self) -> dict:
        return {"state": "running", "pid": 1, "online_players": 0}

    def start_server(self) -> dict:
        self.start_calls += 1
        return {"state": "starting", "pid": 2}

    def send_command(self, command: str) -> dict:
        return {"status": "executed", "output": command, "error_message": None}


class FakeConfigService:
    def list_capabilities(self) -> dict:
        return {"status": "ok", "files": []}

    def read_config_file(self, relative_path: str) -> dict:
        return {"status": "ok", "relative_path": relative_path}

    def get_values(self, relative_path: str, keys: list[str]) -> dict:
        return {"status": "ok", "relative_path": relative_path, "values": []}

    def propose_change(
        self,
        relative_path: str,
        changes: list[dict],
        user_request: str,
    ) -> dict:
        return {"status": "no_change", "relative_path": relative_path}


class FakeJavaEnvironmentService:
    def __init__(self, status: str = "selected") -> None:
        self.status = status
        self.ensure_calls = 0

    def ensure_environment(
        self,
        server_version: str | None = None,
        start_after_ready: bool = False,
    ) -> dict:
        del server_version
        self.ensure_calls += 1
        return {
            "status": self.status,
            "minecraft_version": "1.20.1",
            "required_java_major": 17,
            "selected_java": {"java_path": "/fake/java/bin/java"},
            "message": "Java ready",
            "metadata": {"start_after_ready": start_after_ready},
        }

    def check_environment(self, server_version: str | None = None) -> dict:
        return self.ensure_environment(server_version=server_version)


class FakeAddonService:
    def scan_addons(self, refresh_online: bool = False) -> dict:
        return {
            "status": "completed",
            "refresh_online": refresh_online,
            "assets": [],
            "diagnostics": [],
            "summary": {},
        }

    def list_addon_diagnostics(self, severity: str | None = None) -> list[dict]:
        return [{"id": "d1", "severity": severity or "BLOCKER"}]

    def get_latest_addon_report(self) -> dict:
        return {"status": "completed", "assets": [], "diagnostics": []}

    def create_addon_remediation_plan(self, diagnostic_ids: list[str]) -> dict:
        return {"status": "proposal_created", "diagnostic_ids": diagnostic_ids}


def test_tool_registry_includes_execute_server_command(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    server_service = FakeServerService()
    registry = build_tool_registry(
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=server_service,
        command_service=CommandService(CommandRepository(get_connection(db_path)), server_service),
        config_service=FakeConfigService(),
        java_environment_service=FakeJavaEnvironmentService(),
        addon_service=FakeAddonService(),
    )

    tool_names = {schema["function"]["name"] for schema in registry.get_schemas()}

    assert "execute_server_command" in tool_names
    assert "check_java_environment" in tool_names
    assert "ensure_java_environment" in tool_names
    assert "start_server" in tool_names
    assert "restart_server" in tool_names
    assert "scan_server_addons" in tool_names
    assert "get_addon_diagnostics" in tool_names
    assert "propose_addon_remediation" in tool_names

    scan = registry.execute("scan_server_addons", {"refresh_online": True})

    assert scan.ok is True
    assert json.loads(scan.content)["refresh_online"] is False


def test_start_server_tool_ensures_java_before_starting(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    server_service = FakeServerService()
    java_environment = FakeJavaEnvironmentService()
    registry = build_tool_registry(
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=server_service,
        command_service=CommandService(CommandRepository(get_connection(db_path)), server_service),
        config_service=FakeConfigService(),
        java_environment_service=java_environment,
    )

    result = registry.execute("start_server", {})

    assert result.ok is True
    assert java_environment.ensure_calls == 1
    assert server_service.start_calls == 1
    assert '"java_environment"' in result.content
