from __future__ import annotations

from src.service.server_service import ServerService


class ServerInterface:
    def __init__(self, server_service: ServerService) -> None:
        self._service = server_service

    def get_server_status(self) -> dict:
        return self._service.get_status()

    def start_server(self) -> dict:
        return self._service.start_server()

    def stop_server(self) -> dict:
        return self._service.stop_server()

    def shutdown_server(self, force: bool = True) -> dict:
        return self._service.shutdown_server(force=force)

    def send_command(self, command: str) -> dict:
        return self._service.send_command(command)

    def drain_stdout_events(self, persist: bool = True) -> list[dict]:
        return self._service.drain_stdout_events(persist=persist)
