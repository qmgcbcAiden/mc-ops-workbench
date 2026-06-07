from __future__ import annotations

import sqlite3

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class PlayerAiRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_settings(self) -> dict:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT enabled, audience, list_mode, updated_at
                FROM player_ai_settings
                WHERE id = 1
                """
            ).fetchone()
            entry_rows = self.connection.execute(
                """
                SELECT player_key, display_name, player_uuid, created_at, updated_at
                FROM player_ai_access_entries
                ORDER BY display_name COLLATE NOCASE ASC
                """
            ).fetchall()
        if row is None:
            return {
                "enabled": True,
                "audience": "all",
                "list_mode": "blocklist",
                "access_entries": [],
                "updated_at": None,
            }
        return {
            "enabled": bool(row["enabled"]),
            "audience": str(row["audience"]),
            "list_mode": str(row["list_mode"]),
            "access_entries": [row_to_dict(entry) for entry in entry_rows],
            "updated_at": row["updated_at"],
        }

    def save_settings(
        self,
        *,
        enabled: bool,
        audience: str,
        list_mode: str,
        access_entries: list[dict],
    ) -> dict:
        now = utc_now_iso()
        normalized_entries = _normalize_entries(access_entries)
        entry_keys = [entry["player_key"] for entry in normalized_entries]
        with locked_connection(self.connection):
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO player_ai_settings (
                        id, enabled, audience, list_mode, updated_at
                    )
                    VALUES (1, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        enabled = excluded.enabled,
                        audience = excluded.audience,
                        list_mode = excluded.list_mode,
                        updated_at = excluded.updated_at
                    """,
                    (1 if enabled else 0, audience, list_mode, now),
                )
                if entry_keys:
                    placeholders = ", ".join("?" for _ in entry_keys)
                    self.connection.execute(
                        f"""
                        DELETE FROM player_ai_access_entries
                        WHERE player_key NOT IN ({placeholders})
                        """,
                        tuple(entry_keys),
                    )
                else:
                    self.connection.execute("DELETE FROM player_ai_access_entries")
                self.connection.executemany(
                    """
                    INSERT INTO player_ai_access_entries (
                        player_key,
                        display_name,
                        player_uuid,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(player_key) DO UPDATE SET
                        display_name = excluded.display_name,
                        player_uuid = COALESCE(
                            excluded.player_uuid,
                            player_ai_access_entries.player_uuid
                        ),
                        updated_at = excluded.updated_at
                    """,
                    [
                        (
                            entry["player_key"],
                            entry["display_name"],
                            entry.get("player_uuid"),
                            now,
                            now,
                        )
                        for entry in normalized_entries
                    ],
                )
        return self.get_settings()

    def get_conversation(self, player_name: str) -> dict | None:
        player_key = _player_key(player_name)
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT player_key, display_name, player_uuid, session_id, last_active_at
                FROM player_ai_conversations
                WHERE player_key = ?
                """,
                (player_key,),
            ).fetchone()
        return row_to_dict(row) if row else None

    def save_conversation(
        self,
        *,
        player_name: str,
        session_id: str,
        player_uuid: str | None = None,
    ) -> dict:
        player_key = _player_key(player_name)
        display_name = str(player_name).strip()
        now = utc_now_iso()
        with locked_connection(self.connection):
            self.connection.execute(
                """
                INSERT INTO player_ai_conversations (
                    player_key,
                    display_name,
                    player_uuid,
                    session_id,
                    last_active_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(player_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    player_uuid = COALESCE(
                        excluded.player_uuid,
                        player_ai_conversations.player_uuid
                    ),
                    session_id = excluded.session_id,
                    last_active_at = excluded.last_active_at
                """,
                (player_key, display_name, player_uuid, session_id, now),
            )
            self.connection.commit()
        return self.get_conversation(display_name) or {}

    def touch_conversation(self, player_name: str) -> None:
        with locked_connection(self.connection):
            self.connection.execute(
                """
                UPDATE player_ai_conversations
                SET display_name = ?, last_active_at = ?
                WHERE player_key = ?
                """,
                (
                    str(player_name).strip(),
                    utc_now_iso(),
                    _player_key(player_name),
                ),
            )
            self.connection.commit()


def _normalize_entries(entries: list[dict]) -> list[dict]:
    normalized: dict[str, dict] = {}
    for entry in entries:
        display_name = str(
            entry.get("display_name")
            or entry.get("name")
            or entry.get("player_name")
            or ""
        ).strip()
        if not display_name:
            continue
        player_key = _player_key(display_name)
        normalized[player_key] = {
            "player_key": player_key,
            "display_name": display_name,
            "player_uuid": str(entry.get("player_uuid") or entry.get("uuid") or "").strip()
            or None,
        }
    return sorted(normalized.values(), key=lambda item: item["display_name"].lower())


def _player_key(player_name: str) -> str:
    return str(player_name or "").strip().lower()
