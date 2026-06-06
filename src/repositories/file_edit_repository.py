from __future__ import annotations

import sqlite3

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class FileEditAuditRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        relative_path: str,
        size_before: int,
        size_after: int,
        status: str,
        backup_path: str | None = None,
        error_message: str | None = None,
    ) -> int:
        now = utc_now_iso()
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO file_edit_audits
                    (relative_path, size_before, size_after, backup_path, status, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (relative_path, size_before, size_after, backup_path, status, error_message, now),
            )
            self.connection.commit()
            return int(cursor.lastrowid)

    def list_recent(self, limit: int = 50) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT id, relative_path, size_before, size_after, backup_path, status, error_message, created_at
                FROM file_edit_audits
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def list_for_path(self, relative_path: str, limit: int = 20) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT id, relative_path, size_before, size_after, backup_path, status, error_message, created_at
                FROM file_edit_audits
                WHERE relative_path = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (relative_path, limit),
            ).fetchall()
        return [row_to_dict(row) for row in rows]
