from __future__ import annotations

from src.service.system_service import SystemService


class SystemInterface:
    def __init__(self, system_service: SystemService):
        self.system_service = system_service

    def capture_metrics(self, server_pid: int | None = None) -> dict:
        return self.system_service.capture_metrics(server_pid=server_pid)

    def get_recent_metrics(self, limit: int = 120) -> list[dict]:
        return self.system_service.get_recent_metrics(limit=limit)

    def get_server_status(self) -> dict:
        return self.system_service.get_server_status()

