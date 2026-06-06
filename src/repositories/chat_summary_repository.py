from __future__ import annotations

import sqlite3

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class ChatSummaryRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        session_id: str,
        summary: str,
        covered_message_id: str | None = None,
        previous_summary_id: int | None = None,
        covered_through_turn_index: int = 0,
        covered_through_message_index: int = 0,
        status: str = "active",
        source_llm_call_id: int | None = None,
    ) -> int:
        del covered_message_id
        now = utc_now_iso()
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO chat_session_summaries
                    (
                        session_id, previous_summary_id, covered_through_turn_index,
                        covered_through_message_index, summary, status,
                        source_llm_call_id, char_count, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    previous_summary_id,
                    covered_through_turn_index,
                    covered_through_message_index,
                    summary,
                    status,
                    source_llm_call_id,
                    len(summary or ""),
                    now,
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)

    def get_latest(self, session_id: str) -> dict | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT id, session_id, previous_summary_id, covered_through_turn_index,
                       covered_through_message_index, summary, status,
                       source_llm_call_id, char_count, created_at
                FROM chat_session_summaries
                WHERE session_id = ?
                ORDER BY covered_through_turn_index DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return row_to_dict(row) if row else None

    def list_for_session(self, session_id: str) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT id, session_id, previous_summary_id, covered_through_turn_index,
                       covered_through_message_index, summary, status,
                       source_llm_call_id, char_count, created_at
                FROM chat_session_summaries
                WHERE session_id = ?
                ORDER BY covered_through_turn_index ASC, created_at ASC
                """,
                (session_id,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]
