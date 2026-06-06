from __future__ import annotations

import sqlite3

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class EventRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def insert_event(self, event: dict) -> bool:
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO server_events
                    (event_time, level, category, player_id, message, raw_line, raw_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.get("event_time"),
                    event.get("level"),
                    event.get("category"),
                    event.get("player_id"),
                    event["message"],
                    event["raw_line"],
                    event["raw_hash"],
                    event.get("created_at", utc_now_iso()),
                ),
            )
            inserted = cursor.rowcount > 0
            if not inserted:
                values = (
                    event.get("event_time"),
                    event.get("level"),
                    event.get("category"),
                    event.get("player_id"),
                    event["message"],
                )
                self.connection.execute(
                    """
                    UPDATE server_events
                    SET event_time = ?, level = ?, category = ?, player_id = ?, message = ?
                    WHERE raw_hash = ?
                      AND (
                          event_time IS NOT ? OR level IS NOT ? OR category IS NOT ?
                          OR player_id IS NOT ? OR message IS NOT ?
                      )
                    """,
                    (*values, event["raw_hash"], *values),
                )
            self.connection.commit()
            return inserted

    def list_recent(self, level: str = "ANY", limit: int = 100) -> list[dict]:
        with locked_connection(self.connection):
            if level == "ANY":
                rows = self.connection.execute(
                    """
                    SELECT id, event_time, level, category, player_id, message, raw_line,
                           raw_hash, created_at
                    FROM server_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """
                    SELECT id, event_time, level, category, player_id, message, raw_line,
                           raw_hash, created_at
                    FROM server_events
                    WHERE level = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (level, limit),
                ).fetchall()
        return [row_to_dict(row) for row in rows]

    def latest_id(self) -> int:
        with locked_connection(self.connection):
            row = self.connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS latest_id FROM server_events"
            ).fetchone()
        return int(row["latest_id"] if row else 0)

    def list_after_id(self, event_id: int, limit: int = 100) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT id, event_time, level, category, player_id, message, raw_line,
                       raw_hash, created_at
                FROM server_events
                WHERE id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (event_id, limit),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def search(self, keyword: str, level: str = "ANY", limit: int = 100) -> list[dict]:
        pattern = f"%{keyword}%"
        with locked_connection(self.connection):
            if level == "ANY":
                rows = self.connection.execute(
                    """
                    SELECT id, event_time, level, category, player_id, message, raw_line,
                           raw_hash, created_at
                    FROM server_events
                    WHERE message LIKE ? OR raw_line LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (pattern, pattern, limit),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """
                    SELECT id, event_time, level, category, player_id, message, raw_line,
                           raw_hash, created_at
                    FROM server_events
                    WHERE level = ? AND (message LIKE ? OR raw_line LIKE ?)
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (level, pattern, pattern, limit),
                ).fetchall()
        return [row_to_dict(row) for row in rows]

    def delete_all(self) -> int:
        with locked_connection(self.connection):
            cursor = self.connection.execute("DELETE FROM server_events")
            self.connection.commit()
            return cursor.rowcount

    def delete_older_than(self, cutoff_time: str) -> int:
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                DELETE FROM server_events
                WHERE COALESCE(event_time, created_at) < ?
                """,
                (cutoff_time,),
            )
            self.connection.commit()
            return cursor.rowcount
