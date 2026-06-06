from __future__ import annotations

from src.config.settings import Settings
from src.mc.server_capabilities import detect_server_capabilities


class ServerCapabilityService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_capabilities(self) -> dict:
        capabilities = detect_server_capabilities(
            self._settings.mc_server_dir,
            self._settings.mc_server_jar,
        )
        return capabilities.to_dict()
