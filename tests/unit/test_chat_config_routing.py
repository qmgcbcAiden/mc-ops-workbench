from __future__ import annotations

from pathlib import Path

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.chat_repository import ChatRepository
from src.repositories.config_change_repository import ConfigChangeRepository
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


class FakePlayerService:
    def get_online_players(self) -> dict:
        return {"online_count": 0, "players": []}


class FakeLogService:
    def list_recent(self, level: str | None = None, limit: int = 20) -> list[dict]:
        return []


class FakeSystemService:
    def capture_metrics(self) -> dict:
        return {"cpu_percent": 0, "memory_percent": 0}


def _chat_with_repo(tmp_path: Path) -> tuple[ChatService, ChatRepository, Path]:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "server.properties").write_text(
        "online-mode=true\nmax-players=10\npvp=true\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    config_service = ConfigEditService(
        FileService(FakeSettings(server_dir)),
        ConfigChangeRepository(conn),
    )
    return (
        ChatService(
            chat_repository=chat_repo,
            player_service=FakePlayerService(),
            log_service=FakeLogService(),
            system_service=FakeSystemService(),
            config_edit_service=config_service,
        ),
        chat_repo,
        server_dir,
    )


def _chat(tmp_path: Path) -> tuple[ChatService, Path]:
    chat, _chat_repo, server_dir = _chat_with_repo(tmp_path)
    return chat, server_dir


def test_chat_routes_pirated_player_join_issue_to_online_mode_proposal(tmp_path: Path) -> None:
    chat, server_dir = _chat(tmp_path)
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "我的服务器为什么盗版玩家进不去，帮我改一下")

    proposal = result["config_proposal"]
    assert result["tool_name"] == "propose_config_change"
    assert proposal["risk_level"] == "HIGH"
    assert proposal["changes"][0]["key"] == "online-mode"
    assert proposal["changes"][0]["new_value"] == "false"
    assert "配置编辑器中采纳或拒绝" in result["assistant"]
    assert "online-mode=true" in (server_dir / "server.properties").read_text(encoding="utf-8")


def test_chat_still_routes_explicit_online_player_question_to_player_tool(tmp_path: Path) -> None:
    chat, _server_dir = _chat(tmp_path)
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "现在有哪些玩家在线？")

    assert result["tool_name"] == "get_online_players"
    assert result.get("config_proposal") is None


def test_chat_routes_spaced_pvp_typo_to_config_proposal(tmp_path: Path) -> None:
    chat, server_dir = _chat(tmp_path)
    session_id = chat.create_session("test")

    result = chat.send_message(session_id, "关掉pv p")

    proposal = result["config_proposal"]
    assert result["tool_name"] == "propose_config_change"
    assert proposal["changes"][0]["key"] == "pvp"
    assert proposal["changes"][0]["new_value"] == "false"
    assert "pvp=true" in (server_dir / "server.properties").read_text(encoding="utf-8")


def test_apply_config_proposal_records_tool_result_for_llm_context(tmp_path: Path) -> None:
    chat, chat_repo, server_dir = _chat_with_repo(tmp_path)
    session_id = chat.create_session("test")
    proposal = chat.send_message(session_id, "关掉pvp")["config_proposal"]

    result = chat.apply_config_proposal(
        session_id,
        proposal["proposal_id"],
        high_risk_confirmed=False,
    )

    assert result["status"] == "saved"
    assert result["tool_message_id"]
    assert "配置草案" in result["assistant"]
    assert "pvp=false" in (server_dir / "server.properties").read_text(encoding="utf-8")
    messages = chat_repo.list_messages(session_id)
    tool_message = messages[-2]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_name"] == "apply_config_proposal"
    assert "config_apply" in tool_message["content"]
    assert proposal["proposal_id"] in tool_message["content"]
    assert messages[-1]["role"] == "assistant"
    assert "配置草案" in messages[-1]["content"]


def test_reject_config_proposal_records_feedback_without_writing_file(tmp_path: Path) -> None:
    chat, chat_repo, server_dir = _chat_with_repo(tmp_path)
    session_id = chat.create_session("test")
    proposal = chat.send_message(session_id, "关掉pvp")["config_proposal"]

    result = chat.reject_config_proposal(session_id, proposal["proposal_id"])

    assert result["status"] == "rejected"
    assert "pvp=true" in (server_dir / "server.properties").read_text(encoding="utf-8")
    messages = chat_repo.list_messages(session_id)
    assert messages[-2]["tool_name"] == "reject_config_proposal"
    assert "config_reject" in messages[-2]["content"]
    assert messages[-1]["role"] == "assistant"
