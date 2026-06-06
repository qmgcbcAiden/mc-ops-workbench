from __future__ import annotations

from src.service.log_service import LogService


class LogInterface:
    def __init__(self, log_service: LogService):
        self.log_service = log_service

    def refresh_logs(self) -> dict:
        inserted = self.log_service.ingest_latest()
        return {"inserted": inserted}

    def append_sample_log(self) -> dict:
        return self.log_service.append_sample_event()

    def load_recent_events(self, limit: int = 300) -> list[dict]:
        return self.log_service.load_recent_events(limit=limit)

    def prime_tail_to_end(self) -> None:
        self.log_service.prime_tail_to_end()

    def tail_new_logs(self, persist: bool = True, limit: int | None = None) -> list[dict]:
        return self.log_service.tail_new_events(persist=persist, limit=limit)

    def get_visible_events(self, level: str = "ANY", keyword: str = "", limit: int = 300) -> list[dict]:
        return self.log_service.get_visible_events(level=level, keyword=keyword, limit=limit)

    def get_recent_logs(self, level: str = "ANY", limit: int = 100) -> list[dict]:
        return self.log_service.list_recent(level=level, limit=limit)

    def search_logs(self, keyword: str, level: str = "ANY", limit: int = 100) -> list[dict]:
        return self.log_service.search(keyword=keyword, level=level, limit=limit)
