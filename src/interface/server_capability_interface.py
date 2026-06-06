from __future__ import annotations

from src.service.server_capability_service import ServerCapabilityService


class ServerCapabilityInterface:
    def __init__(self, capability_service: ServerCapabilityService):
        self.capability_service = capability_service

    def get_server_capabilities(self) -> dict:
        return self.capability_service.get_capabilities()
