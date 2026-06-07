from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from src.repositories.player_ai_repository import PlayerAiRepository


PLAYER_AI_AUDIENCES = {"all", "operators"}
PLAYER_AI_LIST_MODES = {"allowlist", "blocklist"}
_PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


@dataclass(frozen=True)
class PlayerAiAccessEntry:
    player_key: str
    display_name: str
    player_uuid: str | None = None

    def to_dict(self) -> dict:
        return {
            "player_key": self.player_key,
            "display_name": self.display_name,
            "player_uuid": self.player_uuid,
        }


@dataclass(frozen=True)
class PlayerAiSettings:
    enabled: bool = True
    audience: str = "all"
    list_mode: str = "blocklist"
    access_entries: tuple[PlayerAiAccessEntry, ...] = ()
    updated_at: str | None = None
    access_keys: frozenset[str] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "access_keys",
            frozenset(entry.player_key for entry in self.access_entries),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "audience": self.audience,
            "list_mode": self.list_mode,
            "access_entries": [entry.to_dict() for entry in self.access_entries],
            "updated_at": self.updated_at,
        }


class PlayerAiPolicyService:
    def __init__(
        self,
        repository: PlayerAiRepository,
        *,
        is_operator: Callable[[str], bool],
        list_known_players: Callable[[], dict] | None = None,
    ) -> None:
        self._repository = repository
        self._is_operator = is_operator
        self._list_known_players = list_known_players
        self._lock = threading.Lock()
        self._snapshot = _settings_from_record(repository.get_settings())

    def get_settings(self) -> PlayerAiSettings:
        with self._lock:
            return self._snapshot

    def save_settings(self, payload: dict) -> PlayerAiSettings:
        settings = _settings_from_payload(payload)
        saved = self._repository.save_settings(
            enabled=settings.enabled,
            audience=settings.audience,
            list_mode=settings.list_mode,
            access_entries=[entry.to_dict() for entry in settings.access_entries],
        )
        snapshot = _settings_from_record(saved)
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def is_enabled(self) -> bool:
        return self.get_settings().enabled

    def is_allowed(self, player_name: str) -> bool:
        settings = self.get_settings()
        if not settings.enabled:
            return False
        player_key = str(player_name or "").strip().lower()
        if not player_key:
            return False
        in_group = (
            settings.audience == "all"
            or self._is_operator(player_name)
        )
        listed = player_key in settings.access_keys
        if settings.list_mode == "allowlist":
            return in_group or listed
        return in_group and not listed

    def list_known_players(self) -> list[dict]:
        if self._list_known_players is None:
            return []
        directory = self._list_known_players() or {}
        players = directory.get("players") or []
        return [
            {
                "name": str(player.get("name") or ""),
                "uuid": player.get("uuid"),
                "is_operator": bool(player.get("is_operator")),
                "is_online": bool(player.get("is_online")),
            }
            for player in players
            if str(player.get("name") or "").strip()
        ]


def _settings_from_payload(payload: dict) -> PlayerAiSettings:
    audience = str(payload.get("audience") or "all").strip().lower()
    list_mode = str(payload.get("list_mode") or "blocklist").strip().lower()
    if audience not in PLAYER_AI_AUDIENCES:
        raise ValueError("玩家 AI 使用范围无效。")
    if list_mode not in PLAYER_AI_LIST_MODES:
        raise ValueError("玩家 AI 名单模式无效。")
    entries = _entries_from_records(payload.get("access_entries") or [])
    return PlayerAiSettings(
        enabled=bool(payload.get("enabled", True)),
        audience=audience,
        list_mode=list_mode,
        access_entries=entries,
    )


def _settings_from_record(record: dict) -> PlayerAiSettings:
    return PlayerAiSettings(
        enabled=bool(record.get("enabled", True)),
        audience=str(record.get("audience") or "all"),
        list_mode=str(record.get("list_mode") or "blocklist"),
        access_entries=_entries_from_records(record.get("access_entries") or []),
        updated_at=record.get("updated_at"),
    )


def _entries_from_records(records: list[dict]) -> tuple[PlayerAiAccessEntry, ...]:
    entries: dict[str, PlayerAiAccessEntry] = {}
    for record in records:
        display_name = str(
            record.get("display_name")
            or record.get("name")
            or record.get("player_name")
            or ""
        ).strip()
        if not _PLAYER_NAME_RE.fullmatch(display_name):
            raise ValueError(f"玩家名无效：{display_name or '空值'}")
        player_key = display_name.lower()
        entries[player_key] = PlayerAiAccessEntry(
            player_key=player_key,
            display_name=display_name,
            player_uuid=str(record.get("player_uuid") or record.get("uuid") or "").strip()
            or None,
        )
    return tuple(sorted(entries.values(), key=lambda item: item.display_name.lower()))
