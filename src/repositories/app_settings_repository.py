from __future__ import annotations

import sqlite3

from src.db.connection import locked_connection
from src.repositories._time import utc_now_iso


class AppSettingsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, key: str) -> str | None:
        with locked_connection(self._connection):
            row = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set(self, key: str, value: str) -> None:
        now = utc_now_iso()
        with locked_connection(self._connection):
            self._connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            self._connection.commit()
