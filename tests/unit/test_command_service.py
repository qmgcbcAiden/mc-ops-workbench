from __future__ import annotations

from pathlib import Path

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.command_repository import CommandRepository
from src.service.command_service import CommandService


class RecordingServerService:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def send_command(self, command: str) -> dict:
        self.commands.append(command)
        return {
            "status": "executed",
            "output": "sent",
            "error_message": None,
        }


class StoppedServerService:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def send_command(self, command: str) -> dict:
        self.commands.append(command)
        return {
            "status": "failed",
            "output": None,
            "error_message": "服务器未运行。",
        }


def _command_repository(tmp_path: Path) -> CommandRepository:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    return CommandRepository(get_connection(db_path))


def test_submit_low_risk_command_sends_through_server_service_and_audits(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    service = CommandService(repository, server)

    result = service.submit_command("list")

    assert result["status"] == "executed"
    assert result["risk_level"] == "LOW"
    assert server.commands == ["list"]
    audits = repository.list_recent(limit=1)
    assert audits[0]["command"] == "list"
    assert audits[0]["status"] == "executed"
    assert audits[0]["risk_level"] == "LOW"


def test_submit_high_risk_command_requires_confirmation(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    service = CommandService(repository, server)

    result = service.submit_command("stop")

    assert result["status"] == "confirmation_required"
    assert result["risk_level"] == "HIGH"
    assert result["confirmation_required"] is True
    assert server.commands == []
    audits = repository.list_recent(limit=1)
    assert audits[0]["status"] == "confirmation_required"
    assert audits[0]["confirmation_required"] == 1


def test_submit_confirmed_high_risk_command_executes(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    service = CommandService(repository, server)

    result = service.submit_command("stop", user_confirmed=True)

    assert result["status"] == "executed"
    assert result["risk_level"] == "HIGH"
    assert server.commands == ["stop"]


def test_confirming_pending_high_risk_command_updates_original_audit(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    service = CommandService(repository, server)
    pending = service.submit_command("stop")

    result = service.complete_confirmation(pending["audit_id"], "stop", approved=True)

    assert result["status"] == "executed"
    assert server.commands == ["stop"]
    audits = repository.list_recent(limit=5)
    assert len(audits) == 1
    assert audits[0]["id"] == pending["audit_id"]
    assert audits[0]["status"] == "executed"


def test_cancelling_pending_high_risk_command_updates_original_audit(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    service = CommandService(repository, server)
    pending = service.submit_command("stop")

    result = service.complete_confirmation(pending["audit_id"], "stop", approved=False)

    assert result["status"] == "cancelled"
    assert server.commands == []
    audits = repository.list_recent(limit=5)
    assert len(audits) == 1
    assert audits[0]["id"] == pending["audit_id"]
    assert audits[0]["status"] == "cancelled"


def test_confirming_with_different_command_rejects_execution(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    service = CommandService(repository, server)
    pending = service.submit_command("stop")

    result = service.complete_confirmation(pending["audit_id"], "op Aiden233", approved=True)

    assert result["status"] == "failed"
    assert server.commands == []
    assert repository.list_recent(limit=1)[0]["status"] == "confirmation_required"


def test_replaying_confirmation_does_not_send_command_twice(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    service = CommandService(repository, server)
    pending = service.submit_command("stop")

    first = service.complete_confirmation(pending["audit_id"], "stop", approved=True)
    replay = service.complete_confirmation(pending["audit_id"], "stop", approved=True)

    assert first["status"] == "executed"
    assert replay["status"] == "failed"
    assert server.commands == ["stop"]
    assert repository.list_recent(limit=1)[0]["status"] == "executed"


def test_submit_blocked_command_is_not_sent(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    service = CommandService(repository, server)

    result = service.submit_command("cat server.properties")

    assert result["status"] == "blocked"
    assert result["risk_level"] == "BLOCKED"
    assert server.commands == []
    audits = repository.list_recent(limit=1)
    assert audits[0]["status"] == "blocked"


def test_propose_command_records_audit_without_executing(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    service = CommandService(repository, server)

    result = service.propose_command("stop")

    assert result["status"] == "confirmation_required"
    assert result["risk_level"] == "HIGH"
    assert server.commands == []
    audits = repository.list_recent(limit=1)
    assert audits[0]["requested_by"] == "ai_tool"
    assert audits[0]["status"] == "confirmation_required"


def test_submit_command_without_server_service_fails_and_audits(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    service = CommandService(repository, server_service=None)

    result = service.submit_command("list")

    assert result["status"] == "failed"
    assert result["risk_level"] == "LOW"
    assert result["error_message"] == "服务器命令服务未配置。"
    audits = repository.list_recent(limit=1)
    assert audits[0]["command"] == "list"
    assert audits[0]["status"] == "failed"


def test_empty_command_is_not_audited(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    service = CommandService(repository, server_service=None)

    result = service.submit_command("   ")

    assert result["status"] == "empty"
    assert repository.list_recent(limit=1) == []


def test_player_menu_uses_offline_config_fallback_when_server_is_stopped(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = StoppedServerService()
    fallback_calls: list[str] = []

    def fallback(command: str) -> dict:
        fallback_calls.append(command)
        return {
            "status": "file_updated",
            "message": "服务器未运行，已更新 ops.json；下次启动后生效。",
            "fallback": "config_file",
            "relative_path": "ops.json",
        }

    service = CommandService(repository, server, offline_player_command_fallback=fallback)

    result = service.submit_command(
        "op Steve",
        requested_by="player_menu",
        user_confirmed=True,
    )

    assert result["status"] == "file_updated"
    assert result["fallback"] == "config_file"
    assert server.commands == ["op Steve"]
    assert fallback_calls == ["op Steve"]
    audit = repository.list_recent(limit=1)[0]
    assert audit["command"] == "op Steve"
    assert audit["requested_by"] == "player_menu"
    assert audit["status"] == "file_updated"
    assert audit["output"] == "服务器未运行，已更新 ops.json；下次启动后生效。"


def test_non_player_menu_command_does_not_use_offline_config_fallback(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = StoppedServerService()
    fallback_calls: list[str] = []
    service = CommandService(
        repository,
        server,
        offline_player_command_fallback=lambda command: fallback_calls.append(command),
    )

    result = service.submit_command(
        "op Steve",
        requested_by="ui",
        user_confirmed=True,
    )

    assert result["status"] == "failed"
    assert fallback_calls == []
    assert repository.list_recent(limit=1)[0]["status"] == "failed"


def test_successful_server_command_does_not_use_offline_config_fallback(tmp_path: Path) -> None:
    repository = _command_repository(tmp_path)
    server = RecordingServerService()
    fallback_calls: list[str] = []
    service = CommandService(
        repository,
        server,
        offline_player_command_fallback=lambda command: fallback_calls.append(command),
    )

    result = service.submit_command(
        "op Steve",
        requested_by="player_menu",
        user_confirmed=True,
    )

    assert result["status"] == "executed"
    assert server.commands == ["op Steve"]
    assert fallback_calls == []
