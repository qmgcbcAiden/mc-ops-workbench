from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class ConfigChangeRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_proposal(
        self,
        proposal_id: str,
        relative_path: str,
        user_request: str,
        before_hash: str,
        after_hash: str,
        before_content: str,
        after_content: str,
        diff_text: str,
        changes: list[dict[str, Any]],
        risk_level: str,
        restart_required: bool,
        warnings: list[str],
        session_id: str | None = None,
        status: str = "pending",
    ) -> str:
        with locked_connection(self.connection):
            self.connection.execute(
                """
                INSERT INTO config_change_proposals
                    (
                        id, session_id, user_request, relative_path, before_hash,
                        after_hash, before_content, after_content, diff_text,
                        changes_json, risk_level, restart_required, warnings_json,
                        status, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    session_id,
                    user_request,
                    relative_path,
                    before_hash,
                    after_hash,
                    before_content,
                    after_content,
                    diff_text,
                    json.dumps(changes, ensure_ascii=False),
                    risk_level,
                    1 if restart_required else 0,
                    json.dumps(warnings, ensure_ascii=False),
                    status,
                    utc_now_iso(),
                ),
            )
            self.connection.commit()
        return proposal_id

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT *
                FROM config_change_proposals
                WHERE id = ?
                """,
                (proposal_id,),
            ).fetchone()
        return self._inflate(row_to_dict(row)) if row else None

    def update_status(
        self,
        proposal_id: str,
        status: str,
        confirmed_by: str | None = None,
        backup_path: str | None = None,
        error_message: str | None = None,
        set_confirmed_at: bool = False,
        set_applied_at: bool = False,
    ) -> None:
        now = utc_now_iso()
        confirmed_at = now if set_confirmed_at else None
        applied_at = now if set_applied_at else None

        assignments = ["status = ?", "error_message = ?"]
        values: list[Any] = [status, error_message]
        if set_confirmed_at:
            assignments.append("confirmed_at = ?")
            values.append(confirmed_at)
        if set_applied_at:
            assignments.append("applied_at = ?")
            values.append(applied_at)
        if confirmed_by is not None:
            assignments.append("confirmed_by = ?")
            values.append(confirmed_by)
        if backup_path is not None:
            assignments.append("backup_path = ?")
            values.append(backup_path)

        values.append(proposal_id)
        sql = f"""
            UPDATE config_change_proposals
            SET {", ".join(assignments)}
            WHERE id = ?
        """
        with locked_connection(self.connection):
            self.connection.execute(sql, values)
            self.connection.commit()

    def update_auto_approval(
        self,
        proposal_id: str,
        auto_approved: bool,
        approval_policy: str,
        version_commit_id: str | None = None,
        version_status: str | None = None,
        redaction_version: str | None = None,
    ) -> None:
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE config_change_proposals
                SET auto_approved = ?,
                    approval_policy = ?,
                    version_commit_id = ?,
                    version_status = ?,
                    redaction_version = ?
                WHERE id = ?
                """,
                (
                    1 if auto_approved else 0,
                    approval_policy,
                    version_commit_id,
                    version_status,
                    redaction_version,
                    proposal_id,
                ),
            )
            self.connection.commit()

    def update_version_status(
        self,
        proposal_id: str,
        version_status: str,
        version_commit_id: str | None = None,
        version_error_message: str | None = None,
        redaction_version: str | None = None,
    ) -> None:
        assignments = ["version_status = ?", "version_error_message = ?"]
        values: list[Any] = [version_status, version_error_message]
        if version_commit_id is not None:
            assignments.append("version_commit_id = ?")
            values.append(version_commit_id)
        if redaction_version is not None:
            assignments.append("redaction_version = ?")
            values.append(redaction_version)

        values.append(proposal_id)
        sql = f"""
            UPDATE config_change_proposals
            SET {", ".join(assignments)}
            WHERE id = ?
        """
        with locked_connection(self.connection):
            self.connection.execute(sql, values)
            self.connection.commit()

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT *
                FROM config_change_proposals
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._inflate(row_to_dict(row)) for row in rows]

    def list_for_session(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT *
                FROM config_change_proposals
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [self._inflate(row_to_dict(row)) for row in rows]

    def _inflate(self, row: dict[str, Any]) -> dict[str, Any]:
        row["restart_required"] = bool(row.get("restart_required"))
        row["changes"] = _loads(row.get("changes_json"), [])
        row["warnings"] = _loads(row.get("warnings_json"), [])
        return row


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
