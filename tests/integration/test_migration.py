from __future__ import annotations

import shutil
from pathlib import Path

from src.db.connection import get_connection
from src.db.migrate import MIGRATIONS_DIR
from src.db.migrate import get_user_version, run_migrations


EXPECTED_TABLES = {
    "app_settings",
    "chat_sessions",
    "chat_turns",
    "chat_messages",
    "chat_session_summaries",
    "chat_attachments",
    "chat_context_snapshots",
    "chat_context_items",
    "log_analysis_results",
    "file_edit_audits",
    "server_events",
    "player_snapshots",
    "player_snapshot_items",
    "player_directory_players",
    "player_directory_banned_ips",
    "player_directory_sync_state",
    "player_ai_settings",
    "player_ai_access_entries",
    "player_ai_conversations",
    "metrics_samples",
    "command_audits",
    "llm_calls",
    "tool_calls",
    "server_runtime_events",
    "java_environment_audits",
    "addon_scan_runs",
    "addon_assets",
    "addon_diagnostics",
    "addon_remediation_proposals",
    "addon_knowledge_cache",
    "external_knowledge_requests",
    "config_change_proposals",
    "config_version_commits",
    "autonomous_tasks",
    "autonomous_task_steps",
    "autonomous_task_artifacts",
}


def test_run_migrations_creates_schema_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"

    assert run_migrations(db_path) == 13
    assert run_migrations(db_path) == 13

    connection = get_connection(db_path)
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()
        }

        assert EXPECTED_TABLES.issubset(tables)
        tool_call_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(tool_calls)").fetchall()
        }

        config_proposal_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(config_change_proposals)").fetchall()
        }
        chat_message_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(chat_messages)").fetchall()
        }
        llm_call_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(llm_calls)").fetchall()
        }
        autonomous_task_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(autonomous_tasks)").fetchall()
        }
        autonomous_artifact_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(autonomous_task_artifacts)").fetchall()
        }
        java_audit_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(java_environment_audits)").fetchall()
        }
        addon_diagnostic_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(addon_diagnostics)").fetchall()
        }

        assert get_user_version(connection) == 13
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert {
            "provider_tool_call_id",
            "finished_at",
            "latency_ms",
            "error_type",
        }.issubset(tool_call_columns)
        assert {
            "auto_approved",
            "approval_policy",
            "version_commit_id",
            "version_status",
            "version_error_message",
            "redaction_version",
        }.issubset(config_proposal_columns)
        assert {
            "turn_id",
            "message_index",
            "visibility",
            "content_type",
            "metadata_json",
            "char_count",
        }.issubset(chat_message_columns)
        assert {
            "session_id",
            "turn_id",
            "context_snapshot_id",
        }.issubset(llm_call_columns)
        assert {
            "session_id",
            "initial_turn_id",
            "current_round",
            "llm_call_count",
            "tool_call_count",
        }.issubset(autonomous_task_columns)
        assert {
            "artifact_text_id",
            "artifact_int_id",
        }.issubset(autonomous_artifact_columns)
        assert {
            "action",
            "status",
            "minecraft_version",
            "required_java_major",
            "selected_java_path",
            "metadata_json",
        }.issubset(java_audit_columns)
        assert {
            "severity",
            "category",
            "evidence_type",
            "confidence",
            "affected_files_json",
            "suggested_actions_json",
        }.issubset(addon_diagnostic_columns)
    finally:
        connection.close()


def test_v8_rebuild_clears_chat_history_but_preserves_runtime_data(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    migrations_v7 = tmp_path / "migrations_v7"
    migrations_v7.mkdir()
    for path in MIGRATIONS_DIR.glob("00[1-7]_*.sql"):
        shutil.copy(path, migrations_v7 / path.name)

    assert run_migrations(db_path, migrations_v7) == 7
    connection = get_connection(db_path)
    try:
        connection.execute(
            "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("s1", "old", "now", "now"),
        )
        connection.execute(
            """
            INSERT INTO chat_messages (id, session_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("m1", "s1", "user", "old chat", "now"),
        )
        connection.execute(
            """
            INSERT INTO server_events (message, raw_line, raw_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("keep", "keep", "keep-hash", "now"),
        )
        connection.commit()
    finally:
        connection.close()

    assert run_migrations(db_path) == 13
    connection = get_connection(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM server_events").fetchone()[0] == 1
    finally:
        connection.close()
