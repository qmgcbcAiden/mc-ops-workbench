from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class AutonomousTaskRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_task(
        self,
        session_id: str,
        initial_turn_id: str,
        kind: str,
        user_goal: str,
        max_rounds: int = 3,
        max_llm_calls: int = 8,
        max_tool_calls: int = 20,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        task_id = f"auto_cfg_{uuid4().hex[:16]}"
        now = utc_now_iso()
        with locked_connection(self.connection):
            self.connection.execute(
                """
                INSERT INTO autonomous_tasks
                    (
                        id, session_id, initial_turn_id, kind, user_goal, status,
                        max_rounds, max_llm_calls, max_tool_calls, created_at,
                        updated_at, metadata_json
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    session_id,
                    initial_turn_id,
                    kind,
                    user_goal,
                    "created",
                    max_rounds,
                    max_llm_calls,
                    max_tool_calls,
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            self.connection.commit()
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT *
                FROM autonomous_tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        return _inflate_task(row_to_dict(row)) if row else None

    def list_tasks(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT *
                FROM autonomous_tasks
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [_inflate_task(row_to_dict(row)) for row in rows]

    def update_status(
        self,
        task_id: str,
        status: str,
        stopped_reason: str | None = None,
        final_summary: str | None = None,
        completed: bool = False,
    ) -> None:
        now = utc_now_iso()
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE autonomous_tasks
                SET status = ?,
                    stopped_reason = COALESCE(?, stopped_reason),
                    final_summary = COALESCE(?, final_summary),
                    updated_at = ?,
                    completed_at = CASE WHEN ? THEN ? ELSE completed_at END
                WHERE id = ?
                """,
                (
                    status,
                    stopped_reason,
                    final_summary,
                    now,
                    1 if completed else 0,
                    now,
                    task_id,
                ),
            )
            self.connection.commit()

    def increment_round(self, task_id: str) -> int:
        now = utc_now_iso()
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE autonomous_tasks
                SET current_round = current_round + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, task_id),
            )
            row = self.connection.execute(
                "SELECT current_round FROM autonomous_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            self.connection.commit()
        return int(row["current_round"]) if row else 0

    def create_step(
        self,
        task_id: str,
        round_index: int,
        step_type: str,
        input_json: dict[str, Any] | None = None,
        status: str = "started",
    ) -> int:
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO autonomous_task_steps
                    (task_id, round_index, step_type, status, input_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    round_index,
                    step_type,
                    status,
                    json.dumps(input_json or {}, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)

    def finish_step(
        self,
        step_id: int,
        status: str,
        output_json: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE autonomous_task_steps
                SET status = ?,
                    output_json = ?,
                    error_message = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(output_json or {}, ensure_ascii=False, default=str),
                    error_message,
                    utc_now_iso(),
                    step_id,
                ),
            )
            self.connection.commit()

    def add_artifact(
        self,
        task_id: str,
        round_index: int,
        artifact_type: str,
        artifact_text_id: str | None = None,
        artifact_int_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO autonomous_task_artifacts
                    (
                        task_id, round_index, artifact_type, artifact_text_id,
                        artifact_int_id, metadata_json, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    round_index,
                    artifact_type,
                    artifact_text_id,
                    artifact_int_id,
                    json.dumps(metadata or {}, ensure_ascii=False, default=str),
                    utc_now_iso(),
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)

    def count_artifacts(self, task_id: str, artifact_type: str | None = None) -> int:
        params: list[Any] = [task_id]
        where = "task_id = ?"
        if artifact_type:
            where += " AND artifact_type = ?"
            params.append(artifact_type)
        with locked_connection(self.connection):
            row = self.connection.execute(
                f"SELECT COUNT(*) FROM autonomous_task_artifacts WHERE {where}",
                tuple(params),
            ).fetchone()
        return int(row[0])

    def find_pending_proposal(self, task_id: str) -> dict[str, Any] | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT
                    a.task_id,
                    a.round_index,
                    a.artifact_text_id AS proposal_id,
                    a.metadata_json,
                    p.status,
                    p.relative_path,
                    p.risk_level,
                    p.restart_required,
                    p.changes_json
                FROM autonomous_task_artifacts a
                JOIN config_change_proposals p ON p.id = a.artifact_text_id
                WHERE a.task_id = ?
                  AND a.artifact_type = 'config_proposal'
                  AND p.status = 'pending'
                ORDER BY a.created_at DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        if not row:
            return None
        data = row_to_dict(row)
        data["restart_required"] = bool(data.get("restart_required"))
        data["metadata"] = _loads(data.pop("metadata_json", None), {})
        data["changes"] = _loads(data.pop("changes_json", None), [])
        return data

    def increment_llm_call_count(self, task_id: str, count: int = 1) -> None:
        self._increment_count(task_id, "llm_call_count", count)

    def increment_tool_call_count(self, task_id: str, count: int = 1) -> None:
        self._increment_count(task_id, "tool_call_count", count)

    def _increment_count(self, task_id: str, column: str, count: int) -> None:
        if column not in {"llm_call_count", "tool_call_count"}:
            raise ValueError(f"Unsupported counter: {column}")
        now = utc_now_iso()
        with locked_connection(self.connection):
            self.connection.execute(
                f"""
                UPDATE autonomous_tasks
                SET {column} = {column} + ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (count, now, task_id),
            )
            self.connection.commit()


def _inflate_task(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = _loads(row.get("metadata_json"), {})
    return row


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
