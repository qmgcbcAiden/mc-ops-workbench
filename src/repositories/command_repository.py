from __future__ import annotations

import sqlite3

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class CommandRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_audit(
        self,
        command: str,
        normalized_command: str,
        risk_level: str,
        requested_by: str,
        confirmation_required: bool,
        status: str = "requested",
    ) -> int:
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO command_audits
                    (command, normalized_command, risk_level, status, requested_by,
                     confirmation_required, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command,
                    normalized_command,
                    risk_level,
                    status,
                    requested_by,
                    1 if confirmation_required else 0,
                    utc_now_iso(),
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)

    def mark_executed(
        self,
        audit_id: int,
        status: str,
        output: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE command_audits
                SET status = ?, output = ?, error_message = ?, executed_at = ?
                WHERE id = ?
                """,
                (status, output, error_message, utc_now_iso(), audit_id),
            )
            self.connection.commit()

    def mark_status(
        self,
        audit_id: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE command_audits
                SET status = ?, error_message = ?
                WHERE id = ?
                """,
                (status, error_message, audit_id),
            )
            self.connection.commit()

    def get_audit(self, audit_id: int) -> dict | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT id, command, normalized_command, risk_level, status, requested_by,
                       confirmation_required, output, error_message, created_at, executed_at
                FROM command_audits
                WHERE id = ?
                """,
                (audit_id,),
            ).fetchone()
        return row_to_dict(row) if row is not None else None

    def transition_status(
        self,
        audit_id: int,
        expected_status: str,
        status: str,
        *,
        set_executed_at: bool = False,
    ) -> bool:
        with locked_connection(self.connection):
            if set_executed_at:
                cursor = self.connection.execute(
                    """
                    UPDATE command_audits
                    SET status = ?, executed_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (status, utc_now_iso(), audit_id, expected_status),
                )
            else:
                cursor = self.connection.execute(
                    """
                    UPDATE command_audits
                    SET status = ?
                    WHERE id = ? AND status = ?
                    """,
                    (status, audit_id, expected_status),
                )
            self.connection.commit()
            return cursor.rowcount == 1

    def list_recent(self, limit: int = 50) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT id, command, normalized_command, risk_level, status, requested_by,
                       confirmation_required, output, error_message, created_at, executed_at
                FROM command_audits
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]
