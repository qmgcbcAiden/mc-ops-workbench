from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class LogAnalysisRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        source_label: str,
        raw_line_count: int,
        raw_char_count: int,
        compressed_text: str,
        key_findings: list[str],
        retained_snippets: list[str],
        attachment_id: str | None = None,
        model: str | None = None,
    ) -> str:
        analysis_id = str(uuid4())
        now = utc_now_iso()
        key_findings_json = json.dumps(key_findings, ensure_ascii=False)
        with locked_connection(self.connection):
            self.connection.execute(
                """
                INSERT INTO log_analysis_results
                    (id, attachment_id, source_label, raw_line_count, raw_char_count,
                     compressed_text, key_findings_json, retained_snippets, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    attachment_id,
                    source_label,
                    raw_line_count,
                    raw_char_count,
                    compressed_text,
                    key_findings_json,
                    "\n".join(retained_snippets),
                    model,
                    now,
                ),
            )
            self.connection.commit()
        return analysis_id

    def get(self, analysis_id: str) -> dict | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT id, attachment_id, source_label, raw_line_count, raw_char_count,
                       compressed_text, key_findings_json, retained_snippets, model, created_at
                FROM log_analysis_results
                WHERE id = ?
                """,
                (analysis_id,),
            ).fetchone()
        return row_to_dict(row) if row else None

    def get_by_attachment(self, attachment_id: str) -> dict | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT id, attachment_id, source_label, raw_line_count, raw_char_count,
                       compressed_text, key_findings_json, retained_snippets, model, created_at
                FROM log_analysis_results
                WHERE attachment_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (attachment_id,),
            ).fetchone()
        return row_to_dict(row) if row else None
