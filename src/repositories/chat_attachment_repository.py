from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class ChatAttachmentRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        session_id: str,
        kind: str,
        label: str,
        content: str,
        metadata: dict | None = None,
        turn_id: str | None = None,
        compressed_content: str | None = None,
        include_policy: str = "current_turn",
    ) -> str:
        attachment_id = str(uuid4())
        now = utc_now_iso()
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        with locked_connection(self.connection):
            self.connection.execute(
                """
                INSERT INTO chat_attachments
                    (
                        id, session_id, turn_id, kind, label, content,
                        compressed_content, include_policy, metadata_json,
                        char_count, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment_id,
                    session_id,
                    turn_id,
                    kind,
                    label,
                    content,
                    compressed_content,
                    include_policy,
                    metadata_json,
                    len(content or ""),
                    now,
                ),
            )
            self.connection.commit()
        return attachment_id

    def bind_to_turn(self, attachment_id: str, turn_id: str) -> None:
        with locked_connection(self.connection):
            self.connection.execute(
                "UPDATE chat_attachments SET turn_id = ? WHERE id = ?",
                (turn_id, attachment_id),
            )
            self.connection.commit()

    def update_compressed_content(
        self,
        attachment_id: str,
        compressed_content: str | None,
    ) -> None:
        with locked_connection(self.connection):
            self.connection.execute(
                "UPDATE chat_attachments SET compressed_content = ? WHERE id = ?",
                (compressed_content, attachment_id),
            )
            self.connection.commit()

    def get(self, attachment_id: str) -> dict | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT id, session_id, turn_id, kind, label, content,
                       compressed_content, include_policy, metadata_json,
                       char_count, created_at
                FROM chat_attachments
                WHERE id = ?
                """,
                (attachment_id,),
            ).fetchone()
        return row_to_dict(row) if row else None

    def list_for_session(self, session_id: str) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT id, session_id, turn_id, kind, label, content,
                       compressed_content, include_policy, metadata_json,
                       char_count, created_at
                FROM chat_attachments
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def list_for_turn(self, turn_id: str) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT id, session_id, turn_id, kind, label, content,
                       compressed_content, include_policy, metadata_json,
                       char_count, created_at
                FROM chat_attachments
                WHERE turn_id = ?
                ORDER BY created_at ASC
                """,
                (turn_id,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]
