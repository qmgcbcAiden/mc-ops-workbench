from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class ConfigVersionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_commit(
        self,
        commit_id: str,
        parent_commit_id: str | None,
        proposal_id: str | None,
        relative_path: str,
        actor: str,
        source: str,
        message: str,
        risk_level: str | None,
        auto_approved: bool,
        status: str,
        error_message: str | None = None,
    ) -> int:
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO config_version_commits
                    (commit_id, parent_commit_id, proposal_id, relative_path,
                     actor, source, message, risk_level, auto_approved,
                     status, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    commit_id,
                    parent_commit_id,
                    proposal_id,
                    relative_path,
                    actor,
                    source,
                    message,
                    risk_level,
                    1 if auto_approved else 0,
                    status,
                    error_message,
                    utc_now_iso(),
                ),
            )
            self.connection.commit()
            return cursor.lastrowid

    def get_by_commit_id(self, commit_id: str) -> dict[str, Any] | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                "SELECT * FROM config_version_commits WHERE commit_id = ?",
                (commit_id,),
            ).fetchone()
        return self._inflate(row_to_dict(row)) if row else None

    def list_for_path(self, relative_path: str, limit: int = 50) -> list[dict[str, Any]]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT * FROM config_version_commits
                WHERE relative_path = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (relative_path, limit),
            ).fetchall()
        return [self._inflate(row_to_dict(row)) for row in rows]

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT * FROM config_version_commits
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._inflate(row_to_dict(row)) for row in rows]

    def update_proposal_version(
        self,
        proposal_id: str,
        commit_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE config_change_proposals
                SET version_commit_id = ?,
                    version_status = ?,
                    version_error_message = ?
                WHERE id = ?
                """,
                (commit_id, status, error_message, proposal_id),
            )
            self.connection.commit()

    def _inflate(self, row: dict[str, Any]) -> dict[str, Any]:
        row["auto_approved"] = bool(row.get("auto_approved"))
        return row
