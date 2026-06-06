from __future__ import annotations

import sqlite3

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class MetricRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def insert_sample(self, sample: dict) -> int:
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO metrics_samples
                    (captured_at, cpu_percent, memory_percent, memory_used_mb,
                     memory_total_mb, server_pid)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sample.get("captured_at", utc_now_iso()),
                    sample["cpu_percent"],
                    sample["memory_percent"],
                    sample.get("memory_used_mb"),
                    sample.get("memory_total_mb"),
                    sample.get("server_pid"),
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)

    def list_recent(self, limit: int = 120) -> list[dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT id, captured_at, cpu_percent, memory_percent, memory_used_mb,
                       memory_total_mb, server_pid
                FROM metrics_samples
                ORDER BY captured_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def delete_older_than(self, cutoff_time: str) -> int:
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                "DELETE FROM metrics_samples WHERE captured_at < ?",
                (cutoff_time,),
            )
            self.connection.commit()
            return cursor.rowcount
