from __future__ import annotations

import json
import sqlite3

from src.db.connection import locked_connection, row_to_dict
from src.repositories._time import utc_now_iso


class PlayerRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_snapshot(
        self,
        players: list[str],
        captured_at: str | None = None,
    ) -> int:
        captured_at = captured_at or utc_now_iso()
        with locked_connection(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO player_snapshots (captured_at, online_count)
                VALUES (?, ?)
                """,
                (captured_at, len(players)),
            )
            snapshot_id = int(cursor.lastrowid)
            self.connection.executemany(
                """
                INSERT INTO player_snapshot_items (snapshot_id, player_id)
                VALUES (?, ?)
                """,
                [(snapshot_id, player_id) for player_id in players],
            )
            self.connection.commit()
            return snapshot_id

    def get_snapshot(self, snapshot_id: int) -> dict | None:
        with locked_connection(self.connection):
            snapshot = self.connection.execute(
                """
                SELECT id, captured_at, online_count
                FROM player_snapshots
                WHERE id = ?
                """,
                (snapshot_id,),
            ).fetchone()
            if snapshot is None:
                return None

            players = self.connection.execute(
                """
                SELECT player_id
                FROM player_snapshot_items
                WHERE snapshot_id = ?
                ORDER BY player_id ASC
                """,
                (snapshot_id,),
            ).fetchall()

        result = row_to_dict(snapshot)
        result["players"] = [row["player_id"] for row in players]
        return result

    def get_latest_snapshot(self) -> dict | None:
        with locked_connection(self.connection):
            row = self.connection.execute(
                """
                SELECT id
                FROM player_snapshots
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return self.get_snapshot(int(row["id"]))

    def list_known_players(self, limit: int = 500) -> list[str]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT item.player_id, MAX(snapshot.captured_at) AS last_seen_at
                FROM player_snapshot_items item
                JOIN player_snapshots snapshot ON snapshot.id = item.snapshot_id
                GROUP BY item.player_id
                ORDER BY last_seen_at DESC, item.player_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["player_id"]) for row in rows]

    def replace_player_directory_cache(
        self,
        players: list[dict],
        banned_ips: list[dict],
        source_states: list[dict],
        updated_at: str | None = None,
    ) -> None:
        updated_at = updated_at or utc_now_iso()
        with locked_connection(self.connection):
            self.connection.execute("DELETE FROM player_directory_players")
            self.connection.execute("DELETE FROM player_directory_banned_ips")
            self.connection.executemany(
                """
                INSERT INTO player_directory_players (
                    player_key,
                    name,
                    uuid,
                    avatar_url,
                    is_operator,
                    operator_level,
                    is_banned,
                    ban_json,
                    known_ips_json,
                    sources_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(player.get("name") or "").strip().lower(),
                        str(player.get("name") or "").strip(),
                        player.get("uuid"),
                        player.get("avatar_url"),
                        1 if player.get("is_operator") else 0,
                        player.get("operator_level"),
                        1 if player.get("is_banned") else 0,
                        _json_or_none(player.get("ban")),
                        _json_list(player.get("known_ips")),
                        _json_list(player.get("sources")),
                        updated_at,
                    )
                    for player in players
                    if str(player.get("name") or "").strip()
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO player_directory_banned_ips (
                    ip,
                    players_json,
                    created,
                    source,
                    expires,
                    reason,
                    mapping_source,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(entry.get("ip") or "").strip(),
                        _json_list(entry.get("players")),
                        entry.get("created"),
                        entry.get("source"),
                        entry.get("expires"),
                        entry.get("reason"),
                        entry.get("mapping_source"),
                        updated_at,
                    )
                    for entry in banned_ips
                    if str(entry.get("ip") or "").strip()
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO player_directory_sync_state (
                    source,
                    mtime_ns,
                    size_bytes,
                    synced_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    mtime_ns = excluded.mtime_ns,
                    size_bytes = excluded.size_bytes,
                    synced_at = excluded.synced_at
                """,
                [
                    (
                        str(state.get("source") or ""),
                        state.get("mtime_ns"),
                        state.get("size_bytes"),
                        updated_at,
                    )
                    for state in source_states
                    if str(state.get("source") or "")
                ],
            )
            self.connection.commit()

    def get_player_directory_cache(self) -> dict:
        with locked_connection(self.connection):
            player_rows = self.connection.execute(
                """
                SELECT
                    name,
                    uuid,
                    avatar_url,
                    is_operator,
                    operator_level,
                    is_banned,
                    ban_json,
                    known_ips_json,
                    sources_json,
                    updated_at
                FROM player_directory_players
                ORDER BY name COLLATE NOCASE ASC
                """
            ).fetchall()
            ip_rows = self.connection.execute(
                """
                SELECT
                    ip,
                    players_json,
                    created,
                    source,
                    expires,
                    reason,
                    mapping_source,
                    updated_at
                FROM player_directory_banned_ips
                ORDER BY ip ASC
                """
            ).fetchall()

        players = [_directory_player_from_row(row) for row in player_rows]
        banned_ips = [_banned_ip_from_row(row) for row in ip_rows]
        return _directory_result(players, banned_ips)

    def get_player_directory_source_states(self) -> dict[str, dict]:
        with locked_connection(self.connection):
            rows = self.connection.execute(
                """
                SELECT source, mtime_ns, size_bytes, synced_at
                FROM player_directory_sync_state
                """
            ).fetchall()
        return {str(row["source"]): row_to_dict(row) for row in rows}


def _directory_result(players: list[dict], banned_ips: list[dict]) -> dict:
    return {
        "players": players,
        "banned_ips": banned_ips,
        "counts": {
            "players": len(players),
            "operators": sum(1 for player in players if player.get("is_operator")),
            "banned_players": sum(1 for player in players if player.get("is_banned")),
            "banned_ips": len(banned_ips),
        },
        "captured_at": utc_now_iso(),
        "ip_mapping_note": (
            "banned-ips.json 不保存玩家名；这里的玩家映射来自 latest.log 登录记录，"
            "只代表最近日志中能看到的 IP 对应关系。"
        ),
    }


def _directory_player_from_row(row: sqlite3.Row) -> dict:
    return {
        "name": row["name"],
        "uuid": row["uuid"],
        "avatar_url": row["avatar_url"],
        "is_online": False,
        "is_operator": bool(row["is_operator"]),
        "operator_level": row["operator_level"],
        "is_banned": bool(row["is_banned"]),
        "ban": _json_object(row["ban_json"]),
        "known_ips": _json_array(row["known_ips_json"]),
        "sources": _json_array(row["sources_json"]),
        "updated_at": row["updated_at"],
    }


def _banned_ip_from_row(row: sqlite3.Row) -> dict:
    return {
        "ip": row["ip"],
        "players": _json_array(row["players_json"]),
        "created": row["created"],
        "source": row["source"],
        "expires": row["expires"],
        "reason": row["reason"],
        "mapping_source": row["mapping_source"],
        "updated_at": row["updated_at"],
    }


def _json_list(value: object) -> str:
    if not isinstance(value, (list, tuple, set)):
        return "[]"
    cleaned = [str(item) for item in value if str(item).strip()]
    return json.dumps(cleaned, ensure_ascii=False)


def _json_or_none(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_array(value: str | None) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
