from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.db.connection import locked_connection
from src.repositories._time import utc_now_iso


class LlmRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_llm_call(
        self,
        purpose: str,
        model: str,
        status: str = "started",
        session_id: str | None = None,
        turn_id: str | None = None,
        context_snapshot_id: int | None = None,
    ) -> int:
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO llm_calls
                    (session_id, turn_id, context_snapshot_id, purpose, model, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_id,
                    context_snapshot_id,
                    purpose,
                    model,
                    status,
                    utc_now_iso(),
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)

    def finish_llm_call(
        self,
        call_id: int,
        status: str,
        token_usage: dict | None = None,
        latency_ms: int | None = None,
        error_message: str | None = None,
    ) -> None:
        token_usage = token_usage or {}
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE llm_calls
                SET status = ?, prompt_tokens = ?, completion_tokens = ?, total_tokens = ?,
                    latency_ms = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    status,
                    token_usage.get("prompt_tokens"),
                    token_usage.get("completion_tokens"),
                    token_usage.get("total_tokens"),
                    latency_ms,
                    error_message,
                    call_id,
                ),
            )
            self.connection.commit()

    def create_tool_call(
        self,
        llm_call_id: int | None,
        tool_name: str,
        arguments_json: str | dict[str, Any],
        status: str = "started",
        provider_tool_call_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        context_snapshot_id: int | None = None,
    ) -> int:
        if isinstance(arguments_json, dict):
            arguments_json = json.dumps(arguments_json, ensure_ascii=False)

        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO tool_calls
                    (
                        llm_call_id, session_id, turn_id, context_snapshot_id,
                        tool_name, arguments_json, status, provider_tool_call_id, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    llm_call_id,
                    session_id,
                    turn_id,
                    context_snapshot_id,
                    tool_name,
                    arguments_json,
                    status,
                    provider_tool_call_id,
                    utc_now_iso(),
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)

    def finish_tool_call(
        self,
        tool_call_id: int,
        status: str,
        result_json: str | dict[str, Any] | None = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
        error_type: str | None = None,
    ) -> None:
        if isinstance(result_json, dict):
            result_json = json.dumps(result_json, ensure_ascii=False)

        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE tool_calls
                SET status = ?, result_json = ?, error_message = ?,
                    latency_ms = ?, error_type = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    result_json,
                    error_message,
                    latency_ms,
                    error_type,
                    utc_now_iso(),
                    tool_call_id,
                ),
            )
            self.connection.commit()
