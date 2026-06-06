from __future__ import annotations

import json
from pathlib import Path

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.chat_repository import ChatRepository
from src.repositories.command_repository import CommandRepository
from src.service.chat_service import ChatService
from src.service.command_service import CommandService


class FakePlayerService:
    def get_online_players(self) -> dict:
        return {"online_count": 0, "players": []}


class FakeLogService:
    def list_recent(self, level: str | None = None, limit: int = 20) -> list[dict]:
        return []

    def capture_cursor(self) -> int:
        return 0

    def list_since(self, cursor: int, limit: int = 20) -> list[dict]:
        del cursor, limit
        return []


class FakeSystemService:
    def capture_metrics(self) -> dict:
        return {"cpu_percent": 0, "memory_percent": 0}


class TrackingPlayerService(FakePlayerService):
    def __init__(self) -> None:
        self.calls = 0

    def get_online_players(self) -> dict:
        self.calls += 1
        return super().get_online_players()


class TrackingLogService(FakeLogService):
    def __init__(self) -> None:
        self.list_since_calls = 0

    def list_since(self, cursor: int, limit: int = 20) -> list[dict]:
        self.list_since_calls += 1
        return super().list_since(cursor, limit)


class FakeServerService:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.restart_calls = 0
        self.start_calls = 0

    def send_command(self, command: str) -> dict:
        self.commands.append(command)
        return {"status": "executed", "output": "sent", "error_message": None}

    def start_server(self) -> dict:
        self.start_calls += 1
        return {"state": "starting", "pid": 4321, "message": "starting"}

    def restart_server(self) -> dict:
        self.restart_calls += 1
        return {
            "operation": "restart_server",
            "status": "executed",
            "stop": {"state": "stopped"},
            "stopped": {"state": "stopped"},
            "start": {"state": "starting", "pid": 4321},
            "message": "服务器已停止并提交启动请求，当前状态：starting。",
            "error_message": None,
        }


def _chat(
    tmp_path: Path,
    java_environment=None,
) -> tuple[ChatService, ChatRepository, FakeServerService, CommandRepository]:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    server = FakeServerService()
    command_repository = CommandRepository(conn)
    command_service = CommandService(command_repository, server)
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        command_service=command_service,
        server_service=server,
        java_environment_service=java_environment,
    )
    return chat, chat_repo, server, command_repository


class FakeJavaEnvironmentService:
    def __init__(self, status: str = "selected") -> None:
        self.status = status
        self.ensure_calls: list[dict] = []

    def ensure_environment(
        self,
        server_version: str | None = None,
        start_after_ready: bool = False,
    ) -> dict:
        self.ensure_calls.append({
            "server_version": server_version,
            "start_after_ready": start_after_ready,
        })
        return {
            "status": self.status,
            "minecraft_version": server_version or "1.20.1",
            "required_java_major": 17,
            "selected_java": {"java_path": "/fake/java/bin/java"},
            "message": "Java ready",
        }

    def check_environment(self, server_version: str | None = None) -> dict:
        return self.ensure_environment(server_version=server_version)


def test_chat_locally_routes_op_request_to_high_risk_command_confirmation(tmp_path: Path) -> None:
    chat, _chat_repo, server, _command_repository = _chat(tmp_path)
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "把 Aiden233 设为管理员")

    assert result["source"] == "local"
    assert result["command_action"]["status"] == "confirmation_required"
    assert result["command_action"]["command"] == "op Aiden233"
    assert "对话内确认卡片" in result["assistant"]
    assert server.commands == []


def test_chat_start_request_ensures_java_before_starting_server(tmp_path: Path) -> None:
    java_environment = FakeJavaEnvironmentService()
    chat, _chat_repo, server, _command_repository = _chat(
        tmp_path,
        java_environment=java_environment,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "帮我启动 1.20.1 服务器")

    assert result["source"] == "local"
    assert java_environment.ensure_calls == [
        {"server_version": "1.20.1", "start_after_ready": True}
    ]
    assert server.start_calls == 1
    assert result["java_environment"]["status"] == "selected"
    assert result["server_action"]["action_type"] == "server_start"


def test_chat_locally_routes_chinese_shutdown_request_to_confirmation(tmp_path: Path) -> None:
    chat, _chat_repo, server, _command_repository = _chat(tmp_path)
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "帮我关闭服务器")

    assert result["command_action"]["status"] == "confirmation_required"
    assert result["command_action"]["command"] == "stop"
    assert "对话内确认卡片" in result["assistant"]
    assert server.commands == []


def test_chat_creates_confirmation_for_each_command_in_compound_request(tmp_path: Path) -> None:
    chat, _chat_repo, server, command_repository = _chat(tmp_path)
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "帮我把Aiden233的管理员取消，然后关闭服务器")

    assert [action["command"] for action in result["command_actions"]] == [
        "deop Aiden233",
        "stop",
    ]
    assert all(
        action["status"] == "confirmation_required"
        for action in result["command_actions"]
    )
    assert len(command_repository.list_recent(limit=5)) == 2
    assert server.commands == []


def test_chat_locally_routes_restart_to_single_server_restart_confirmation(tmp_path: Path) -> None:
    chat, _chat_repo, server, command_repository = _chat(tmp_path)
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "帮我重启服务器")

    assert result["source"] == "local"
    assert len(result["command_actions"]) == 1
    assert result["command_action"]["status"] == "confirmation_required"
    assert result["command_action"]["action_type"] == "server_restart"
    assert result["command_action"]["command"] == "restart_server"
    assert "调用 `start_server`" in result["assistant"]
    assert server.commands == []
    assert server.restart_calls == 0
    assert command_repository.list_recent(limit=5) == []


def test_confirm_restart_action_restarts_without_command_audit(tmp_path: Path) -> None:
    chat, chat_repo, server, command_repository = _chat(tmp_path)
    session_id = chat.create_session("test")
    pending = chat.send_message(session_id, "重启一下服务器")

    result = chat.confirm_command_action(
        session_id,
        pending["command_action"]["command"],
        audit_id=pending["command_action"]["audit_id"],
    )

    assert result["status"] == "executed"
    assert result["tool_message_id"]
    assert server.restart_calls == 1
    assert server.commands == []
    assert command_repository.list_recent(limit=5) == []
    assert "启动状态：starting" in result["assistant"]
    messages = chat_repo.list_messages(session_id)
    tool_message = messages[-2]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_name"] == "restart_server"
    assert "server_restart" in tool_message["content"]


def test_confirm_command_action_executes_and_records_tool_result(tmp_path: Path) -> None:
    chat, chat_repo, server, command_repository = _chat(tmp_path)
    session_id = chat.create_session("test")
    pending = chat.send_message(session_id, "把 Aiden233 设为管理员")

    result = chat.confirm_command_action(
        session_id,
        "op Aiden233",
        audit_id=pending["command_action"]["audit_id"],
    )

    assert result["status"] == "executed"
    assert result["tool_message_id"]
    assert "命令 `op Aiden233` 已发送" in result["assistant"]
    assert server.commands == ["op Aiden233"]
    audits = command_repository.list_recent(limit=5)
    assert len(audits) == 1
    assert audits[0]["status"] == "executed"
    messages = chat_repo.list_messages(session_id)
    tool_message = messages[-2]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_name"] == "execute_server_command"
    assert "command_confirmation" in tool_message["content"]
    assert "op Aiden233" in tool_message["content"]
    payload = json.loads(tool_message["content"])
    assert payload["current_online_players"]["available"] is True
    assert payload["current_online_players"]["online_count"] == 0
    assert payload["current_online_players"]["players"] == []
    assert messages[-1]["role"] == "assistant"
    assert "命令 `op Aiden233` 已发送" in messages[-1]["content"]


def test_command_execution_returns_before_feedback_context_is_collected(tmp_path: Path) -> None:
    chat, _chat_repo, server, _command_repository = _chat(tmp_path)
    player_service = TrackingPlayerService()
    log_service = TrackingLogService()
    chat.player_service = player_service
    chat.log_service = log_service
    session_id = chat.create_session("test")
    pending = chat.send_message(session_id, "把 Aiden233 设为管理员")

    executed = chat.execute_confirmed_command_action(
        session_id,
        "op Aiden233",
        audit_id=pending["command_action"]["audit_id"],
    )

    assert executed["status"] == "executed"
    assert server.commands == ["op Aiden233"]
    assert log_service.list_since_calls == 0
    assert player_service.calls == 0

    completed = chat.complete_command_action_feedback(session_id, executed)

    assert completed["tool_message_id"]
    assert log_service.list_since_calls == 1
    assert player_service.calls == 1


def test_cancel_command_action_records_tool_result_without_execution(tmp_path: Path) -> None:
    chat, chat_repo, server, command_repository = _chat(tmp_path)
    session_id = chat.create_session("test")
    pending = chat.send_message(session_id, "把 Aiden233 设为管理员")

    result = chat.cancel_command_action(
        session_id,
        "op Aiden233",
        audit_id=pending["command_action"]["audit_id"],
    )

    assert result["status"] == "cancelled"
    assert result["tool_message_id"]
    assert "取消" in result["assistant"]
    assert server.commands == []
    audits = command_repository.list_recent(limit=5)
    assert len(audits) == 1
    assert audits[0]["status"] == "cancelled"
    messages = chat_repo.list_messages(session_id)
    tool_message = messages[-2]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_name"] == "execute_server_command"
    assert "command_cancelled" in tool_message["content"]
    assert "op Aiden233" in tool_message["content"]
    assert messages[-1]["role"] == "assistant"
