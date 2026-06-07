from __future__ import annotations

from typing import Any, Mapping

from src.service.environment_settings_service import EnvironmentSettingsService


class EnvironmentSettingsInterface:
    def __init__(self, service: EnvironmentSettingsService) -> None:
        self._service = service

    def inspect(self) -> dict[str, Any]:
        return self._service.inspect()

    def test_ai_connection(
        self,
        provider: str,
        api_key: str,
        base_url: str,
    ) -> dict[str, Any]:
        return self._service.test_ai_connection(provider, api_key, base_url)

    def save_basic_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._service.save_basic_settings(payload)
