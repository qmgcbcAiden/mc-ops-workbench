from __future__ import annotations

from pathlib import Path

from src.ai.assistant_service import AssistantService
from src.ai.context_manager import ContextManager
from src.ai.llm_client import LlmResponse
from src.ai.prompts import SYSTEM_PROMPT_ASSISTANT
from src.ai.tool_registry import ToolRegistry
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.chat_attachment_repository import ChatAttachmentRepository
from src.repositories.chat_repository import ChatRepository
from src.repositories.chat_summary_repository import ChatSummaryRepository
from src.repositories.llm_repository import LlmRepository
from src.service.chat_service import ChatService


def test_context_manager_exposes_historical_tool_results_as_system_context(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    session_id = chat_repo.create_session("test")
    chat_repo.add_message(session_id, "user", "执行 op Aiden233")
    chat_repo.add_message(
        session_id,
        "tool",
        '{"interactive_action":"command_confirmation","result":{"status":"executed"}}',
        tool_name="execute_server_command",
    )
    context = ContextManager(chat_repo, summary_repo, attachment_repo)

    messages = context.build_messages(session_id, "结果怎么样？")

    tool_history = [
        message for message in messages
        if "历史工具结果：execute_server_command" in message["content"]
    ]
    assert tool_history
    assert tool_history[0]["role"] == "system"
    assert all(message["role"] != "tool" for message in messages)


def test_context_manager_uses_summary_boundary_and_recent_turns(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    session_id = chat_repo.create_session("test")
    for text in ("oldest", "middle", "newest"):
        turn_id = chat_repo.create_turn(session_id, source="test")
        chat_repo.add_message(session_id, "user", text, turn_id=turn_id)
        chat_repo.update_turn(turn_id, status="completed", completed=True)
    summary_repo.create(
        session_id=session_id,
        summary="oldest 已经进入摘要",
        covered_through_turn_index=1,
        covered_through_message_index=1,
    )
    current_turn_id = chat_repo.create_turn(session_id, source="pending")
    chat_repo.add_message(session_id, "user", "current", turn_id=current_turn_id)
    context = ContextManager(
        chat_repo,
        summary_repo,
        attachment_repo,
        recent_messages_limit=2,
    )

    built = context.build_context(session_id, "current", turn_id=current_turn_id)
    joined = "\n".join(message["content"] for message in built.messages)

    assert "oldest 已经进入摘要" in joined
    assert "newest" in joined
    assert "middle" not in joined
    assert "oldest\n" not in joined
    assert conn.execute(
        "SELECT COUNT(*) FROM chat_context_items WHERE snapshot_id = ?",
        (built.snapshot_id,),
    ).fetchone()[0] == built.stats["item_count"]


def test_context_manager_prefers_compressed_attachment_content(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    session_id = chat_repo.create_session("test")
    turn_id = chat_repo.create_turn(session_id, source="pending")
    attachment_id = attachment_repo.create(
        session_id=session_id,
        turn_id=turn_id,
        kind="log_selection",
        label="日志片段",
        content="raw log content that should not be sent",
        compressed_content="compressed log summary",
    )
    context = ContextManager(chat_repo, summary_repo, attachment_repo)

    built = context.build_context(
        session_id,
        "分析一下",
        turn_id=turn_id,
        attachment_ids=[attachment_id],
    )
    joined = "\n".join(message["content"] for message in built.messages)

    assert "compressed log summary" in joined
    assert "raw log content" not in joined


def test_context_manager_accepts_custom_system_prompt(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    session_id = chat_repo.create_session("test")
    context = ContextManager(chat_repo, summary_repo, attachment_repo)

    built = context.build_context(
        session_id=session_id,
        user_message="自主配置任务",
        purpose="autonomous_config_loop",
        system_prompt="CUSTOM AUTONOMOUS PROMPT",
    )

    assert built.messages[0]["content"] == "CUSTOM AUTONOMOUS PROMPT"
    assert SYSTEM_PROMPT_ASSISTANT not in built.messages[0]["content"]


class SummaryLlm:
    model = "fake-summary-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        assert tools is None
        assert "本次新增对话" in messages[-1]["content"]
        return LlmResponse(content="滚动摘要内容", model=self.model)


class NoopPlayerService:
    def get_online_players(self) -> dict:
        return {"online_count": 0, "players": []}


class NoopLogService:
    def list_recent(self, level: str | None = None, limit: int = 20) -> list[dict]:
        del level, limit
        return []


class NoopSystemService:
    def capture_metrics(self) -> dict:
        return {"cpu_percent": 0, "memory_percent": 0}


def test_chat_service_summary_records_coverage_and_llm_call(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    fake_llm = SummaryLlm()
    context = ContextManager(
        chat_repo,
        summary_repo,
        attachment_repo,
        recent_messages_limit=2,
        summary_trigger_messages=1,
    )
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=NoopPlayerService(),
        log_service=NoopLogService(),
        system_service=NoopSystemService(),
        llm_client=fake_llm,
        assistant_service=AssistantService(fake_llm, ToolRegistry(), LlmRepository(conn)),
        context_manager=context,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")
    for idx in range(3):
        turn_id = chat_repo.create_turn(session_id, source="test")
        chat_repo.add_message(session_id, "user", f"user {idx}", turn_id=turn_id)
        chat_repo.add_message(session_id, "assistant", f"assistant {idx}", turn_id=turn_id)
        chat_repo.update_turn(turn_id, status="completed", completed=True)

    chat._maybe_summarize(session_id)
    chat._maybe_summarize(session_id)

    summaries = summary_repo.list_for_session(session_id)
    assert len(summaries) == 1
    assert summaries[0]["covered_through_turn_index"] == 2
    assert summaries[0]["source_llm_call_id"] is not None
    assert conn.execute("SELECT COUNT(*) FROM llm_calls WHERE purpose = 'summary'").fetchone()[0] == 1
