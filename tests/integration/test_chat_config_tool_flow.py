from __future__ import annotations

import json
from pathlib import Path

from src.ai.assistant_service import AssistantService
from src.ai.config_tool_handlers import ConfigToolHandlers
from src.ai.context_manager import ContextManager
from src.ai.llm_client import LlmResponse
from src.ai.stream_events import ChatStreamEvent, StreamEventType
from src.ai.tool_registry import ToolRegistry
from src.ai.tool_schemas import PROPOSE_CONFIG_CHANGE, READ_CONFIG_FILE
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.chat_attachment_repository import ChatAttachmentRepository
from src.repositories.chat_repository import ChatRepository
from src.repositories.chat_summary_repository import ChatSummaryRepository
from src.repositories.config_change_repository import ConfigChangeRepository
from src.repositories.llm_repository import LlmRepository
from src.service.chat_service import ChatService
from src.service.config_edit_service import ConfigEditService
from src.service.file_service import FileService


class FakeSettings:
    def __init__(self, server_dir: Path) -> None:
        self.mc_server_dir = server_dir
        self.mc_file_tree_max_depth = 32
        self.mc_file_preview_max_bytes = 1048576
        self.mc_editable_file_max_bytes = 1048576
        self.mc_config_backup_on_save = True


class FakeToolLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            return LlmResponse(
                content="",
                model=self.model,
                tool_calls=[
                    {
                        "id": "call_config_1",
                        "name": "propose_config_change",
                        "arguments": json.dumps(
                            {
                                "file": "server.properties",
                                "changes": [
                                    {
                                        "key": "difficulty",
                                        "value": "普通",
                                        "reason": "用户要求把难度调成普通",
                                    }
                                ],
                                "user_request": "请把难度调成普通",
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            )
        if self.calls == 3:
            assert tools is None
            joined = "\n".join(message.get("content", "") for message in messages)
            assert "config_apply" in joined
            assert '"status": "saved"' in joined
            return LlmResponse(
                content="配置已保存，难度已更新为 normal。",
                model=self.model,
            )
        assert any(message["role"] == "tool" for message in messages)
        return LlmResponse(
            content="已生成 server.properties 配置修改草案，等待确认后应用。",
            model=self.model,
            token_usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )


class FakeStreamingConfigLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(self, messages: list[dict], tools: list[dict] | None = None):
        self.calls += 1
        if self.calls == 1:
            tool_names = {tool["function"]["name"] for tool in tools or []}
            assert "propose_config_change" in tool_names
            yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_START)
            yield ChatStreamEvent(
                event_type=StreamEventType.TOOL_START,
                tool_name="propose_config_change",
            )
            yield ChatStreamEvent(
                event_type=StreamEventType.TOOL_RESULT,
                tool_name="propose_config_change",
                tool_arguments={
                    "id": "call_stream_config",
                    "raw": json.dumps(
                        {
                            "file": "server.properties",
                            "changes": [
                                {
                                    "key": "online-mode",
                                    "value": "false",
                                    "reason": "用户希望放宽进服校验",
                                }
                            ],
                            "user_request": "这个进服校验有点挡人，帮我处理",
                        },
                        ensure_ascii=False,
                    ),
                },
            )
            return
        assert any(message["role"] == "tool" for message in messages)
        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_START)
        yield ChatStreamEvent(
            event_type=StreamEventType.DELTA,
            text="模型后续说明不应覆盖本地配置草案提示。",
        )
        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_END)


class FakePirateJoinLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            assert "盗版玩家进不去" in messages[-1]["content"]
            return LlmResponse(
                content="",
                model=self.model,
                tool_calls=[
                    {
                        "id": "call_config_online_mode",
                        "name": "propose_config_change",
                        "arguments": json.dumps(
                            {
                                "file": "server.properties",
                                "changes": [
                                    {
                                        "key": "online-mode",
                                        "value": "false",
                                        "reason": "用户希望允许非正版玩家进入服务器",
                                    }
                                ],
                                "user_request": "我的服务器为什么盗版玩家进不去，帮我改一下",
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            )
        assert any(message["role"] == "tool" for message in messages)
        return LlmResponse(
            content=(
                "盗版玩家进不去通常是因为 server.properties 里 online-mode=true，"
                "服务端会要求正版验证。我已生成 online-mode=false 的高风险草案，"
                "需要你确认后才会写入。"
            ),
            model=self.model,
        )


class FakeReadConfigLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            tool_names = {tool["function"]["name"] for tool in tools or []}
            assert "read_config_file" in tool_names
            return LlmResponse(
                content="",
                model=self.model,
                tool_calls=[
                    {
                        "id": "call_read_config",
                        "name": "read_config_file",
                        "arguments": json.dumps({"file": "server.properties"}),
                    }
                ],
            )
        tool_message = next(message for message in messages if message["role"] == "tool")
        assert "rcon.password" not in tool_message["content"]
        assert "secret" not in tool_message["content"]
        return LlmResponse(
            content="我读取了安全配置快照，当前 max-players 是 10。",
            model=self.model,
        )


class FakePlayerService:
    def get_online_players(self) -> dict:
        return {"online_count": 0, "players": []}


class FakeLogService:
    def list_recent(self, level: str | None = None, limit: int = 20) -> list[dict]:
        return []


class FakeSystemService:
    def capture_metrics(self) -> dict:
        return {"cpu_percent": 0, "memory_percent": 0}


def test_chat_config_tool_flow_records_llm_tool_and_proposal(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "server.properties").write_text(
        "difficulty=easy\n", encoding="utf-8"
    )
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)

    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    llm_repo = LlmRepository(conn)
    config_service = ConfigEditService(
        FileService(FakeSettings(server_dir)),
        ConfigChangeRepository(conn),
    )
    handlers = ConfigToolHandlers(config_service)
    registry = ToolRegistry()
    registry.register(
        "propose_config_change",
        handlers.propose_config_change,
        PROPOSE_CONFIG_CHANGE,
    )
    fake_llm = FakeToolLlm()
    assistant = AssistantService(fake_llm, registry, llm_repo)
    context = ContextManager(chat_repo, summary_repo, attachment_repo)
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=context,
        config_edit_service=config_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "请把难度调成普通")

    assert result["source"] == "ai"
    assert result["config_proposal"]["changes"][0]["new_value"] == "normal"
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM config_change_proposals").fetchone()[0] == 1
    assert "difficulty=easy" in (server_dir / "server.properties").read_text(encoding="utf-8")

    applied = chat.apply_config_proposal(
        session_id,
        result["config_proposal"]["proposal_id"],
    )

    assert applied["status"] == "saved"
    assert applied["assistant"] == "配置已保存，难度已更新为 normal。"
    assert fake_llm.calls == 3
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 3
    messages = chat_repo.list_messages(session_id)
    assert messages[-2]["role"] == "tool"
    assert messages[-2]["tool_name"] == "apply_config_proposal"
    assert "config_apply" in messages[-2]["content"]
    assert messages[-1]["role"] == "assistant"
    assert "难度已更新" in messages[-1]["content"]


def test_streaming_config_tool_flow_emits_proposal_event(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "server.properties").write_text(
        "online-mode=true\n", encoding="utf-8"
    )
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)

    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    llm_repo = LlmRepository(conn)
    config_service = ConfigEditService(
        FileService(FakeSettings(server_dir)),
        ConfigChangeRepository(conn),
    )
    handlers = ConfigToolHandlers(config_service)
    registry = ToolRegistry()
    registry.register(
        "propose_config_change",
        handlers.propose_config_change,
        PROPOSE_CONFIG_CHANGE,
    )
    fake_llm = FakeStreamingConfigLlm()
    assistant = AssistantService(fake_llm, registry, llm_repo)
    context = ContextManager(chat_repo, summary_repo, attachment_repo)
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=context,
        config_edit_service=config_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    events = list(chat.stream_message(session_id, "这个进服校验有点挡人，帮我处理"))
    proposal_events = [
        event for event in events if event.event_type == StreamEventType.CONFIG_PROPOSAL
    ]

    assert len(proposal_events) == 1
    proposal = proposal_events[0].config_proposal
    assert proposal["risk_level"] == "HIGH"
    assert proposal["changes"][0]["key"] == "online-mode"
    assert proposal["changes"][0]["new_value"] == "false"
    assert proposal["turn_id"]
    assert "配置编辑器" in proposal_events[0].text
    assert "online-mode=true" in (server_dir / "server.properties").read_text(encoding="utf-8")
    stored_answer = chat_repo.list_messages(session_id)[-1]["content"]
    assert "配置编辑器" in stored_answer
    assert "模型后续说明" not in stored_answer


def test_chat_uses_llm_tool_call_for_config_issue_even_when_text_mentions_players(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "server.properties").write_text(
        "online-mode=true\n", encoding="utf-8"
    )
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)

    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    llm_repo = LlmRepository(conn)
    config_service = ConfigEditService(
        FileService(FakeSettings(server_dir)),
        ConfigChangeRepository(conn),
    )
    handlers = ConfigToolHandlers(config_service)
    registry = ToolRegistry()
    registry.register(
        "propose_config_change",
        handlers.propose_config_change,
        PROPOSE_CONFIG_CHANGE,
    )
    fake_llm = FakePirateJoinLlm()
    assistant = AssistantService(fake_llm, registry, llm_repo)
    context = ContextManager(chat_repo, summary_repo, attachment_repo)
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=context,
        config_edit_service=config_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "我的服务器为什么盗版玩家进不去，帮我改一下")

    assert result["source"] == "ai"
    assert fake_llm.calls == 2
    assert "online-mode=true" in (server_dir / "server.properties").read_text(encoding="utf-8")
    assert "正版验证" in result["assistant"]
    assert result["config_proposal"]["risk_level"] == "HIGH"
    assert result["config_proposal"]["changes"][0]["key"] == "online-mode"
    assert result["config_proposal"]["changes"][0]["new_value"] == "false"
    assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1


def test_chat_llm_can_read_safe_config_snapshot_with_tool_call(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "server.properties").write_text(
        "max-players=10\nrcon.password=secret\n", encoding="utf-8"
    )
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)

    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    llm_repo = LlmRepository(conn)
    config_service = ConfigEditService(
        FileService(FakeSettings(server_dir)),
        ConfigChangeRepository(conn),
    )
    handlers = ConfigToolHandlers(config_service)
    registry = ToolRegistry()
    registry.register(
        "read_config_file",
        handlers.read_config_file,
        READ_CONFIG_FILE,
    )
    fake_llm = FakeReadConfigLlm()
    assistant = AssistantService(fake_llm, registry, llm_repo)
    context = ContextManager(chat_repo, summary_repo, attachment_repo)
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=assistant,
        context_manager=context,
        config_edit_service=config_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "读取一下 server.properties 配置")

    assert result["source"] == "ai"
    assert "max-players" in result["assistant"]
    tool_result = conn.execute("SELECT result_json FROM tool_calls").fetchone()[0]
    assert "max-players=10" in tool_result
    assert "rcon.password" not in tool_result
    assert "secret" not in tool_result
