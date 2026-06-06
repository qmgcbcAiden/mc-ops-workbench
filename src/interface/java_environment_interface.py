from __future__ import annotations

from src.service.java_environment_service import JavaEnvironmentService


class JavaEnvironmentInterface:
    def __init__(self, service: JavaEnvironmentService) -> None:
        self._service = service

    def check_environment(self, server_version: str | None = None) -> dict:
        return self._service.check_environment(server_version=server_version)

    def ensure_environment(
        self,
        server_version: str | None = None,
        start_after_ready: bool = False,
    ) -> dict:
        return self._service.ensure_environment(
            server_version=server_version,
            start_after_ready=start_after_ready,
        )
