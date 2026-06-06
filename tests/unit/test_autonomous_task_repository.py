from __future__ import annotations

import json
from pathlib import Path

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.autonomous_task_repository import AutonomousTaskRepository
from src.repositories.chat_repository import ChatRepository
from src.repositories.config_change_repository import ConfigChangeRepository


def test_autonomous_task_repository_records_task_steps_and_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    session_id = chat_repo.create_session("test")
    turn_id = chat_repo.create_turn(session_id, source="test")
    repo = AutonomousTaskRepository(conn)

    task_id = repo.create_task(
        session_id=session_id,
        initial_turn_id=turn_id,
        kind="minecraft_config",
        user_goal="自主把最大人数改到 30",
        metadata={"auto_apply_max_risk": "LOW"},
    )
    round_index = repo.increment_round(task_id)
    step_id = repo.create_step(task_id, round_index, "collect_context", {"a": 1})
    repo.finish_step(step_id, "completed", {"ok": True})
    repo.add_artifact(task_id, round_index, "llm_call", artifact_int_id=42)
    repo.add_artifact(task_id, round_index, "config_proposal", artifact_text_id="cfgp_test")
    repo.increment_llm_call_count(task_id, 2)
    repo.increment_tool_call_count(task_id, 1)

    task = repo.get_task(task_id)

    assert task is not None
    assert task["session_id"] == session_id
    assert task["initial_turn_id"] == turn_id
    assert task["current_round"] == 1
    assert task["llm_call_count"] == 2
    assert task["tool_call_count"] == 1
    assert task["metadata"]["auto_apply_max_risk"] == "LOW"
    assert repo.count_artifacts(task_id) == 2
    assert repo.count_artifacts(task_id, "llm_call") == 1


def test_find_pending_proposal_supports_text_artifact_id(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    chat_repo = ChatRepository(conn)
    session_id = chat_repo.create_session("test")
    turn_id = chat_repo.create_turn(session_id, source="test")
    repo = AutonomousTaskRepository(conn)
    proposal_repo = ConfigChangeRepository(conn)
    task_id = repo.create_task(
        session_id=session_id,
        initial_turn_id=turn_id,
        kind="minecraft_config",
        user_goal="自主把最大人数改到 30",
    )
    proposal_repo.create_proposal(
        proposal_id="cfgp_pending",
        session_id=session_id,
        user_request="自主把最大人数改到 30",
        relative_path="server.properties",
        before_hash="before",
        after_hash="after",
        before_content="max-players=20\n",
        after_content="max-players=30\n",
        diff_text="diff",
        changes=[{"key": "max-players", "old_value": "20", "new_value": "30"}],
        risk_level="LOW",
        restart_required=True,
        warnings=[],
    )
    repo.add_artifact(
        task_id,
        1,
        "config_proposal",
        artifact_text_id="cfgp_pending",
        metadata={"source": "test"},
    )

    pending = repo.find_pending_proposal(task_id)

    assert pending is not None
    assert pending["proposal_id"] == "cfgp_pending"
    assert pending["restart_required"] is True
    assert pending["metadata"]["source"] == "test"
    assert json.dumps(pending["changes"], ensure_ascii=False)
