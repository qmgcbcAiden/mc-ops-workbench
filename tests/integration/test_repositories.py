from __future__ import annotations

import threading
from pathlib import Path

import pytest

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.chat_repository import ChatRepository
from src.repositories.command_repository import CommandRepository
from src.repositories.event_repository import EventRepository
from src.repositories.llm_repository import LlmRepository
from src.repositories.metric_repository import MetricRepository
from src.repositories.player_repository import PlayerRepository


@pytest.fixture()
def connection(tmp_path: Path):
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def test_chat_repository_creates_sessions_and_messages(connection) -> None:
    repository = ChatRepository(connection)

    session_id = repository.create_session("Ops")
    turn_id = repository.create_turn(session_id, source="local")
    message_id = repository.add_message(session_id, "user", "现在谁在线？", turn_id=turn_id)

    messages = repository.list_messages(session_id)
    sessions = repository.list_sessions()

    assert message_id
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["turn_id"] == turn_id
    assert messages[0]["content"] == "现在谁在线？"
    assert sessions[0]["id"] == session_id


def test_chat_repository_lists_recent_messages_before_returning_ascending(connection) -> None:
    repository = ChatRepository(connection)

    session_id = repository.create_session("Ops")
    for text in ("oldest", "middle", "newest"):
        turn_id = repository.create_turn(session_id, source="test")
        repository.add_message(session_id, "user", text, turn_id=turn_id)

    messages = repository.list_messages(session_id, limit=2)

    assert [message["content"] for message in messages] == ["middle", "newest"]


def test_chat_repository_derives_title_from_first_user_message(connection) -> None:
    repository = ChatRepository(connection)

    session_id = repository.create_session("server_ops")
    turn_id = repository.create_turn(session_id, source="test")
    repository.add_message(
        session_id,
        "user",
        "查看一下服务器当前状态",
        turn_id=turn_id,
    )

    sessions = repository.list_sessions()

    assert sessions[0]["title"] == "查看一下服务器当前状态"
    assert sessions[0]["visible_message_count"] == 1


def test_chat_repository_records_context_snapshot_items(connection) -> None:
    repository = ChatRepository(connection)

    session_id = repository.create_session("Ops")
    turn_id = repository.create_turn(session_id, source="ai")
    snapshot_id = repository.create_context_snapshot(
        session_id=session_id,
        turn_id=turn_id,
        model="fake-model",
        purpose="chat",
        max_chars=1000,
        total_chars=20,
        summary_id=None,
        items=[
            {
                "item_type": "system_prompt",
                "item_id": "system",
                "role": "system",
                "char_count": 10,
                "included_chars": 10,
                "truncated": False,
            },
            {
                "item_type": "current_user",
                "item_id": turn_id,
                "role": "user",
                "char_count": 10,
                "included_chars": 10,
                "truncated": False,
            },
        ],
    )

    assert snapshot_id > 0
    assert connection.execute(
        "SELECT COUNT(*) FROM chat_context_items WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()[0] == 2


def test_event_repository_deduplicates_by_raw_hash(connection) -> None:
    repository = EventRepository(connection)
    event = {
        "event_time": "2026-05-19T10:00:00+00:00",
        "level": "ERROR",
        "category": "Server thread",
        "player_id": None,
        "message": "Something failed",
        "raw_line": "[10:00:00 ERROR]: Something failed",
        "raw_hash": "same-hash",
    }

    assert repository.insert_event(event) is True
    assert repository.insert_event(event) is False

    errors = repository.list_recent(level="ERROR")
    search_results = repository.search("failed")

    assert len(errors) == 1
    assert len(search_results) == 1
    assert search_results[0]["raw_hash"] == "same-hash"


def test_event_repository_recent_order_uses_arrival_order_across_midnight(connection) -> None:
    repository = EventRepository(connection)
    for message, time_text, raw_hash in (
        ("Before midnight", "23:59:59", "before"),
        ("After midnight", "00:00:01", "after"),
    ):
        repository.insert_event({
            "event_time": time_text,
            "level": "INFO",
            "category": "Server thread",
            "player_id": None,
            "message": message,
            "raw_line": message,
            "raw_hash": raw_hash,
        })

    cursor = repository.latest_id() - 1

    assert repository.list_recent(limit=1)[0]["message"] == "After midnight"
    assert [event["message"] for event in repository.list_after_id(cursor)] == [
        "After midnight"
    ]


def test_metric_repository_inserts_and_lists_samples(connection) -> None:
    repository = MetricRepository(connection)

    sample_id = repository.insert_sample(
        {
            "captured_at": "2026-05-19T10:00:00+00:00",
            "cpu_percent": 12.5,
            "memory_percent": 60.0,
            "memory_used_mb": 1024.0,
            "memory_total_mb": 4096.0,
            "server_pid": 1234,
        }
    )

    samples = repository.list_recent()

    assert sample_id > 0
    assert samples[0]["cpu_percent"] == 12.5
    assert samples[0]["server_pid"] == 1234


def test_metric_repository_accepts_worker_thread_insert(connection) -> None:
    repository = MetricRepository(connection)
    errors: list[BaseException] = []

    def insert_from_worker() -> None:
        try:
            repository.insert_sample(
                {
                    "captured_at": "2026-05-19T10:00:01+00:00",
                    "cpu_percent": 18.0,
                    "memory_percent": 55.0,
                    "memory_used_mb": 1024.0,
                    "memory_total_mb": 4096.0,
                    "server_pid": 4321,
                }
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=insert_from_worker)
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert errors == []
    assert repository.list_recent(limit=1)[0]["server_pid"] == 4321


def test_player_repository_creates_snapshot_with_players(connection) -> None:
    repository = PlayerRepository(connection)

    snapshot_id = repository.create_snapshot(["Steve", "Alex"])
    snapshot = repository.get_snapshot(snapshot_id)
    latest = repository.get_latest_snapshot()

    assert snapshot is not None
    assert snapshot["online_count"] == 2
    assert snapshot["players"] == ["Alex", "Steve"]
    assert latest is not None
    assert latest["id"] == snapshot_id


def test_player_repository_persists_directory_cache(connection) -> None:
    repository = PlayerRepository(connection)

    repository.replace_player_directory_cache(
        players=[
            {
                "name": "Steve",
                "uuid": "12345678-abcd-1234-abcd-123456789012",
                "avatar_url": "https://minotar.net/helm/Steve/40.png",
                "is_operator": True,
                "operator_level": 4,
                "is_banned": False,
                "known_ips": ["10.0.0.5"],
                "sources": ["usercache", "ops"],
            },
            {
                "name": "BadGuy",
                "uuid": None,
                "avatar_url": "https://minotar.net/helm/BadGuy/40.png",
                "is_banned": True,
                "ban": {"reason": "griefing"},
                "known_ips": [],
                "sources": ["banned-players"],
            },
        ],
        banned_ips=[
            {
                "ip": "10.0.0.5",
                "players": ["Steve"],
                "reason": "alts",
                "expires": "forever",
                "mapping_source": "latest.log",
            }
        ],
        source_states=[
            {"source": "usercache.json", "mtime_ns": 1, "size_bytes": 32},
            {"source": "latest.log", "mtime_ns": 2, "size_bytes": 128},
        ],
    )

    cache = repository.get_player_directory_cache()
    states = repository.get_player_directory_source_states()

    assert cache["counts"] == {
        "players": 2,
        "operators": 1,
        "banned_players": 1,
        "banned_ips": 1,
    }
    players = {player["name"]: player for player in cache["players"]}
    assert players["Steve"]["known_ips"] == ["10.0.0.5"]
    assert players["Steve"]["is_operator"] is True
    assert players["BadGuy"]["ban"] == {"reason": "griefing"}
    assert cache["banned_ips"][0]["players"] == ["Steve"]
    assert states["latest.log"]["size_bytes"] == 128


def test_command_repository_records_audit_lifecycle(connection) -> None:
    repository = CommandRepository(connection)

    audit_id = repository.create_audit(
        command="stop",
        normalized_command="stop",
        risk_level="HIGH",
        requested_by="ui",
        confirmation_required=True,
        status="confirmation_required",
    )
    repository.mark_executed(audit_id, status="blocked", error_message="Not confirmed")

    audits = repository.list_recent()

    assert audits[0]["id"] == audit_id
    assert audits[0]["confirmation_required"] == 1
    assert audits[0]["status"] == "blocked"
    assert audits[0]["error_message"] == "Not confirmed"


def test_command_repository_transitions_only_expected_status_once(connection) -> None:
    repository = CommandRepository(connection)
    audit_id = repository.create_audit(
        command="op Aiden233",
        normalized_command="op Aiden233",
        risk_level="HIGH",
        requested_by="ui",
        confirmation_required=True,
        status="confirmation_required",
    )

    assert repository.transition_status(audit_id, "confirmation_required", "executing") is True
    assert repository.transition_status(audit_id, "confirmation_required", "executing") is False
    assert repository.list_recent(limit=1)[0]["status"] == "executing"


def test_llm_repository_records_llm_and_tool_calls(connection) -> None:
    repository = LlmRepository(connection)

    llm_call_id = repository.create_llm_call("chat", "qwen-plus")
    tool_call_id = repository.create_tool_call(
        llm_call_id,
        "get_online_players",
        {},
        provider_tool_call_id="call_provider_1",
    )
    repository.finish_tool_call(
        tool_call_id,
        "succeeded",
        {"online_count": 0, "players": []},
        latency_ms=7,
    )
    repository.finish_llm_call(
        llm_call_id,
        "succeeded",
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        latency_ms=123,
    )

    llm_row = connection.execute(
        "SELECT status, total_tokens, latency_ms FROM llm_calls WHERE id = ?",
        (llm_call_id,),
    ).fetchone()
    tool_row = connection.execute(
        """
        SELECT status, result_json, provider_tool_call_id, latency_ms, finished_at
        FROM tool_calls
        WHERE id = ?
        """,
        (tool_call_id,),
    ).fetchone()

    assert llm_row["status"] == "succeeded"
    assert llm_row["total_tokens"] == 15
    assert llm_row["latency_ms"] == 123
    assert tool_row["status"] == "succeeded"
    assert '"online_count": 0' in tool_row["result_json"]
    assert tool_row["provider_tool_call_id"] == "call_provider_1"
    assert tool_row["latency_ms"] == 7
    assert tool_row["finished_at"]
