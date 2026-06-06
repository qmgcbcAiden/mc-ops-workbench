from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class AddonDiagnosticRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_scan_run(self, scan_run_id: str, refresh_online: bool) -> str:
        with locked_connection(self._connection):
            self._connection.execute(
                """
                INSERT INTO addon_scan_runs
                    (id, status, refresh_online, started_at)
                VALUES (?, ?, ?, ?)
                """,
                (scan_run_id, "started", 1 if refresh_online else 0, utc_now_iso()),
            )
            self._connection.commit()
        return scan_run_id

    def finish_scan_run(
        self,
        scan_run_id: str,
        *,
        status: str,
        asset_count: int,
        diagnostic_count: int,
        severity_counts: dict[str, int],
        error_message: str | None = None,
    ) -> None:
        with locked_connection(self._connection):
            self._connection.execute(
                """
                UPDATE addon_scan_runs
                SET status = ?,
                    asset_count = ?,
                    diagnostic_count = ?,
                    blocker_count = ?,
                    high_count = ?,
                    medium_count = ?,
                    low_count = ?,
                    info_count = ?,
                    error_message = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    asset_count,
                    diagnostic_count,
                    int(severity_counts.get("BLOCKER", 0)),
                    int(severity_counts.get("HIGH", 0)),
                    int(severity_counts.get("MEDIUM", 0)),
                    int(severity_counts.get("LOW", 0)),
                    int(severity_counts.get("INFO", 0)),
                    error_message,
                    utc_now_iso(),
                    scan_run_id,
                ),
            )
            self._connection.commit()

    def add_assets(self, scan_run_id: str, assets: list[dict[str, Any]]) -> None:
        if not assets:
            return
        rows = []
        now = utc_now_iso()
        for asset in assets:
            rows.append(
                (
                    asset["id"],
                    scan_run_id,
                    asset.get("relative_path", ""),
                    asset.get("file_name", ""),
                    asset.get("folder", ""),
                    asset.get("kind", "unknown"),
                    asset.get("addon_id"),
                    asset.get("name"),
                    asset.get("version"),
                    asset.get("loader"),
                    asset.get("environment"),
                    _dump(asset.get("minecraft_versions", [])),
                    _dump(asset.get("dependencies", [])),
                    _dump(asset.get("conflicts", [])),
                    asset.get("sha1"),
                    asset.get("sha512"),
                    int(asset.get("file_size_bytes") or 0),
                    asset.get("metadata_source"),
                    asset.get("metadata_confidence", "heuristic"),
                    _dump(asset.get("knowledge", {})),
                    now,
                )
            )
        with locked_connection(self._connection):
            self._connection.executemany(
                """
                INSERT INTO addon_assets
                    (
                        id, scan_run_id, relative_path, file_name, folder, kind,
                        addon_id, name, version, loader, environment,
                        minecraft_versions_json, dependencies_json, conflicts_json,
                        sha1, sha512, file_size_bytes, metadata_source,
                        metadata_confidence, knowledge_json, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._connection.commit()

    def add_diagnostics(self, scan_run_id: str, diagnostics: list[dict[str, Any]]) -> None:
        if not diagnostics:
            return
        rows = []
        now = utc_now_iso()
        for diagnostic in diagnostics:
            rows.append(
                (
                    diagnostic["id"],
                    scan_run_id,
                    diagnostic.get("severity", "INFO"),
                    diagnostic.get("category", "unknown"),
                    diagnostic.get("message", ""),
                    diagnostic.get("evidence_type", "heuristic_filename"),
                    diagnostic.get("confidence", "heuristic"),
                    _dump(diagnostic.get("affected_files", [])),
                    _dump(diagnostic.get("evidence", {})),
                    _dump(diagnostic.get("sources", [])),
                    _dump(diagnostic.get("suggested_actions", [])),
                    now,
                )
            )
        with locked_connection(self._connection):
            self._connection.executemany(
                """
                INSERT INTO addon_diagnostics
                    (
                        id, scan_run_id, severity, category, message,
                        evidence_type, confidence, affected_files_json,
                        evidence_json, sources_json, suggested_actions_json,
                        created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._connection.commit()

    def latest_scan_run(self) -> dict[str, Any] | None:
        with locked_connection(self._connection):
            row = self._connection.execute(
                """
                SELECT *
                FROM addon_scan_runs
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        return row_to_dict(row) if row else None

    def list_assets(self, scan_run_id: str | None = None) -> list[dict[str, Any]]:
        scan_run_id = scan_run_id or self._latest_scan_run_id()
        if not scan_run_id:
            return []
        with locked_connection(self._connection):
            rows = self._connection.execute(
                """
                SELECT *
                FROM addon_assets
                WHERE scan_run_id = ?
                ORDER BY folder, file_name
                """,
                (scan_run_id,),
            ).fetchall()
        return [self._asset_from_row(row_to_dict(row)) for row in rows]

    def list_diagnostics(
        self,
        scan_run_id: str | None = None,
        severity: str | None = None,
    ) -> list[dict[str, Any]]:
        scan_run_id = scan_run_id or self._latest_scan_run_id()
        if not scan_run_id:
            return []
        severity = (severity or "").strip().upper()
        with locked_connection(self._connection):
            if severity:
                rows = self._connection.execute(
                    """
                    SELECT *
                    FROM addon_diagnostics
                    WHERE scan_run_id = ? AND severity = ?
                    ORDER BY created_at ASC
                    """,
                    (scan_run_id, severity),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT *
                    FROM addon_diagnostics
                    WHERE scan_run_id = ?
                    ORDER BY
                        CASE severity
                            WHEN 'BLOCKER' THEN 0
                            WHEN 'HIGH' THEN 1
                            WHEN 'MEDIUM' THEN 2
                            WHEN 'LOW' THEN 3
                            ELSE 4
                        END,
                        created_at ASC
                    """,
                    (scan_run_id,),
                ).fetchall()
        return [self._diagnostic_from_row(row_to_dict(row)) for row in rows]

    def create_remediation_proposal(
        self,
        proposal_id: str,
        *,
        scan_run_id: str | None,
        diagnostic_ids: list[str],
        actions: list[dict[str, Any]],
    ) -> str:
        with locked_connection(self._connection):
            self._connection.execute(
                """
                INSERT INTO addon_remediation_proposals
                    (
                        id, scan_run_id, diagnostic_ids_json, actions_json,
                        status, confirmation_required, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    scan_run_id,
                    _dump(diagnostic_ids),
                    _dump(actions),
                    "pending",
                    1,
                    utc_now_iso(),
                ),
            )
            self._connection.commit()
        return proposal_id

    def get_remediation_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with locked_connection(self._connection):
            row = self._connection.execute(
                """
                SELECT *
                FROM addon_remediation_proposals
                WHERE id = ?
                """,
                (proposal_id,),
            ).fetchone()
        if not row:
            return None
        data = row_to_dict(row)
        data["diagnostic_ids"] = _load(data.get("diagnostic_ids_json"), [])
        data["actions"] = _load(data.get("actions_json"), [])
        data["confirmation_required"] = bool(data.get("confirmation_required"))
        return data

    def get_cache(self, cache_key: str, now_iso: str) -> dict[str, Any] | None:
        with locked_connection(self._connection):
            row = self._connection.execute(
                """
                SELECT *
                FROM addon_knowledge_cache
                WHERE cache_key = ? AND expires_at > ?
                """,
                (cache_key, now_iso),
            ).fetchone()
        if not row:
            return None
        data = row_to_dict(row)
        data["request"] = _load(data.get("request_json"), {})
        data["response"] = _load(data.get("response_json"), {})
        return data

    def upsert_cache(
        self,
        *,
        cache_key: str,
        provider: str,
        request: dict[str, Any],
        response: dict[str, Any],
        status: str,
        expires_at: str,
    ) -> None:
        now = utc_now_iso()
        with locked_connection(self._connection):
            self._connection.execute(
                """
                INSERT INTO addon_knowledge_cache
                    (
                        cache_key, provider, request_json, response_json,
                        status, expires_at, created_at, updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    provider = excluded.provider,
                    request_json = excluded.request_json,
                    response_json = excluded.response_json,
                    status = excluded.status,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    cache_key,
                    provider,
                    _dump(request),
                    _dump(response),
                    status,
                    expires_at,
                    now,
                    now,
                ),
            )
            self._connection.commit()

    def record_external_request(
        self,
        *,
        provider: str,
        request_type: str,
        request: dict[str, Any],
        status: str,
        error_message: str | None = None,
        latency_ms: int | None = None,
    ) -> int:
        with locked_connection(self._connection):
            cursor = self._connection.execute(
                """
                INSERT INTO external_knowledge_requests
                    (
                        provider, request_type, request_json, status,
                        error_message, latency_ms, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    request_type,
                    _dump(request),
                    status,
                    error_message,
                    latency_ms,
                    utc_now_iso(),
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def _latest_scan_run_id(self) -> str | None:
        latest = self.latest_scan_run()
        return str(latest["id"]) if latest else None

    def _asset_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["minecraft_versions"] = _load(row.get("minecraft_versions_json"), [])
        row["dependencies"] = _load(row.get("dependencies_json"), [])
        row["conflicts"] = _load(row.get("conflicts_json"), [])
        row["knowledge"] = _load(row.get("knowledge_json"), {})
        return row

    def _diagnostic_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["affected_files"] = _load(row.get("affected_files_json"), [])
        row["evidence"] = _load(row.get("evidence_json"), {})
        row["sources"] = _load(row.get("sources_json"), [])
        row["suggested_actions"] = _load(row.get("suggested_actions_json"), [])
        return row


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
