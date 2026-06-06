from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class JavaEnvironmentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_audit(
        self,
        *,
        action: str,
        status: str,
        minecraft_version: str | None = None,
        required_java_major: int | None = None,
        selected_java_path: str | None = None,
        selected_java_home: str | None = None,
        candidate_source: str | None = None,
        distribution: str | None = None,
        package_type: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        metadata_json = (
            json.dumps(metadata, ensure_ascii=False, default=str)
            if metadata is not None
            else None
        )
        with locked_connection(self._connection):
            cursor = self._connection.execute(
                """
                INSERT INTO java_environment_audits
                    (action, status, minecraft_version, required_java_major,
                     selected_java_path, selected_java_home, candidate_source,
                     distribution, package_type, error_message, metadata_json,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action,
                    status,
                    minecraft_version,
                    required_java_major,
                    selected_java_path,
                    selected_java_home,
                    candidate_source,
                    distribution,
                    package_type,
                    error_message,
                    metadata_json,
                    utc_now_iso(),
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def list_recent(self, limit: int = 50) -> list[dict]:
        with locked_connection(self._connection):
            rows = self._connection.execute(
                """
                SELECT id, action, status, minecraft_version, required_java_major,
                       selected_java_path, selected_java_home, candidate_source,
                       distribution, package_type, error_message, metadata_json,
                       created_at
                FROM java_environment_audits
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]
