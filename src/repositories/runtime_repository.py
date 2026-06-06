from __future__ import annotations

import sqlite3

from src.db.connection import locked_connection
from src.repositories._time import utc_now_iso


class ServerRuntimeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_event(
        self,
        event_type: str,
        status: str,
        pid: int | None = None,
        message: str | None = None,
    ) -> int:
        now = utc_now_iso()
        with locked_connection(self._connection):
            cursor = self._connection.execute(
                """INSERT INTO server_runtime_events
                   (event_type, status, pid, message, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (event_type, status, pid, message, now),
            )
            self._connection.commit()
            return cursor.lastrowid

    def list_recent(self, limit: int = 20) -> list[dict]:
        with locked_connection(self._connection):
            rows = self._connection.execute(
                """SELECT id, event_type, status, pid, message, created_at
                   FROM server_runtime_events
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest(self) -> dict | None:
        with locked_connection(self._connection):
            row = self._connection.execute(
                """SELECT id, event_type, status, pid, message, created_at
                   FROM server_runtime_events
                   ORDER BY created_at DESC
                   LIMIT 1"""
            ).fetchone()
        if row is None:
            return None
        return dict(row)
