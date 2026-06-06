from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from src.mc.server_log_tail import LogTailer
from src.repositories._time import utc_now_iso
from src.repositories.event_repository import EventRepository


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_LOG_LEVEL = r"TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|SEVERE"
_LEVEL_RE = re.compile(rf"\b({_LOG_LEVEL})\b", re.IGNORECASE)
_TIME_RE = re.compile(r"\b(\d{2}:\d{2}:\d{2})\b")

_LOG_PATTERNS = [
    re.compile(
        r"^\d{4}-\d{2}-\d{2}\s+"
        r"(?P<time>\d{2}:\d{2}:\d{2})(?:[,.]\d{1,9})?\s+"
        rf".*?\b(?P<level>{_LOG_LEVEL})\b\s*:?\s*"
        r"(?P<message>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\[[^\]\r\n]*?(?P<time>\d{2}:\d{2}:\d{2})(?:[,.]\d{1,9})?\]\s+"
        rf"\[[^\]]*/(?P<level>{_LOG_LEVEL})\]\s*"
        r"(?:\[[^\]]+/[^\]]*\]|\([^\)]+/[^\)]*\))?\s*:?\s*"
        r"(?P<message>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\[[^\]\r\n]*?(?P<time>\d{2}:\d{2}:\d{2})(?:[,.]\d{1,9})?\]\s+"
        r"\[[^\]]+\]\s+"
        rf"\[[^\]]*/(?P<level>{_LOG_LEVEL})\]\s*:?\s*"
        r"(?P<message>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\[[^\]\r\n]*?(?P<time>\d{2}:\d{2}:\d{2})(?:[,.]\d{1,9})?\]\s+"
        rf"\[(?P<level>{_LOG_LEVEL})\]\s*"
        r"(?:\[[^\]]+/[^\]]*\]|\([^\)]+/[^\)]*\))?\s*:?\s*"
        r"(?P<message>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\[[^\]\r\n]*?(?P<time>\d{2}:\d{2}:\d{2})(?:[,.]\d{1,9})?\s+"
        rf"(?P<level>{_LOG_LEVEL})\]\s*:?\s*"
        r"(?P<message>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<time>\d{2}:\d{2}:\d{2})(?:[,.]\d{1,9})?\s+"
        rf"(?:\[(?P<bracket_level>{_LOG_LEVEL})\]|(?P<plain_level>{_LOG_LEVEL}))"
        r":?\s*"
        r"(?P<message>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^\[[^\]]*/(?P<level>{_LOG_LEVEL})\]\s*"
        r"(?:\[[^\]]+/[^\]]*\]|\([^\)]+/[^\)]*\))?\s*:?\s*"
        r"(?P<message>.*)$",
        re.IGNORECASE,
    ),
]


class LogService:
    def __init__(self, event_repository: EventRepository, log_path: Path):
        self.event_repository = event_repository
        self.log_path = log_path
        self._tailer = LogTailer(log_path)

    def ingest_latest(self) -> int:
        if not self.log_path.is_file():
            return 0

        inserted = 0
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return 0

        for raw_line in lines:
            if self.event_repository.insert_event(parse_log_line(raw_line)):
                inserted += 1
        return inserted

    def load_recent_events(self, limit: int = 300) -> list[dict]:
        self.ingest_latest()
        self._tailer.seek_to_end()
        return self.event_repository.list_recent(level="ANY", limit=limit)

    def prime_tail_to_end(self) -> None:
        self._tailer.seek_to_end()

    def tail_new_events(self, persist: bool = True, limit: int | None = None) -> list[dict]:
        events: list[dict] = []
        raw_lines = self._tailer.read_new_lines(limit=limit)
        if self._tailer.last_skipped_count:
            events.append(make_overflow_event(self._tailer.last_skipped_count))
        for raw_line in raw_lines:
            event = parse_log_line(raw_line)
            if persist:
                self.event_repository.insert_event(event)
            events.append(event)
        return events

    def get_visible_events(
        self,
        level: str = "ANY",
        keyword: str = "",
        limit: int = 300,
    ) -> list[dict]:
        level = normalize_level(level)
        keyword = (keyword or "").strip()
        if keyword:
            return self.event_repository.search(keyword=keyword, level=level, limit=limit)
        return self.event_repository.list_recent(level=level, limit=limit)

    def list_recent(self, level: str = "ANY", limit: int = 100) -> list[dict]:
        self.ingest_latest()
        return self.event_repository.list_recent(level=normalize_level(level), limit=limit)

    def capture_cursor(self) -> int:
        self.ingest_latest()
        return self.event_repository.latest_id()

    def list_since(self, cursor: int, limit: int = 100) -> list[dict]:
        self.ingest_latest()
        return self.event_repository.list_after_id(cursor, limit=limit)

    def search(self, keyword: str, level: str = "ANY", limit: int = 100) -> list[dict]:
        self.ingest_latest()
        keyword = (keyword or "").strip()
        if not keyword:
            return self.event_repository.list_recent(level=normalize_level(level), limit=limit)
        return self.event_repository.search(keyword=keyword, level=normalize_level(level), limit=limit)

    def append_sample_event(self) -> dict:
        time_text = datetime.now().strftime("%H:%M:%S")
        raw_line = f"[{time_text} INFO]: Manual UI test event"
        event = parse_log_line(raw_line)
        self.event_repository.insert_event(event)
        return event


def parse_log_line(raw_line: str) -> dict:
    cleaned = _ANSI_RE.sub("", raw_line.strip())
    event_time, level, message, is_structured = _parse_clean_line(cleaned)
    return {
        "event_time": event_time,
        "level": normalize_level(level),
        "category": "Server thread",
        "player_id": extract_player_id(message),
        "message": _truncate_message(message),
        "raw_line": raw_line,
        "raw_hash": hashlib.sha256(raw_line.encode("utf-8")).hexdigest(),
        "created_at": utc_now_iso(),
        "is_structured": is_structured,
    }


def make_overflow_event(skipped_count: int) -> dict:
    created_at = utc_now_iso()
    message = f"日志输出过快，已折叠较早的 {skipped_count} 条实时日志以保持界面响应。"
    return {
        "event_time": None,
        "level": "WARN",
        "category": "UI",
        "player_id": None,
        "message": message,
        "raw_line": message,
        "raw_hash": f"ui-overflow-{created_at}-{skipped_count}",
        "created_at": created_at,
        "is_structured": False,
    }


def normalize_level(level: str | None) -> str:
    normalized = (level or "ANY").upper()
    if normalized == "WARNING":
        return "WARN"
    if normalized in {"FATAL", "SEVERE"}:
        return "ERROR"
    if normalized in {"TRACE", "DEBUG"}:
        return "INFO"
    if normalized in {"ANY", "INFO", "WARN", "ERROR"}:
        return normalized
    return "ANY"


def extract_player_id(message: str) -> str | None:
    player_patterns = [
        r"^(?P<player>[A-Za-z0-9_]{3,16}) joined the game\b",
        r"^(?P<player>[A-Za-z0-9_]{3,16}) lost connection\b",
        r"^(?P<player>[A-Za-z0-9_]{3,16}) left the game\b",
        r"^(?P<player>[A-Za-z0-9_]{3,16}) moved (?:too quickly|wrongly)\b",
        r"^(?P<player>[A-Za-z0-9_]{3,16}) issued server command\b",
    ]
    for pattern in player_patterns:
        match = re.search(pattern, message)
        if match:
            return match.group("player")
    return None


def _parse_clean_line(cleaned: str) -> tuple[str | None, str, str, bool]:
    for pattern in _LOG_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            groups = match.groupdict()
            return (
                groups.get("time"),
                groups.get("level") or groups.get("bracket_level") or groups.get("plain_level") or "INFO",
                match.group("message").strip(),
                True,
            )

    level_match = _LEVEL_RE.search(cleaned)
    level = level_match.group(1) if level_match else "INFO"
    return _extract_time(cleaned), level, cleaned, False


def _extract_time(text: str) -> str | None:
    match = _TIME_RE.search(text)
    return match.group(1) if match else None


def _truncate_message(message: str, max_len: int = 240) -> str:
    if len(message) <= max_len:
        return message
    return message[: max_len - 1] + "…"
