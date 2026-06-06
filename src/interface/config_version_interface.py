from __future__ import annotations

from typing import Any

from src.service.config_version_service import ConfigVersionService


class ConfigVersionInterface:
    def __init__(self, version_service: ConfigVersionService | None) -> None:
        self._service = version_service

    @property
    def enabled(self) -> bool:
        return self._service is not None

    def list_history(self, relative_path: str, limit: int = 50) -> list[dict[str, Any]]:
        if not self._service:
            return []
        return self._service.list_history(relative_path, limit=limit)

    def diff_commit(self, commit_id: str) -> dict[str, Any]:
        if not self._service:
            return {"status": "disabled"}
        return self._service.diff_commit(commit_id)

    def get_file_at_commit(self, commit_id: str) -> dict[str, Any]:
        if not self._service:
            return {"status": "disabled"}
        return self._service.get_file_at_commit(commit_id)
