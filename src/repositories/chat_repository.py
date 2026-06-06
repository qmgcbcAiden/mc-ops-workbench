from __future__ import annotations

import json
import re
import sqlite3
from uuid import uuid4

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class ChatRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_session(self, title: str | None = None) -> str:
        session_id = str(uuid4())
        now = utc_now_iso()
        with locked_connection(self.connection):
            self.connection.execute(
                """
                INSERT INTO chat_sessions (id, title, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, title, "active", now, now),
            )
            self.connection.commit()
        return session_id

    def touch_session(self, session_id: str, last_turn_at: str | None = None) -> None:
        now = utc_now_iso()
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE chat_sessions
                SET updated_at = ?, last_turn_at = COALESCE(?, last_turn_at)
                WHERE id = ?
                """,
                (now, last_turn_at, session_id),
            )
            self.connection.commit()

    def list_sessions(self, limit: int = 20) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT
                    s.id,
                    s.title,
                    s.status,
                    s.created_at,
                    s.updated_at,
                    s.last_turn_at,
                    (
                        SELECT COUNT(*)
                        FROM chat_messages m
                        WHERE m.session_id = s.id
                          AND m.visibility = 'visible'
                    ) AS visible_message_count,
                    (
                        SELECT m.content
                        FROM chat_messages m
                        WHERE m.session_id = s.id
                          AND m.role = 'user'
                          AND m.visibility = 'visible'
                        ORDER BY m.created_at ASC, m.message_index ASC
                        LIMIT 1
                    ) AS first_user_message
                FROM chat_sessions s
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        sessions = [row_to_dict(row) for row in rows]
        for session in sessions:
            session["title"] = _display_session_title(session)
        return sessions

    def create_turn(
        self,
        session_id: str,
        source: str | None = None,
        status: str = "started",
    ) -> str:
        turn_id = str(uuid4())
        now = utc_now_iso()
        with locked_connection(self.connection):
            row = self.connection.execute(
                "SELECT COALESCE(MAX(turn_index), 0) + 1 FROM chat_turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            turn_index = int(row[0])
            self.connection.execute(
                """
                INSERT INTO chat_turns
                    (id, session_id, turn_index, status, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (turn_id, session_id, turn_index, status, source, now),
            )
            self.connection.execute(
                "UPDATE chat_sessions SET updated_at = ?, last_turn_at = ? WHERE id = ?",
                (now, now, session_id),
            )
            self.connection.commit()
        return turn_id

    def update_turn(
        self,
        turn_id: str,
        status: str | None = None,
        source: str | None = None,
        completed: bool = False,
    ) -> None:
        turn = self.get_turn(turn_id)
        if turn is None:
            return
        now = utc_now_iso()
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE chat_turns
                SET status = COALESCE(?, status),
                    source = COALESCE(?, source),
                    completed_at = CASE WHEN ? THEN ? ELSE completed_at END
                WHERE id = ?
                """,
                (status, source, 1 if completed else 0, now, turn_id),
            )
            self.connection.execute(
                "UPDATE chat_sessions SET updated_at = ?, last_turn_at = ? WHERE id = ?",
                (now, now, turn["session_id"]),
            )
            self.connection.commit()

    def get_turn(self, turn_id: str) -> dict | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT id, session_id, turn_index, status, source, created_at, completed_at
                FROM chat_turns
                WHERE id = ?
                """,
                (turn_id,),
            ).fetchone()
        return row_to_dict(row) if row else None

    def get_latest_turn(self, session_id: str) -> dict | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT id, session_id, turn_index, status, source, created_at, completed_at
                FROM chat_turns
                WHERE session_id = ?
                ORDER BY turn_index DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return row_to_dict(row) if row else None

    def list_turns(
        self,
        session_id: str,
        limit: int = 50,
        after_turn_index: int | None = None,
    ) -> list[dict]:
        params: list[object] = [session_id]
        where = "session_id = ?"
        if after_turn_index is not None:
            where += " AND turn_index > ?"
            params.append(after_turn_index)
        params.append(limit)
        with locked_connection(self.connection):
            rows = self.connection.execute(
                f"""
                SELECT id, session_id, turn_index, status, source, created_at, completed_at
                FROM chat_turns
                WHERE {where}
                ORDER BY turn_index DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [row_to_dict(row) for row in reversed(rows)]

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        turn_id: str | None = None,
        visibility: str = "visible",
        content_type: str = "text",
        metadata: dict | None = None,
    ) -> str:
        if turn_id is None:
            turn_id = self.create_turn(session_id, source="legacy")
        message_id = str(uuid4())
        now = utc_now_iso()
        with locked_connection(self.connection):
            row = self.connection.execute(
                "SELECT COALESCE(MAX(message_index), 0) + 1 FROM chat_messages WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            message_index = int(row[0])
            self.connection.execute(
                """
                INSERT INTO chat_messages
                    (
                        id, session_id, turn_id, message_index, role, visibility,
                        content_type, content, tool_name, tool_call_id, metadata_json,
                        char_count, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    session_id,
                    turn_id,
                    message_index,
                    role,
                    visibility,
                    content_type,
                    content,
                    tool_name,
                    tool_call_id,
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                    len(content or ""),
                    now,
                ),
            )
            self.connection.execute(
                "UPDATE chat_sessions SET updated_at = ?, last_turn_at = ? WHERE id = ?",
                (now, now, session_id),
            )
            if role == "user" and visibility == "visible":
                _maybe_update_session_title(
                    self.connection,
                    session_id=session_id,
                    content=content,
                )
            self.connection.commit()
        return message_id

    def list_messages(
        self,
        session_id: str,
        limit: int = 50,
        include_context_only: bool = True,
    ) -> list[dict]:
        visibility_clause = "" if include_context_only else "AND visibility = 'visible'"
        with locked_connection(self.connection):
            rows = self.connection.execute(
                f"""
                SELECT id, session_id, turn_id, message_index, role, visibility,
                       content_type, content, tool_name, tool_call_id, metadata_json,
                       char_count, created_at
                FROM (
                    SELECT id, session_id, turn_id, message_index, role, visibility,
                           content_type, content, tool_name, tool_call_id, metadata_json,
                           char_count, created_at
                    FROM chat_messages
                    WHERE session_id = ? {visibility_clause}
                    ORDER BY created_at DESC, message_index DESC
                    LIMIT ?
                )
                ORDER BY created_at ASC, message_index ASC
                """,
                (session_id, limit),
            ).fetchall()
        return [_message_row_to_dict(row) for row in rows]

    def list_turn_messages(
        self,
        turn_id: str,
        include_context_only: bool = True,
    ) -> list[dict]:
        visibility_clause = "" if include_context_only else "AND visibility = 'visible'"
        with locked_connection(self.connection):
            rows = self.connection.execute(
                f"""
                SELECT id, session_id, turn_id, message_index, role, visibility,
                       content_type, content, tool_name, tool_call_id, metadata_json,
                       char_count, created_at
                FROM chat_messages
                WHERE turn_id = ? {visibility_clause}
                ORDER BY message_index ASC
                """,
                (turn_id,),
            ).fetchall()
        return [_message_row_to_dict(row) for row in rows]

    def get_session_view(self, session_id: str, limit_turns: int = 50) -> dict:
        turns = self.list_turns(session_id, limit=limit_turns)
        with locked_connection(self.connection):
            session_row = self.connection.execute(
                """
                SELECT id, title, status, created_at, updated_at, last_turn_at
                FROM chat_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        visible_turns: list[dict] = []
        for turn in turns:
            item = dict(turn)
            item["messages"] = self.list_turn_messages(
                turn["id"],
                include_context_only=False,
            )
            visible_turns.append(item)
        return {
            "session": row_to_dict(session_row) if session_row else None,
            "turns": visible_turns,
        }

    def latest_message_position(self, session_id: str) -> tuple[int, int]:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT t.turn_index, m.message_index
                FROM chat_messages m
                JOIN chat_turns t ON t.id = m.turn_id
                WHERE m.session_id = ?
                ORDER BY t.turn_index DESC, m.message_index DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return (0, 0)
        return (int(row["turn_index"]), int(row["message_index"]))

    def list_messages_for_summary(
        self,
        session_id: str,
        after_turn_index: int,
        through_turn_index: int,
    ) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT m.id, m.session_id, m.turn_id, t.turn_index, m.message_index,
                       m.role, m.visibility, m.content_type, m.content, m.tool_name,
                       m.tool_call_id, m.char_count, m.created_at
                FROM chat_messages m
                JOIN chat_turns t ON t.id = m.turn_id
                WHERE m.session_id = ?
                  AND t.turn_index > ?
                  AND t.turn_index <= ?
                  AND m.visibility IN ('visible', 'context_only')
                ORDER BY t.turn_index ASC, m.message_index ASC
                """,
                (session_id, after_turn_index, through_turn_index),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def create_context_snapshot(
        self,
        session_id: str,
        turn_id: str | None,
        model: str | None,
        purpose: str,
        max_chars: int,
        total_chars: int,
        summary_id: int | None,
        items: list[dict],
    ) -> int:
        now = utc_now_iso()
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO chat_context_snapshots
                    (session_id, turn_id, model, purpose, max_chars, total_chars, summary_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, turn_id, model, purpose, max_chars, total_chars, summary_id, now),
            )
            snapshot_id = int(cursor.lastrowid)
            for index, item in enumerate(items, start=1):
                self.connection.execute(
                    """
                    INSERT INTO chat_context_items
                        (
                            snapshot_id, item_order, item_type, item_id, role,
                            char_count, included_chars, truncated, created_at
                        )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        index,
                        item["item_type"],
                        item.get("item_id"),
                        item["role"],
                        int(item.get("char_count", 0)),
                        int(item.get("included_chars", 0)),
                        1 if item.get("truncated") else 0,
                        now,
                    ),
                )
            self.connection.commit()
        return snapshot_id


def _maybe_update_session_title(
    connection: sqlite3.Connection,
    session_id: str,
    content: str,
) -> None:
    row = connection.execute(
        """
        SELECT
            title,
            (
                SELECT COUNT(*)
                FROM chat_messages
                WHERE session_id = ?
                  AND role = 'user'
                  AND visibility = 'visible'
            ) AS user_message_count
        FROM chat_sessions
        WHERE id = ?
        """,
        (session_id, session_id),
    ).fetchone()
    if row is None:
        return
    if int(row["user_message_count"] or 0) != 1:
        return
    if not _is_generic_session_title(row["title"]):
        return
    connection.execute(
        "UPDATE chat_sessions SET title = ? WHERE id = ?",
        (_title_from_message(content), session_id),
    )


def _display_session_title(session: dict) -> str:
    title = session.get("title")
    if not _is_generic_session_title(title):
        return str(title)
    first_user_message = session.get("first_user_message")
    if first_user_message:
        return _title_from_message(str(first_user_message))
    return "新对话"


def _message_row_to_dict(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
    raw_metadata = item.pop("metadata_json", None)
    if raw_metadata:
        try:
            item["metadata"] = json.loads(raw_metadata)
        except json.JSONDecodeError:
            item["metadata"] = {}
    else:
        item["metadata"] = {}
    return item


def _is_generic_session_title(title: object) -> bool:
    if title is None:
        return True
    normalized = str(title).strip()
    return normalized in {"", "server_ops", "新对话"}


def _title_from_message(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content).strip()
    if not normalized:
        return "新对话"
    if len(normalized) <= 24:
        return normalized
    return normalized[:24] + "..."
