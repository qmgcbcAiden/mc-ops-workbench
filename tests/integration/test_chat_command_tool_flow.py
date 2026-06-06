from __future__ import annotations

import json
from pathlib import Path

from src.ai.assistant_service import AssistantService
from src.ai.context_manager import ContextManager
from src.ai.llm_client import LlmResponse
from src.ai.server_tool_handlers import ServerToolHandlers
from src.ai.stream_events import ChatStreamEvent, StreamEventType
from src.ai.tool_registry import ToolRegistry
from src.ai.tool_schemas import (
    EXECUTE_SERVER_COMMAND,
    PROPOSE_SERVER_COMMAND,
    RESTART_SERVER,
    START_SERVER,
)
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.chat_attachment_repository import ChatAttachmentRepository
from src.repositories.chat_repository import ChatRepository
from src.repositories.chat_summary_repository import ChatSummaryRepository
from src.repositories.command_repository import CommandRepository
from src.repositories.llm_repository import LlmRepository
from src.service.chat_service import ChatService
from src.service.command_service import CommandService


class FakeCommandLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            tool_names = {tool["function"]["name"] for tool in tools or []}
            assert "execute_server_command" in tool_names
            assert "propose_server_command" in tool_names
            return LlmResponse(
                content="",
                model=self.model,
                tool_calls=[
                    {
                        "id": "call_propose_op",
                        "name": "propose_server_command",
                        "arguments": json.dumps({"command": "op Aiden233"}),
                    }
                ],
            )
        if self.calls == 3:
            assert tools is None
            joined = "\n".join(message.get("content", "") for message in messages)
            assert "command_confirmation" in joined
            assert '"status": "executed"' in joined
            assert "op Aiden233" in joined
            assert "当前在线玩家快照" in joined
            assert '"online_count": 0' in joined
            assert "历史事件" in joined
            assert "joined the game" not in joined
            assert "Made Aiden233 a server operator" in joined
            return LlmResponse(
                content="Aiden233 已成功获得 OP 权限，命令已发送到服务器控制台。",
                model=self.model,
            )
        return LlmResponse(content="已成功将 Aiden233 设为管理员。", model=self.model)


class FakeTextOnlyCommandLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        return LlmResponse(
            content=(
                "命令风险评估结果：op Aiden233 是 HIGH 风险，"
                "需要用户确认后通过 CommandService 执行。"
            ),
            model=self.model,
        )


class FakeStopOnlyCommandLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            return LlmResponse(
                content="",
                model=self.model,
                tool_calls=[{
                    "id": "call_only_stop",
                    "name": "propose_server_command",
                    "arguments": json.dumps({"command": "stop"}),
                }],
            )
        return LlmResponse(content="仅处理了 stop。", model=self.model)


class FakeStartServerLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            tool_names = {tool["function"]["name"] for tool in tools or []}
            assert "start_server" in tool_names
            return LlmResponse(
                content="",
                model=self.model,
                tool_calls=[{
                    "id": "call_start_server",
                    "name": "start_server",
                    "arguments": "{}",
                }],
            )
        assert any(message["role"] == "tool" for message in messages)
        return LlmResponse(content="服务器启动请求已提交，正在启动。", model=self.model)


class FakeRestartServerLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            tool_names = {tool["function"]["name"] for tool in tools or []}
            assert "restart_server" in tool_names
            return LlmResponse(
                content="",
                model=self.model,
                tool_calls=[{
                    "id": "call_restart_server",
                    "name": "restart_server",
                    "arguments": "{}",
                }],
            )
        assert any(message["role"] == "tool" for message in messages)
        return LlmResponse(content="已创建重启确认，请在卡片中确认。", model=self.model)


class FakeStreamingCommandLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(self, messages: list[dict], tools: list[dict] | None = None):
        self.calls += 1
        if self.calls == 1:
            yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_START)
            yield ChatStreamEvent(
                event_type=StreamEventType.TOOL_RESULT,
                tool_name="execute_server_command",
                tool_arguments={
                    "id": "call_stream_stop",
                    "raw": json.dumps({"command": "stop"}),
                },
            )
            return
        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_START)
        yield ChatStreamEvent(
            event_type=StreamEventType.DELTA,
            text="模型生成的后续说明不应覆盖本地待确认内容。",
        )
        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_END)


class FakePlayerService:
    def get_online_players(self) -> dict:
        return {"online_count": 0, "players": []}


class FakeLogService:
    def list_recent(self, level: str | None = None, limit: int = 20) -> list[dict]:
        return [
            {
                "event_time": "11:59:50",
                "level": "INFO",
                "message": "Aiden233 joined the game",
                "raw_line": "[11:59:50 INFO]: Aiden233 joined the game",
            },
            {
                "event_time": "12:00:01",
                "level": "INFO",
                "message": "Aiden233 issued server command: /op Aiden233",
                "raw_line": "[12:00:01 INFO]: Aiden233 issued server command: /op Aiden233",
            },
            {
                "event_time": "12:00:02",
                "level": "INFO",
                "message": "Made Aiden233 a server operator",
                "raw_line": "[12:00:02 INFO]: Made Aiden233 a server operator",
            },
        ][:limit]

    def capture_cursor(self) -> int:
        return 10

    def list_since(self, cursor: int, limit: int = 20) -> list[dict]:
        assert cursor == 10
        return self.list_recent(limit=limit)[1:]


class FakeSystemService:
    def capture_metrics(self) -> dict:
        return {"cpu_percent": 0, "memory_percent": 0}


class FakeServerService:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.start_calls = 0
        self.restart_calls = 0

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

    def send_command(self, command: str) -> dict:
        self.commands.append(command)
        return {"status": "executed", "output": "sent", "error_message": None}


def test_chat_command_tool_does_not_claim_high_risk_op_was_sent(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    command_repo = CommandRepository(conn)
    server_service = FakeServerService()
    command_service = CommandService(command_repo, server_service)
    handlers = ServerToolHandlers(
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=server_service,
        command_service=command_service,
    )
    registry = ToolRegistry()
    registry.register(
        "execute_server_command",
        handlers.execute_server_command,
        EXECUTE_SERVER_COMMAND,
    )
    registry.register(
        "propose_server_command",
        handlers.propose_server_command,
        PROPOSE_SERVER_COMMAND,
    )
    fake_llm = FakeCommandLlm()
    assistant = AssistantService(fake_llm, registry, LlmRepository(conn))
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=ContextManager(chat_repo, summary_repo, attachment_repo),
        command_service=command_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "把 Aiden233 设为管理员")

    assert result["source"] == "ai"
    assert result["command_action"]["status"] == "confirmation_required"
    assert result["command_action"]["command"] == "op Aiden233"
    assert result["command_action"]["source_tool"] == "propose_server_command"
    assert "尚未发送" in result["assistant"]
    assert "成功" not in result["assistant"]
    assert server_service.commands == []
    audit = command_repo.list_recent(limit=1)[0]
    assert audit["status"] == "confirmation_required"

    confirmed = chat.confirm_command_action(
        session_id,
        "op Aiden233",
        audit_id=result["command_action"]["audit_id"],
    )

    assert confirmed["status"] == "executed"
    assert confirmed["assistant"] == "Aiden233 已成功获得 OP 权限，命令已发送到服务器控制台。"
    assert any("Made Aiden233 a server operator" in line for line in confirmed["recent_server_logs"])
    assert server_service.commands == ["op Aiden233"]
    audits = command_repo.list_recent(limit=5)
    assert len(audits) == 1
    assert audits[0]["id"] == result["command_action"]["audit_id"]
    assert audits[0]["status"] == "executed"
    assert fake_llm.calls == 3
    messages = chat_repo.list_messages(session_id)
    assert messages[-2]["role"] == "tool"
    assert messages[-2]["tool_name"] == "execute_server_command"
    assert "command_confirmation" in messages[-2]["content"]
    assert messages[-1]["role"] == "assistant"
    assert "OP 权限" in messages[-1]["content"]


def test_chat_ai_can_call_start_server_tool_and_records_tool_call(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    command_repo = CommandRepository(conn)
    server_service = FakeServerService()
    command_service = CommandService(command_repo, server_service)
    handlers = ServerToolHandlers(
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=server_service,
        command_service=command_service,
    )
    registry = ToolRegistry()
    registry.register(
        "start_server",
        handlers.start_server,
        START_SERVER,
    )
    fake_llm = FakeStartServerLlm()
    assistant = AssistantService(fake_llm, registry, LlmRepository(conn))
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=ContextManager(chat_repo, summary_repo, attachment_repo),
        command_service=command_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "帮我启动服务器")

    assert result["source"] == "ai"
    assert result["assistant"] == "服务器启动请求已提交，正在启动。"
    assert result["server_action"] == {
        "action_type": "server_start",
        "source_tool": "start_server",
        "server": {
            "state": "starting",
            "status": "starting",
            "pid": 4321,
            "message": "Server process is starting.",
        },
        "message": "服务器启动请求已提交，状态：starting，PID：4321。",
    }
    assert server_service.start_calls == 1
    assert server_service.commands == []
    assert command_repo.list_recent(limit=1) == []
    row = conn.execute(
        "SELECT status, provider_tool_call_id, result_json FROM tool_calls WHERE tool_name = ?",
        ("start_server",),
    ).fetchone()
    assert row["status"] == "completed"
    assert row["provider_tool_call_id"] == "call_start_server"
    assert '"state": "starting"' in row["result_json"]


def test_chat_ai_restart_tool_creates_single_confirmation_then_restarts(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    command_repo = CommandRepository(conn)
    server_service = FakeServerService()
    command_service = CommandService(command_repo, server_service)
    handlers = ServerToolHandlers(
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=server_service,
        command_service=command_service,
    )
    registry = ToolRegistry()
    registry.register(
        "restart_server",
        handlers.restart_server,
        RESTART_SERVER,
    )
    fake_llm = FakeRestartServerLlm()
    assistant = AssistantService(fake_llm, registry, LlmRepository(conn))
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=ContextManager(chat_repo, summary_repo, attachment_repo),
        command_service=command_service,
        server_service=server_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "请重新启动这个服务器", attachment_ids=["force_ai"])

    assert result["source"] == "ai"
    assert result["command_action"]["action_type"] == "server_restart"
    assert result["command_action"]["command"] == "restart_server"
    assert result["command_actions"] == [result["command_action"]]
    assert command_repo.list_recent(limit=5) == []
    tool_row = conn.execute(
        "SELECT status, provider_tool_call_id, result_json FROM tool_calls WHERE tool_name = ?",
        ("restart_server",),
    ).fetchone()
    assert tool_row["status"] == "completed"
    assert tool_row["provider_tool_call_id"] == "call_restart_server"
    assert '"action_type": "server_restart"' in tool_row["result_json"]

    confirmed = chat.confirm_command_action(
        session_id,
        result["command_action"]["command"],
        audit_id=result["command_action"]["audit_id"],
    )

    assert confirmed["status"] == "executed"
    assert server_service.restart_calls == 1
    assert server_service.commands == []
    assert command_repo.list_recent(limit=5) == []


def test_chat_command_guardrail_shows_confirmation_when_llm_returns_text_only(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    command_repo = CommandRepository(conn)
    server_service = FakeServerService()
    command_service = CommandService(command_repo, server_service)
    fake_llm = FakeTextOnlyCommandLlm()
    assistant = AssistantService(fake_llm, ToolRegistry(), LlmRepository(conn))
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=ContextManager(chat_repo, summary_repo, attachment_repo),
        command_service=command_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "把 Aiden233 设为管理员")

    assert result["source"] == "ai"
    assert result["command_action"]["status"] == "confirmation_required"
    assert result["command_action"]["command"] == "op Aiden233"
    assert result["command_action"]["guardrail_generated"] is True
    assert "对话内确认卡片" in result["assistant"]
    assert server_service.commands == []


def test_chinese_shutdown_request_gets_confirmation_when_llm_returns_text_only(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    command_repo = CommandRepository(conn)
    server_service = FakeServerService()
    command_service = CommandService(command_repo, server_service)
    fake_llm = FakeTextOnlyCommandLlm()
    assistant = AssistantService(fake_llm, ToolRegistry(), LlmRepository(conn))
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=ContextManager(chat_repo, summary_repo, attachment_repo),
        command_service=command_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "帮我关闭服务器")

    assert result["command_action"]["status"] == "confirmation_required"
    assert result["command_action"]["command"] == "stop"
    assert result["command_action"]["guardrail_generated"] is True
    assert "对话内确认卡片" in result["assistant"]
    assert server_service.commands == []


def test_ai_omitted_command_is_restored_as_separate_confirmation_action(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    command_repo = CommandRepository(conn)
    server_service = FakeServerService()
    command_service = CommandService(command_repo, server_service)
    handlers = ServerToolHandlers(
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=server_service,
        command_service=command_service,
    )
    registry = ToolRegistry()
    registry.register(
        "propose_server_command",
        handlers.propose_server_command,
        PROPOSE_SERVER_COMMAND,
    )
    fake_llm = FakeStopOnlyCommandLlm()
    assistant = AssistantService(fake_llm, registry, LlmRepository(conn))
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=ContextManager(chat_repo, summary_repo, attachment_repo),
        command_service=command_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "帮我把Aiden233的管理员取消，然后关闭服务器")

    assert [action["command"] for action in result["command_actions"]] == [
        "deop Aiden233",
        "stop",
    ]
    assert result["command_actions"][0]["guardrail_generated"] is True
    assert result["command_actions"][1]["source_tool"] == "propose_server_command"
    assert len(command_repo.list_recent(limit=5)) == 2
    assert server_service.commands == []


def test_streamed_high_risk_tool_result_emits_confirmation_action(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    command_repo = CommandRepository(conn)
    server_service = FakeServerService()
    command_service = CommandService(command_repo, server_service)
    handlers = ServerToolHandlers(
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=server_service,
        command_service=command_service,
    )
    registry = ToolRegistry()
    registry.register(
        "execute_server_command",
        handlers.execute_server_command,
        EXECUTE_SERVER_COMMAND,
    )
    fake_llm = FakeStreamingCommandLlm()
    assistant = AssistantService(fake_llm, registry, LlmRepository(conn))
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=ContextManager(chat_repo, summary_repo, attachment_repo),
        command_service=command_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    events = list(chat.stream_message(session_id, "执行会中断连接的维护操作"))
    action_events = [
        event for event in events if event.event_type == StreamEventType.COMMAND_ACTION
    ]

    assert len(action_events) == 1
    assert action_events[0].command_action["status"] == "confirmation_required"
    assert action_events[0].command_action["command"] == "stop"
    assert "确认卡片" in action_events[0].text
    assert server_service.commands == []
    stored_answer = chat_repo.list_messages(session_id)[-1]["content"]
    assert "确认卡片" in stored_answer
    assert "模型生成的后续说明" not in stored_answer
