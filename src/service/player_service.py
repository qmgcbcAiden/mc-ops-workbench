from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

from src.config.settings import Settings
from src.mc.rcon_client import RCONError, send_rcon_command
from src.repositories._time import utc_now_iso
from src.repositories.player_repository import PlayerRepository

logger = logging.getLogger(__name__)

_LIST_RE = re.compile(
    r"There are (?P<online>\d+) of a max of (?P<max>\d+) players? online"
    r"(?:\s*:\s*(?P<players>.+))?"
)
_UUID_VALUE_PATTERN = (
    r"(?:[0-9a-fA-F]{32}|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
_UUID_TEXT_RE = re.compile(rf"^{_UUID_VALUE_PATTERN}$")
_UUID_ENTRY_RE = re.compile(
    rf"(\S+)\s+\(({_UUID_VALUE_PATTERN})\)"
)

_JOIN_PATTERN = re.compile(
    r"\b(?P<player>[A-Za-z0-9_]{3,16}) joined the game\b"
)
_LEAVE_PATTERN = re.compile(
    r"\b(?P<player>[A-Za-z0-9_]{3,16}) (?:lost connection\b|left the game\b)"
)
_LOGIN_ADDRESS_PATTERN = re.compile(
    r"\b(?P<player>[A-Za-z0-9_]{3,16})\[/?(?P<address>[^\]]+)\] logged in\b"
)
RCON_CALIBRATION_INTERVAL_SECONDS = 60.0
UUID_LOOKUP_RETRY_INTERVAL_SECONDS = 60.0
MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{name}"
PLAYER_DIRECTORY_LOG_TAIL_BYTES = 512 * 1024
OFFLINE_PLAYER_CONFIG_ACTIVE_STATES = {"starting", "running", "stopping"}
OPERATOR_CONFIG_PATH = "ops.json"
BANNED_PLAYERS_CONFIG_PATH = "banned-players.json"
BANNED_IPS_CONFIG_PATH = "banned-ips.json"
MINECRAFT_CONFIG_SOURCE = "Server"
DEFAULT_BAN_REASON = "Banned by an operator."
DEFAULT_TEMP_BAN_REASON = "Temporary ban."


class PlayerService:
    def __init__(
        self,
        player_repository: PlayerRepository,
        settings: Settings,
        get_server_state: Callable[[], str] | None = None,
        stdout_source: Callable[[], list[str]] | None = None,
        rcon_sender: Callable[..., str] | None = None,
        uuid_resolver: Callable[[str], str | None] | None = None,
        rcon_refresh_interval_seconds: float = RCON_CALIBRATION_INTERVAL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.player_repository = player_repository
        self._settings = settings
        self._get_server_state = get_server_state or (lambda: "stopped")
        self._stdout_source = stdout_source
        self._rcon_sender = rcon_sender or send_rcon_command
        self._uuid_resolver = uuid_resolver or (lambda name: resolve_skin_uuid(name, settings))
        self._rcon_refresh_interval_seconds = rcon_refresh_interval_seconds
        self._clock = clock or time.monotonic
        self._online_players: dict[str, dict] = {}
        self._uuid_cache: dict[str, str] = {}
        self._last_uuid_lookup_at: dict[str, float] = {}
        self._max_players = 20
        self._lock = threading.RLock()
        self._stdout_snapshot: list[str] = []
        self._last_rcon_attempt_at: float | None = None
        self._last_state: str | None = None
        self._operator_cache_mtime_ns: int | None = None
        self._operator_cache: dict[str, dict] = {
            "uuids": set(),
            "names": set(),
            "entries": {},
        }
        self._last_recorded_snapshot: tuple[str, ...] | None = None

    def refresh(self) -> dict:
        state = self._get_server_state()
        self._remember_state(state)
        if state not in ("running", "starting"):
            with self._lock:
                self._online_players.clear()
                self._stdout_snapshot = []
                self._last_rcon_attempt_at = None
            return _empty_result()

        self._scan_stdout_for_events()
        if state == "running" and self._should_attempt_rcon_calibration():
            rcon_result = self._try_rcon_list()
            if rcon_result is not None:
                rcon_players, max_players = rcon_result
                with self._lock:
                    self._online_players = rcon_players
                    self._max_players = max_players
                    self._remember_stdout_snapshot()
        result = self._build_result(state)
        self._record_player_snapshot(result["players"], state)
        return result

    def get_online_players(self) -> dict:
        return self.refresh()

    def get_cached_players(self) -> dict:
        with self._lock:
            if not self._online_players:
                state = self._get_server_state()
                if state not in ("running", "starting"):
                    return _empty_result()
            return self._build_result(self._get_server_state())

    def get_player_directory(self) -> dict:
        return self.refresh_player_directory(force=False)

    def refresh_player_directory(self, force: bool = False) -> dict:
        if self.player_repository is not None and not force:
            cached = self._get_player_directory_cache()
            if cached is not None and not self._directory_sources_changed():
                return self._with_runtime_directory_data(cached)

        data, source_states = self._build_player_directory_from_sources()
        self._replace_player_directory_cache(data, source_states)
        return data

    def apply_offline_player_command(self, command: str) -> dict:
        command = (command or "").strip()
        state = self._get_server_state()
        if state in OFFLINE_PLAYER_CONFIG_ACTIVE_STATES:
            return _offline_config_result(
                command=command,
                status="failed",
                message="服务器仍在运行或切换状态，请优先使用服务器命令。",
                error_message="服务器仍在运行或切换状态，请优先使用服务器命令。",
            )

        server_dir = getattr(self._settings, "mc_server_dir", None)
        if server_dir is None:
            return _offline_config_result(
                command=command,
                status="failed",
                message="未配置 Minecraft 服务器目录，无法修改玩家配置文件。",
                error_message="未配置 Minecraft 服务器目录，无法修改玩家配置文件。",
            )

        parsed = _parse_offline_player_command(command)
        if parsed is None:
            return _offline_config_result(
                command=command,
                status="failed",
                message="该玩家操作不支持离线配置兜底。",
                error_message="该玩家操作不支持离线配置兜底。",
            )

        server_path = Path(server_dir)
        action = parsed["action"]
        target = parsed["target"]
        uuid_value = self._resolve_local_player_uuid(target)

        try:
            if action == "op":
                result = _set_operator_config(server_path, target, uuid_value, enabled=True)
            elif action == "deop":
                result = _set_operator_config(server_path, target, uuid_value, enabled=False)
            elif action in {"ban", "tempban"}:
                expires = parsed.get("expires") or "forever"
                reason = parsed.get("reason") or (
                    DEFAULT_TEMP_BAN_REASON if action == "tempban" else DEFAULT_BAN_REASON
                )
                result = _set_banned_player_config(
                    server_path,
                    target,
                    uuid_value,
                    banned=True,
                    expires=expires,
                    reason=reason,
                )
            elif action == "pardon":
                result = _set_banned_player_config(
                    server_path,
                    target,
                    uuid_value,
                    banned=False,
                )
            elif action in {"ban-ip", "tempbanip"}:
                expires = parsed.get("expires") or "forever"
                reason = parsed.get("reason") or (
                    DEFAULT_TEMP_BAN_REASON if action == "tempbanip" else DEFAULT_BAN_REASON
                )
                result = _set_banned_ip_config(
                    server_path,
                    target,
                    banned=True,
                    expires=expires,
                    reason=reason,
                    log_path=getattr(self._settings, "mc_log_path", None),
                )
            elif action == "pardon-ip":
                result = _set_banned_ip_config(
                    server_path,
                    target,
                    banned=False,
                    log_path=getattr(self._settings, "mc_log_path", None),
                )
            else:
                return _offline_config_result(
                    command=command,
                    status="failed",
                    message="该玩家操作不支持离线配置兜底。",
                    error_message="该玩家操作不支持离线配置兜底。",
                )
        except OSError as exc:
            return _offline_config_result(
                command=command,
                status="failed",
                message=f"配置文件写入失败：{exc}",
                error_message=f"配置文件写入失败：{exc}",
            )

        if result.get("status") == "file_updated":
            self._invalidate_file_caches(result.get("relative_path"))
            self.refresh_player_directory(force=True)
        elif result.get("status") == "no_change":
            self._invalidate_file_caches(result.get("relative_path"))
        return result

    def _build_player_directory_from_sources(self) -> tuple[dict, list[dict]]:
        server_dir = getattr(self._settings, "mc_server_dir", None)
        directory: dict[str, dict] = {}

        if server_dir is not None:
            for player in _load_usercache_players(Path(server_dir)):
                _merge_directory_player(directory, player, "usercache")

        for name in self._list_known_snapshot_players():
            _merge_directory_player(directory, _make_player_entry(name), "snapshot")

        online_data = self.get_cached_players()
        for player in online_data.get("players", []):
            _merge_directory_player(directory, player, "online", is_online=True)

        operator_lookup = self._load_operator_lookup()
        for operator in _unique_operator_entries(operator_lookup):
            name = operator.get("name")
            if not name:
                continue
            _merge_directory_player(
                directory,
                _make_player_entry(name, operator.get("uuid")),
                "ops",
                is_operator=True,
                operator_level=operator.get("level"),
            )

        banned_players = (
            _load_banned_player_lookup(Path(server_dir))
            if server_dir is not None
            else {"entries": {}, "items": []}
        )
        for banned in banned_players["items"]:
            name = banned.get("name")
            if not name:
                continue
            _merge_directory_player(
                directory,
                _make_player_entry(name, banned.get("uuid")),
                "banned-players",
                is_banned=True,
                ban=banned,
            )

        ip_map = _load_login_ip_map(getattr(self._settings, "mc_log_path", None))
        for name, ips in ip_map.get("players", {}).items():
            _merge_directory_player(
                directory,
                _make_player_entry(name),
                "latest.log",
                known_ips=ips,
            )

        banned_ips = (
            _load_banned_ip_entries(Path(server_dir), ip_map)
            if server_dir is not None
            else []
        )
        players = sorted(
            directory.values(),
            key=lambda item: (
                not bool(item.get("is_online")),
                str(item.get("name") or "").lower(),
            ),
        )
        return _player_directory_result(players, banned_ips), _player_directory_source_states(
            server_dir,
            getattr(self._settings, "mc_log_path", None),
        )

    def on_stdout_line(self, line: str) -> str | None:
        join_match = _JOIN_PATTERN.search(line)
        if join_match:
            name = join_match.group("player")
            entry = self._make_player_entry(name)
            with self._lock:
                if name not in self._online_players:
                    self._online_players[name] = entry
            return name
        leave_match = _LEAVE_PATTERN.search(line)
        if leave_match:
            name = leave_match.group("player")
            with self._lock:
                self._online_players.pop(name, None)
            return name
        return None

    def _try_rcon_list(self) -> tuple[dict[str, dict], int] | None:
        pw = self._settings.mc_rcon_password
        if not pw:
            return None
        try:
            response = self._rcon_sender(
                self._settings.mc_rcon_host,
                self._settings.mc_rcon_port,
                pw,
                "list uuids",
                timeout=3.0,
            )
            players, max_players = _parse_list_uuids_response(response)
            return self._enrich_players_with_resolved_uuids(players), max_players
        except RCONError:
            try:
                response = self._rcon_sender(
                    self._settings.mc_rcon_host,
                    self._settings.mc_rcon_port,
                    pw,
                    "list",
                    timeout=3.0,
                )
                players, max_players = _parse_list_response(response)
                return self._enrich_players_with_resolved_uuids(players), max_players
            except (RCONError, OSError):
                logger.debug("RCON list query failed", exc_info=True)
                return None
        except OSError:
            logger.debug("RCON socket error during list query", exc_info=True)
            return None

    def _make_player_entry(self, name: str, uuid_str: str | None = None) -> dict:
        player_uuid = _normalize_uuid(uuid_str)
        if player_uuid is None:
            player_uuid = self._resolve_uuid_for_name(name)
        return _make_player_entry(name, player_uuid)

    def _enrich_players_with_resolved_uuids(self, players: dict[str, dict]) -> dict[str, dict]:
        enriched = dict(players)
        for name, player in players.items():
            if player.get("uuid"):
                continue
            resolved_uuid = self._resolve_uuid_for_name(name)
            if resolved_uuid:
                enriched[name] = _make_player_entry(name, resolved_uuid)
        return enriched

    def _resolve_uuid_for_name(self, name: str) -> str | None:
        cache_key = name.lower()
        now = self._clock()
        with self._lock:
            cached = self._uuid_cache.get(cache_key)
            if cached:
                return cached
            last_attempt = self._last_uuid_lookup_at.get(cache_key)
            if (
                last_attempt is not None
                and now - last_attempt < UUID_LOOKUP_RETRY_INTERVAL_SECONDS
            ):
                return None
            self._last_uuid_lookup_at[cache_key] = now

        try:
            resolved_uuid = _normalize_uuid(self._uuid_resolver(name))
        except Exception:
            logger.debug("Player UUID lookup failed for %s", name, exc_info=True)
            return None

        if resolved_uuid:
            with self._lock:
                self._uuid_cache[cache_key] = resolved_uuid
        return resolved_uuid

    def _remember_state(self, state: str) -> None:
        with self._lock:
            if state == self._last_state:
                return
            self._last_state = state
            self._last_rcon_attempt_at = None

    def _should_attempt_rcon_calibration(self) -> bool:
        if not self._settings.mc_rcon_password:
            return False
        now = self._clock()
        with self._lock:
            last_attempt = self._last_rcon_attempt_at
            if (
                last_attempt is not None
                and now - last_attempt < self._rcon_refresh_interval_seconds
            ):
                return False
            self._last_rcon_attempt_at = now
        return True

    def _scan_stdout_for_events(self) -> None:
        if self._stdout_source is None:
            return
        try:
            lines = self._stdout_source()
        except Exception:
            return
        with self._lock:
            new_lines = _new_stdout_lines(self._stdout_snapshot, lines)
            self._stdout_snapshot = list(lines)
        if not new_lines:
            return
        for line in new_lines:
            self.on_stdout_line(line)

    def _remember_stdout_snapshot(self) -> None:
        if self._stdout_source is None:
            return
        try:
            lines = self._stdout_source()
        except Exception:
            return
        self._stdout_snapshot = list(lines)

    def _build_result(self, state: str) -> dict:
        with self._lock:
            players = [
                self._with_operator_state(player)
                for player in self._online_players.values()
            ]
            max_players = self._max_players
        return {
            "online_count": len(players),
            "max_players": max_players,
            "players": players,
            "captured_at": utc_now_iso(),
            "server_state": state,
        }

    def _with_operator_state(self, player: dict) -> dict:
        operator_lookup = self._load_operator_lookup()
        player_uuid = _normalize_uuid(player.get("uuid"))
        player_name = str(player.get("name") or "")
        operator_entry = None
        if player_uuid:
            operator_entry = operator_lookup["entries"].get(player_uuid)
        if operator_entry is None and player_name:
            operator_entry = operator_lookup["entries"].get(player_name.lower())
        return {
            **player,
            "is_operator": operator_entry is not None,
            "operator_level": (
                operator_entry.get("level")
                if isinstance(operator_entry, dict)
                else None
            ),
        }

    def _load_operator_lookup(self) -> dict:
        server_dir = getattr(self._settings, "mc_server_dir", None)
        if server_dir is None:
            return self._operator_cache
        ops_path = Path(server_dir) / "ops.json"
        try:
            mtime_ns = ops_path.stat().st_mtime_ns
        except OSError:
            self._operator_cache_mtime_ns = None
            self._operator_cache = {"uuids": set(), "names": set(), "entries": {}}
            return self._operator_cache

        if mtime_ns == self._operator_cache_mtime_ns:
            return self._operator_cache

        lookup = _load_operator_lookup(ops_path)
        self._operator_cache_mtime_ns = mtime_ns
        self._operator_cache = lookup
        return lookup

    def _record_player_snapshot(self, players: list[dict], state: str) -> None:
        if state not in ("running", "starting") or self.player_repository is None:
            return
        names = sorted(
            str(player.get("name") or "").strip()
            for player in players
            if str(player.get("name") or "").strip()
        )
        signature = tuple(names)
        with self._lock:
            if signature == self._last_recorded_snapshot:
                return
            self._last_recorded_snapshot = signature
        try:
            self.player_repository.create_snapshot(names)
        except Exception:
            logger.debug("Player snapshot persistence failed", exc_info=True)

    def _list_known_snapshot_players(self) -> list[str]:
        repository = self.player_repository
        if repository is None:
            return []
        list_known = getattr(repository, "list_known_players", None)
        if not callable(list_known):
            return []
        try:
            return list_known()
        except Exception:
            logger.debug("Known player snapshot lookup failed", exc_info=True)
            return []

    def _get_player_directory_cache(self) -> dict | None:
        repository = self.player_repository
        if repository is None:
            return None
        get_cache = getattr(repository, "get_player_directory_cache", None)
        if not callable(get_cache):
            return None
        try:
            return get_cache()
        except Exception:
            logger.debug("Player directory cache lookup failed", exc_info=True)
            return None

    def _has_player_directory_cache(self) -> bool:
        repository = self.player_repository
        if repository is None:
            return False
        try:
            source_states = repository.get_player_directory_source_states()
        except Exception:
            source_states = {}
        if source_states:
            return True
        cached = self._get_player_directory_cache()
        return bool(
            cached
            and (
                cached.get("players")
                or cached.get("banned_ips")
            )
        )

    def _directory_sources_changed(self) -> bool:
        repository = self.player_repository
        if repository is None:
            return True
        try:
            previous = repository.get_player_directory_source_states()
        except Exception:
            logger.debug("Player directory source-state lookup failed", exc_info=True)
            return True
        if not previous:
            return True
        current = _player_directory_source_states(
            getattr(self._settings, "mc_server_dir", None),
            getattr(self._settings, "mc_log_path", None),
        )
        for state in current:
            source = state["source"]
            old = previous.get(source)
            if old is None:
                return True
            if old.get("mtime_ns") != state.get("mtime_ns"):
                return True
            if old.get("size_bytes") != state.get("size_bytes"):
                return True
        return False

    def _replace_player_directory_cache(self, data: dict, source_states: list[dict]) -> None:
        repository = self.player_repository
        if repository is None:
            return
        replace_cache = getattr(repository, "replace_player_directory_cache", None)
        if not callable(replace_cache):
            return
        try:
            replace_cache(
                data.get("players", []),
                data.get("banned_ips", []),
                source_states,
            )
        except Exception:
            logger.debug("Player directory cache persistence failed", exc_info=True)

    def _with_runtime_directory_data(self, data: dict) -> dict:
        directory: dict[str, dict] = {}
        for player in data.get("players", []):
            _merge_directory_player(directory, player, "cache")
        for name in self._list_known_snapshot_players():
            _merge_directory_player(directory, _make_player_entry(name), "snapshot")
        if self._get_server_state() in ("running", "starting"):
            with self._lock:
                online_players = list(self._online_players.values())
            for player in online_players:
                _merge_directory_player(directory, player, "online", is_online=True)
        players = sorted(
            directory.values(),
            key=lambda item: (
                not bool(item.get("is_online")),
                str(item.get("name") or "").lower(),
            ),
        )
        return _player_directory_result(
            players,
            list(data.get("banned_ips", [])),
            data.get("captured_at"),
        )

    def _resolve_local_player_uuid(self, name: str) -> str | None:
        expected_name = str(name or "").strip().lower()
        if not expected_name:
            return None

        with self._lock:
            for player in self._online_players.values():
                if str(player.get("name") or "").strip().lower() == expected_name:
                    uuid_value = _normalize_uuid(player.get("uuid"))
                    if uuid_value:
                        return uuid_value

        cached = self._get_player_directory_cache()
        if cached:
            for player in cached.get("players", []):
                if str(player.get("name") or "").strip().lower() == expected_name:
                    uuid_value = _normalize_uuid(player.get("uuid"))
                    if uuid_value:
                        return uuid_value

        server_dir = getattr(self._settings, "mc_server_dir", None)
        if server_dir is None:
            return None
        server_path = Path(server_dir)
        return (
            _resolve_uuid_from_usercache(name, server_path)
            or _resolve_uuid_from_operator_config(name, server_path)
            or _resolve_uuid_from_banned_player_config(name, server_path)
        )

    def _invalidate_file_caches(self, relative_path: object) -> None:
        if relative_path == OPERATOR_CONFIG_PATH:
            self._operator_cache_mtime_ns = None


def _parse_list_uuids_response(response: str) -> tuple[dict[str, dict], int]:
    match = _LIST_RE.search(response)
    if not match:
        return {}, 0
    max_players = int(match.group("max"))
    player_part = (match.group("players") or "").strip()
    if not player_part:
        return {}, max_players
    result: dict[str, dict] = {}
    for entry_match in _UUID_ENTRY_RE.finditer(player_part):
        name = entry_match.group(1)
        uuid_str = entry_match.group(2)
        result[name] = _make_player_entry(name, uuid_str)
    leftover = _UUID_ENTRY_RE.sub("", player_part)
    for name in _split_names(leftover):
        if name and name not in result:
            result[name] = _make_player_entry(name)
    return result, max_players


def _parse_list_response(response: str) -> tuple[dict[str, dict], int]:
    match = _LIST_RE.search(response)
    if not match:
        return {}, 0
    max_players = int(match.group("max"))
    player_part = (match.group("players") or "").strip()
    if not player_part:
        return {}, max_players
    return {name: _make_player_entry(name) for name in _split_names(player_part) if name}, max_players


def _split_names(text: str) -> list[str]:
    return [n.strip().rstrip(",") for n in text.split(",") if n.strip()]


def _new_stdout_lines(previous: list[str], current: list[str]) -> list[str]:
    if not current:
        return []
    if not previous:
        return list(current)

    max_overlap = min(len(previous), len(current))
    for overlap in range(max_overlap, 0, -1):
        if previous[-overlap:] == current[:overlap]:
            return list(current[overlap:])
    return list(current)


def resolve_skin_uuid(name: str, settings: Settings) -> str | None:
    remote_uuid = _resolve_uuid_from_mojang_profile(name)
    if remote_uuid:
        return remote_uuid
    if getattr(settings, "mc_server_dir", None) is None:
        return None
    return _resolve_uuid_from_usercache(name, getattr(settings, "mc_server_dir", None))


def resolve_player_uuid(name: str, settings: Settings) -> str | None:
    server_dir = getattr(settings, "mc_server_dir", None)
    cached_uuid = _resolve_uuid_from_usercache(name, server_dir)
    if cached_uuid:
        return cached_uuid
    return _resolve_uuid_from_mojang_profile(name)


def _resolve_uuid_from_usercache(name: str, server_dir: object) -> str | None:
    if server_dir is None:
        return None
    cache_path = Path(server_dir) / "usercache.json"
    try:
        raw_entries = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw_entries, list):
        return None

    expected_name = name.lower()
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        entry_name = str(entry.get("name", "")).lower()
        if entry_name != expected_name:
            continue
        normalized_uuid = _normalize_uuid(str(entry.get("uuid", "")))
        if normalized_uuid:
            return normalized_uuid
    return None


def _resolve_uuid_from_mojang_profile(name: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", name or ""):
        return None
    url = MOJANG_PROFILE_URL.format(name=quote(name, safe=""))
    request = Request(url, headers={"User-Agent": "mvp-codex-mc-dashboard/1.0"})
    try:
        with urlopen(request, timeout=2.0, context=_ssl_context()) as response:
            payload = response.read(8192)
    except OSError:
        return None
    try:
        profile = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _normalize_uuid(str(profile.get("id", ""))) if isinstance(profile, dict) else None


def _load_operator_lookup(ops_path: Path) -> dict:
    try:
        raw_entries = json.loads(ops_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"uuids": set(), "names": set(), "entries": {}}
    if not isinstance(raw_entries, list):
        return {"uuids": set(), "names": set(), "entries": {}}

    uuids: set[str] = set()
    names: set[str] = set()
    entries: dict[str, dict] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        normalized_uuid = _normalize_uuid(str(raw_entry.get("uuid", "")))
        normalized_name = str(raw_entry.get("name", "")).strip().lower()
        entry = {
            "uuid": normalized_uuid,
            "name": str(raw_entry.get("name", "")).strip() or None,
            "level": _operator_level(raw_entry.get("level")),
            "bypasses_player_limit": bool(raw_entry.get("bypassesPlayerLimit", False)),
        }
        if normalized_uuid:
            uuids.add(normalized_uuid)
            entries[normalized_uuid] = entry
        if normalized_name:
            names.add(normalized_name)
            entries[normalized_name] = entry
    return {"uuids": uuids, "names": names, "entries": entries}


def _unique_operator_entries(operator_lookup: dict) -> list[dict]:
    entries = []
    seen: set[str] = set()
    for entry in operator_lookup.get("entries", {}).values():
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("uuid") or entry.get("name") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return entries


def _load_usercache_players(server_dir: Path) -> list[dict]:
    players = []
    for raw_entry in _load_json_list(server_dir / "usercache.json"):
        name = str(raw_entry.get("name", "")).strip()
        if not name:
            continue
        player = _make_player_entry(name, str(raw_entry.get("uuid", "")))
        expires_on = str(raw_entry.get("expiresOn", "")).strip()
        if expires_on:
            player["expires_on"] = expires_on
        players.append(player)
    return players


def _load_banned_player_lookup(server_dir: Path) -> dict:
    items = []
    entries: dict[str, dict] = {}
    for raw_entry in _load_json_list(server_dir / "banned-players.json"):
        name = str(raw_entry.get("name", "")).strip()
        normalized_uuid = _normalize_uuid(str(raw_entry.get("uuid", "")))
        if not name and not normalized_uuid:
            continue
        item = {
            "uuid": normalized_uuid,
            "name": name or None,
            "created": str(raw_entry.get("created", "")).strip() or None,
            "source": str(raw_entry.get("source", "")).strip() or None,
            "expires": str(raw_entry.get("expires", "")).strip() or None,
            "reason": str(raw_entry.get("reason", "")).strip() or None,
        }
        items.append(item)
        if normalized_uuid:
            entries[normalized_uuid] = item
        if name:
            entries[name.lower()] = item
    return {"entries": entries, "items": items}


def _parse_offline_player_command(command: str) -> dict | None:
    parts = command.strip().split()
    if len(parts) < 2:
        return None
    raw_action = parts[0].lower()
    action_aliases = {
        "tempipban": "tempbanip",
    }
    action = action_aliases.get(raw_action, raw_action)
    target = parts[1].strip()
    if not target:
        return None
    if action in {"op", "deop", "pardon", "pardon-ip"}:
        return {"action": action, "target": target}
    if action in {"ban", "ban-ip"}:
        reason = " ".join(parts[2:]).strip() or None
        return {"action": action, "target": target, "reason": reason}
    if action in {"tempban", "tempbanip"}:
        if len(parts) < 3:
            return None
        expires = _duration_to_minecraft_expires(parts[2])
        if expires is None:
            return None
        reason = " ".join(parts[3:]).strip() or None
        return {
            "action": action,
            "target": target,
            "expires": expires,
            "reason": reason,
        }
    return None


def _set_operator_config(
    server_dir: Path,
    name: str,
    uuid_value: str | None,
    *,
    enabled: bool,
) -> dict:
    path = server_dir / OPERATOR_CONFIG_PATH
    entries, load_error = _load_json_list_for_update(path)
    if load_error:
        return _invalid_offline_config_result(
            command=f"{'op' if enabled else 'deop'} {name}",
            relative_path=OPERATOR_CONFIG_PATH,
            error=load_error,
        )
    if enabled and not uuid_value:
        return _offline_config_result(
            command=f"op {name}",
            status="failed",
            message="未找到玩家 UUID，无法离线写入 ops.json。请先让玩家至少登录一次。",
            error_message="未找到玩家 UUID，无法离线写入 ops.json。请先让玩家至少登录一次。",
            relative_path=OPERATOR_CONFIG_PATH,
        )

    matched = False
    changed = False
    normalized_name = name.lower()
    next_entries: list[dict] = []
    for raw_entry in entries:
        entry = dict(raw_entry)
        entry_uuid = _normalize_uuid(str(entry.get("uuid", "")))
        entry_name = str(entry.get("name", "")).strip().lower()
        is_match = (
            (uuid_value is not None and entry_uuid == uuid_value)
            or (entry_name and entry_name == normalized_name)
        )
        if not is_match:
            next_entries.append(entry)
            continue
        matched = True
        if enabled:
            updated = {
                **entry,
                "uuid": uuid_value,
                "name": name,
                "level": _operator_level(entry.get("level")) or 4,
                "bypassesPlayerLimit": bool(entry.get("bypassesPlayerLimit", False)),
            }
            if updated != entry:
                changed = True
            next_entries.append(updated)
        else:
            changed = True

    if enabled and not matched:
        next_entries.append(
            {
                "uuid": uuid_value,
                "name": name,
                "level": 4,
                "bypassesPlayerLimit": False,
            }
        )
        changed = True

    if not changed:
        return _offline_config_result(
            command=f"{'op' if enabled else 'deop'} {name}",
            status="no_change",
            message=(
                f"{name} 已经是管理员。"
                if enabled
                else f"{name} 当前不在管理员名单中。"
            ),
            relative_path=OPERATOR_CONFIG_PATH,
        )

    _write_json_list(path, next_entries)
    return _offline_config_result(
        command=f"{'op' if enabled else 'deop'} {name}",
        status="file_updated",
        message=(
            f"服务器未运行，已更新 {OPERATOR_CONFIG_PATH}；下次启动后生效。"
            if enabled
            else f"服务器未运行，已从 {OPERATOR_CONFIG_PATH} 移除管理员；下次启动后生效。"
        ),
        relative_path=OPERATOR_CONFIG_PATH,
    )


def _set_banned_player_config(
    server_dir: Path,
    name: str,
    uuid_value: str | None,
    *,
    banned: bool,
    expires: str = "forever",
    reason: str = DEFAULT_BAN_REASON,
) -> dict:
    path = server_dir / BANNED_PLAYERS_CONFIG_PATH
    entries, load_error = _load_json_list_for_update(path)
    if load_error:
        return _invalid_offline_config_result(
            command=f"{'ban' if banned else 'pardon'} {name}",
            relative_path=BANNED_PLAYERS_CONFIG_PATH,
            error=load_error,
        )
    if banned and not uuid_value:
        return _offline_config_result(
            command=f"ban {name}",
            status="failed",
            message=(
                "未找到玩家 UUID，无法离线写入 banned-players.json。"
                "请先让玩家至少登录一次。"
            ),
            error_message=(
                "未找到玩家 UUID，无法离线写入 banned-players.json。"
                "请先让玩家至少登录一次。"
            ),
            relative_path=BANNED_PLAYERS_CONFIG_PATH,
        )

    matched = False
    changed = False
    normalized_name = name.lower()
    now = _minecraft_timestamp()
    next_entries: list[dict] = []
    for raw_entry in entries:
        entry = dict(raw_entry)
        entry_uuid = _normalize_uuid(str(entry.get("uuid", "")))
        entry_name = str(entry.get("name", "")).strip().lower()
        is_match = (
            (uuid_value is not None and entry_uuid == uuid_value)
            or (entry_name and entry_name == normalized_name)
        )
        if not is_match:
            next_entries.append(entry)
            continue
        matched = True
        if banned:
            updated = {
                **entry,
                "uuid": uuid_value,
                "name": name,
                "created": str(entry.get("created") or now),
                "source": str(entry.get("source") or MINECRAFT_CONFIG_SOURCE),
                "expires": expires,
                "reason": reason,
            }
            if updated != entry:
                changed = True
            next_entries.append(updated)
        else:
            changed = True

    if banned and not matched:
        next_entries.append(
            {
                "uuid": uuid_value,
                "name": name,
                "created": now,
                "source": MINECRAFT_CONFIG_SOURCE,
                "expires": expires,
                "reason": reason,
            }
        )
        changed = True

    if not changed:
        return _offline_config_result(
            command=f"{'ban' if banned else 'pardon'} {name}",
            status="no_change",
            message=(
                f"{name} 已经在封禁名单中。"
                if banned
                else f"{name} 当前不在封禁名单中。"
            ),
            relative_path=BANNED_PLAYERS_CONFIG_PATH,
        )

    _write_json_list(path, next_entries)
    return _offline_config_result(
        command=f"{'ban' if banned else 'pardon'} {name}",
        status="file_updated",
        message=(
            f"服务器未运行，已更新 {BANNED_PLAYERS_CONFIG_PATH}；下次启动后生效。"
            if banned
            else f"服务器未运行，已从 {BANNED_PLAYERS_CONFIG_PATH} 移除封禁；下次启动后生效。"
        ),
        relative_path=BANNED_PLAYERS_CONFIG_PATH,
    )


def _set_banned_ip_config(
    server_dir: Path,
    target: str,
    *,
    banned: bool,
    expires: str = "forever",
    reason: str = DEFAULT_BAN_REASON,
    log_path: object = None,
) -> dict:
    ip = target if _looks_like_ip(target) else _resolve_latest_ip_for_player(target, log_path)
    if not ip:
        return _offline_config_result(
            command=f"{'ban-ip' if banned else 'pardon-ip'} {target}",
            status="failed",
            message="未找到该玩家最近登录 IP，无法离线写入 banned-ips.json。",
            error_message="未找到该玩家最近登录 IP，无法离线写入 banned-ips.json。",
            relative_path=BANNED_IPS_CONFIG_PATH,
    )

    path = server_dir / BANNED_IPS_CONFIG_PATH
    entries, load_error = _load_json_list_for_update(path)
    if load_error:
        return _invalid_offline_config_result(
            command=f"{'ban-ip' if banned else 'pardon-ip'} {target}",
            relative_path=BANNED_IPS_CONFIG_PATH,
            error=load_error,
        )
    matched = False
    changed = False
    now = _minecraft_timestamp()
    next_entries: list[dict] = []
    for raw_entry in entries:
        entry = dict(raw_entry)
        entry_ip = str(entry.get("ip", "")).strip()
        if entry_ip != ip:
            next_entries.append(entry)
            continue
        matched = True
        if banned:
            updated = {
                **entry,
                "ip": ip,
                "created": str(entry.get("created") or now),
                "source": str(entry.get("source") or MINECRAFT_CONFIG_SOURCE),
                "expires": expires,
                "reason": reason,
            }
            if updated != entry:
                changed = True
            next_entries.append(updated)
        else:
            changed = True

    if banned and not matched:
        next_entries.append(
            {
                "ip": ip,
                "created": now,
                "source": MINECRAFT_CONFIG_SOURCE,
                "expires": expires,
                "reason": reason,
            }
        )
        changed = True

    if not changed:
        return _offline_config_result(
            command=f"{'ban-ip' if banned else 'pardon-ip'} {target}",
            status="no_change",
            message=(
                f"{ip} 已经在 IP 封禁名单中。"
                if banned
                else f"{ip} 当前不在 IP 封禁名单中。"
            ),
            relative_path=BANNED_IPS_CONFIG_PATH,
        )

    _write_json_list(path, next_entries)
    return _offline_config_result(
        command=f"{'ban-ip' if banned else 'pardon-ip'} {target}",
        status="file_updated",
        message=(
            f"服务器未运行，已更新 {BANNED_IPS_CONFIG_PATH}；下次启动后生效。"
            if banned
            else f"服务器未运行，已从 {BANNED_IPS_CONFIG_PATH} 移除 IP 封禁；下次启动后生效。"
        ),
        relative_path=BANNED_IPS_CONFIG_PATH,
    )


def _resolve_uuid_from_operator_config(name: str, server_dir: Path) -> str | None:
    expected_name = str(name or "").strip().lower()
    if not expected_name:
        return None
    for raw_entry in _load_json_list(server_dir / OPERATOR_CONFIG_PATH):
        if str(raw_entry.get("name", "")).strip().lower() != expected_name:
            continue
        uuid_value = _normalize_uuid(str(raw_entry.get("uuid", "")))
        if uuid_value:
            return uuid_value
    return None


def _resolve_uuid_from_banned_player_config(name: str, server_dir: Path) -> str | None:
    expected_name = str(name or "").strip().lower()
    if not expected_name:
        return None
    for raw_entry in _load_json_list(server_dir / BANNED_PLAYERS_CONFIG_PATH):
        if str(raw_entry.get("name", "")).strip().lower() != expected_name:
            continue
        uuid_value = _normalize_uuid(str(raw_entry.get("uuid", "")))
        if uuid_value:
            return uuid_value
    return None


def _resolve_latest_ip_for_player(name: str, log_path: object) -> str | None:
    ip_map = _load_login_ip_map(log_path)
    ips = ip_map.get("players", {}).get(str(name or "").strip())
    if not ips:
        return None
    return sorted(ips)[-1]


def _looks_like_ip(value: str) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", candidate):
        return True
    return ":" in candidate and all(part for part in candidate.split(":"))


def _duration_to_minecraft_expires(duration: str) -> str | None:
    delta = _duration_to_timedelta(duration)
    if delta is None:
        return None
    return _minecraft_timestamp(datetime.now().astimezone() + delta)


def _duration_to_timedelta(duration: str) -> timedelta | None:
    match = re.fullmatch(r"([1-9][0-9]{0,3})(s|m|h|d|w|mo|y)", duration.strip().lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "w":
        return timedelta(weeks=amount)
    if unit == "mo":
        return timedelta(days=amount * 30)
    if unit == "y":
        return timedelta(days=amount * 365)
    return None


def _minecraft_timestamp(moment: datetime | None = None) -> str:
    value = moment or datetime.now().astimezone()
    if value.tzinfo is None:
        value = value.astimezone()
    return value.strftime("%Y-%m-%d %H:%M:%S %z")


def _write_json_list(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_json_list_for_update(path: Path) -> tuple[list[dict], str | None]:
    if not path.exists():
        return [], None
    try:
        raw_entries = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [], f"JSON 解析失败：{exc.msg}"
    except OSError as exc:
        return [], str(exc)
    if not isinstance(raw_entries, list):
        return [], "文件内容必须是 JSON 数组。"
    entries: list[dict] = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            return [], f"第 {index + 1} 项不是 JSON 对象。"
        entries.append(raw_entry)
    return entries, None


def _invalid_offline_config_result(
    *,
    command: str,
    relative_path: str,
    error: str,
) -> dict:
    message = f"{relative_path} 格式无效，无法执行离线配置兜底：{error}"
    return _offline_config_result(
        command=command,
        status="failed",
        message=message,
        error_message=message,
        relative_path=relative_path,
    )


def _offline_config_result(
    *,
    command: str,
    status: str,
    message: str,
    relative_path: str | None = None,
    error_message: str | None = None,
) -> dict:
    return {
        "status": status,
        "command": command,
        "normalized_command": " ".join(command.strip().split()).lower(),
        "risk_level": "HIGH",
        "confirmation_required": True,
        "message": message,
        "output": message if status in {"file_updated", "no_change"} else None,
        "error_message": error_message,
        "fallback": "config_file",
        "relative_path": relative_path,
    }


def _load_banned_ip_entries(server_dir: Path, ip_map: dict) -> list[dict]:
    entries = []
    ip_to_players = ip_map.get("ips", {})
    for raw_entry in _load_json_list(server_dir / "banned-ips.json"):
        ip = str(raw_entry.get("ip", "")).strip()
        if not ip:
            continue
        players = sorted(ip_to_players.get(ip, set()), key=str.lower)
        entries.append(
            {
                "ip": ip,
                "players": players,
                "created": str(raw_entry.get("created", "")).strip() or None,
                "source": str(raw_entry.get("source", "")).strip() or None,
                "expires": str(raw_entry.get("expires", "")).strip() or None,
                "reason": str(raw_entry.get("reason", "")).strip() or None,
                "mapping_source": "latest.log" if players else None,
            }
        )
    return sorted(entries, key=lambda item: item["ip"])


def _load_login_ip_map(log_path: object) -> dict:
    players: dict[str, set[str]] = {}
    ips: dict[str, set[str]] = {}
    if log_path is None:
        return {"players": players, "ips": ips}
    for line in _read_log_tail(Path(log_path)).splitlines():
        match = _LOGIN_ADDRESS_PATTERN.search(line)
        if not match:
            continue
        player = match.group("player")
        ip = _extract_ip_from_login_address(match.group("address"))
        if not ip:
            continue
        players.setdefault(player, set()).add(ip)
        ips.setdefault(ip, set()).add(player)
    return {"players": players, "ips": ips}


def _player_directory_result(
    players: list[dict],
    banned_ips: list[dict],
    captured_at: str | None = None,
) -> dict:
    return {
        "players": players,
        "banned_ips": banned_ips,
        "counts": {
            "players": len(players),
            "operators": sum(1 for player in players if player.get("is_operator")),
            "banned_players": sum(1 for player in players if player.get("is_banned")),
            "banned_ips": len(banned_ips),
        },
        "captured_at": captured_at or utc_now_iso(),
        "ip_mapping_note": (
            "banned-ips.json 不保存玩家名；这里的玩家映射来自 latest.log 登录记录，"
            "只代表最近日志中能看到的 IP 对应关系。"
        ),
    }


def _player_directory_source_states(
    server_dir: object,
    log_path: object,
) -> list[dict]:
    server_path = Path(server_dir) if server_dir is not None else None
    return [
        _file_source_state(
            "usercache.json",
            server_path / "usercache.json" if server_path is not None else None,
        ),
        _file_source_state(
            "ops.json",
            server_path / "ops.json" if server_path is not None else None,
        ),
        _file_source_state(
            "banned-players.json",
            server_path / "banned-players.json" if server_path is not None else None,
        ),
        _file_source_state(
            "banned-ips.json",
            server_path / "banned-ips.json" if server_path is not None else None,
        ),
        _file_source_state(
            "latest.log",
            Path(log_path) if log_path is not None else None,
        ),
    ]


def _file_source_state(source: str, path: Path | None) -> dict:
    if path is None:
        return {"source": source, "mtime_ns": None, "size_bytes": None}
    try:
        stat = path.stat()
    except OSError:
        return {"source": source, "mtime_ns": None, "size_bytes": None}
    return {
        "source": source,
        "mtime_ns": stat.st_mtime_ns,
        "size_bytes": stat.st_size,
    }


def _extract_ip_from_login_address(address: str) -> str | None:
    normalized = address.strip().lstrip("/")
    if ":" not in normalized:
        return normalized or None
    host, _port = normalized.rsplit(":", 1)
    return host or None


def _read_log_tail(log_path: Path, max_bytes: int = PLAYER_DIRECTORY_LOG_TAIL_BYTES) -> str:
    try:
        size = log_path.stat().st_size
        with log_path.open("rb") as file:
            if size > max_bytes:
                file.seek(size - max_bytes)
            payload = file.read(max_bytes)
    except OSError:
        return ""
    return payload.decode("utf-8", errors="replace")


def _load_json_list(path: Path) -> list[dict]:
    try:
        raw_entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw_entries, list):
        return []
    return [entry for entry in raw_entries if isinstance(entry, dict)]


def _merge_directory_player(
    directory: dict[str, dict],
    player: dict,
    source: str,
    is_online: bool = False,
    is_operator: bool = False,
    operator_level: int | None = None,
    is_banned: bool = False,
    ban: dict | None = None,
    known_ips: set[str] | list[str] | None = None,
) -> None:
    name = str(player.get("name") or "").strip()
    if not name:
        return
    key = name.lower()
    player_known_ips = [
        str(ip) for ip in (player.get("known_ips") or []) if str(ip).strip()
    ]
    player_sources = [
        str(item) for item in (player.get("sources") or []) if str(item).strip()
    ]
    existing = directory.setdefault(
        key,
        {
            "name": name,
            "uuid": _normalize_uuid(player.get("uuid")),
            "avatar_url": player.get("avatar_url") or _avatar_url(name),
            "is_online": False,
            "is_operator": False,
            "operator_level": None,
            "is_banned": False,
            "ban": player.get("ban"),
            "known_ips": sorted(set(player_known_ips)),
            "sources": list(dict.fromkeys(player_sources)),
        },
    )
    existing["name"] = existing.get("name") or name
    existing["uuid"] = existing.get("uuid") or _normalize_uuid(player.get("uuid"))
    existing["avatar_url"] = (
        existing.get("avatar_url") or player.get("avatar_url") or _avatar_url(name)
    )
    existing["is_online"] = bool(existing.get("is_online") or is_online)
    existing["is_operator"] = bool(
        existing.get("is_operator")
        or is_operator
        or player.get("is_operator")
    )
    existing["operator_level"] = (
        existing.get("operator_level")
        or operator_level
        or player.get("operator_level")
    )
    existing["is_banned"] = bool(
        existing.get("is_banned")
        or is_banned
        or player.get("is_banned")
    )
    existing["ban"] = existing.get("ban") or ban or player.get("ban")
    merged_ips = set(existing.get("known_ips") or [])
    merged_ips.update(player_known_ips)
    if known_ips:
        merged_ips.update(str(ip) for ip in known_ips if str(ip).strip())
    existing["known_ips"] = sorted(merged_ips)
    sources = list(existing.get("sources") or [])
    for player_source in player_sources:
        if player_source not in sources:
            sources.append(player_source)
    if source not in sources:
        sources.append(source)
    existing["sources"] = sources


def _operator_level(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _make_player_entry(
    name: str,
    uuid_str: str | None = None,
    skin_uuid_str: str | None = None,
) -> dict:
    normalized_uuid = _normalize_uuid(uuid_str)
    skin_uuid = _normalize_uuid(skin_uuid_str) or normalized_uuid
    return {
        "name": name,
        "uuid": normalized_uuid or skin_uuid,
        "skin_uuid": skin_uuid,
        "avatar_url": _avatar_url(name, skin_uuid),
    }


def _avatar_url(name: str, uuid_str: str | None = None) -> str:
    del uuid_str
    return f"https://minotar.net/helm/{quote(name, safe='')}/40.png"


def _normalize_uuid(uuid_str: str | None) -> str | None:
    if not uuid_str:
        return None
    candidate = uuid_str.strip()
    if not _UUID_TEXT_RE.match(candidate):
        return None
    compact = candidate.replace("-", "").lower()
    return (
        f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-"
        f"{compact[16:20]}-{compact[20:32]}"
    )


def _use_alex_fallback(name: str) -> bool:
    return sum(ord(char) for char in name) % 2 == 1


def _empty_result() -> dict:
    return {
        "online_count": 0,
        "max_players": 20,
        "players": [],
        "captured_at": utc_now_iso(),
        "server_state": "stopped",
    }
