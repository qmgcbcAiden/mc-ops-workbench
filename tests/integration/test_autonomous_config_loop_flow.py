from __future__ import annotations

import json
from pathlib import Path

from src.ai.assistant_service import AssistantService
from src.ai.context_manager import ContextManager
from src.ai.llm_client import LlmResponse
from src.ai.tool_builder import build_config_loop_tool_registry
from src.ai.tool_registry import ToolRegistry
from src.config.settings import PROJECT_ROOT
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.autonomous_task_repository import AutonomousTaskRepository
from src.repositories.chat_attachment_repository import ChatAttachmentRepository
from src.repositories.chat_repository import ChatRepository
from src.repositories.chat_summary_repository import ChatSummaryRepository
from src.repositories.config_change_repository import ConfigChangeRepository
from src.repositories.file_edit_repository import FileEditAuditRepository
from src.repositories.llm_repository import LlmRepository
from src.service.autonomous_config_loop_service import AutonomousConfigLoopService
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
        self.config_versioning_enabled = False
        self.config_version_repo_dir = PROJECT_ROOT / "data/config_versions/default/repo"
        self.config_auto_approve_max_risk = "LOW"
        self.config_redaction_version = "v1"
        self.autonomous_config_loop_enabled = True
        self.autonomous_config_loop_max_rounds = 3
        self.autonomous_config_loop_max_llm_calls = 8
        self.autonomous_config_loop_max_tool_calls = 20
        self.autonomous_config_loop_auto_apply_max_risk = "LOW"
        self.autonomous_config_loop_verify_runtime = False


class FakeAutonomousLlm:
    model = "fake-autonomous-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        joined = "\n".join(message.get("content", "") for message in messages)
        if self.calls == 1:
            assert "配置修改自主循环" in messages[0]["content"]
            assert "自主把最大人数改到 30" in joined
            tool_names = {tool["function"]["name"] for tool in tools or []}
            assert "propose_config_change" in tool_names
            assert "execute_server_command" not in tool_names
            session_id = json.loads(messages[-1]["content"])["session_id"]
            return LlmResponse(
                content="",
                model=self.model,
                tool_calls=[
                    {
                        "id": "call_auto_cfg",
                        "name": "propose_config_change",
                        "arguments": json.dumps(
                            {
                                "file": "server.properties",
                                "session_id": session_id,
                                "changes": [
                                    {
                                        "key": "max-players",
                                        "value": "30",
                                        "reason": "用户要求最大人数改到 30",
                                    }
                                ],
                                "user_request": "自主把最大人数改到 30，直到文件确认生效",
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            )
        assert any(message["role"] == "tool" for message in messages)
        return LlmResponse(content="已生成配置草案。", model=self.model)


class FakePlayerService:
    def get_online_players(self) -> dict:
        return {"online_count": 0, "players": []}


class FakeLogService:
    def list_recent(self, level: str | None = None, limit: int = 20) -> list[dict]:
        del level, limit
        return []


class FakeSystemService:
    def capture_metrics(self) -> dict:
        return {"cpu_percent": 0, "memory_percent": 0}


class FakeServerService:
    def get_status(self) -> dict:
        return {"state": "stopped", "online_players": 0}


def test_autonomous_config_loop_routes_before_single_config_flow_and_applies_low_risk(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "server.properties").write_text("max-players=20\n", encoding="utf-8")
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    settings = FakeSettings(server_dir)

    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    llm_repo = LlmRepository(conn)
    config_service = ConfigEditService(
        FileService(settings, edit_repo=FileEditAuditRepository(conn)),
        ConfigChangeRepository(conn),
        auto_approve_max_risk=settings.config_auto_approve_max_risk,
    )
    fake_llm = FakeAutonomousLlm()
    loop_registry = build_config_loop_tool_registry(
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=FakeServerService(),
        config_service=config_service,
    )
    autonomous_assistant = AssistantService(fake_llm, loop_registry, llm_repo)
    context = ContextManager(chat_repo, summary_repo, attachment_repo)
    loop_service = AutonomousConfigLoopService(
        task_repo=AutonomousTaskRepository(conn),
        assistant_service=autonomous_assistant,
        context_manager=context,
        config_edit_service=config_service,
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        server_service=FakeServerService(),
        player_service=FakePlayerService(),
        settings=settings,
    )
    chat = ChatService(
        chat_repository=chat_repo,
        player_service=FakePlayerService(),
        log_service=FakeLogService(),
        system_service=FakeSystemService(),
        llm_client=fake_llm,
        assistant_service=AssistantService(fake_llm, ToolRegistry(), llm_repo),
        context_manager=context,
        config_edit_service=config_service,
        autonomous_config_loop_service=loop_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
    )
    session_id = chat.create_session("test")

    result = chat.send_turn(session_id, "自主把最大人数改到 30，直到文件确认生效")

    assert result["source"] == "autonomous_config_loop"
    assert result["autonomous_task"]["status"] == "completed"
    assert "max-players=30" in (server_dir / "server.properties").read_text(encoding="utf-8")
    assert conn.execute("SELECT COUNT(*) FROM autonomous_tasks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM autonomous_task_steps").fetchone()[0] >= 4
    assert conn.execute(
        "SELECT COUNT(*) FROM autonomous_task_artifacts WHERE artifact_type = 'llm_call'"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM autonomous_task_artifacts WHERE artifact_type = 'tool_call'"
    ).fetchone()[0] == 1
    proposal = conn.execute("SELECT status, auto_approved FROM config_change_proposals").fetchone()
    assert proposal["status"] == "applied"
    assert proposal["auto_approved"] == 0
    assert result["config_proposal"] is None
