from __future__ import annotations

from src.service.command_service import CommandService


class CommandInterface:
    def __init__(self, command_service: CommandService):
        self.command_service = command_service

    def classify_command(self, command: str) -> dict:
        return self.command_service.classify(command)

    def submit_command(
        self,
        command: str,
        requested_by: str = "ui",
        user_confirmed: bool = False,
    ) -> dict:
        return self.command_service.submit_command(
            command=command,
            requested_by=requested_by,
            user_confirmed=user_confirmed,
        )

    def list_command_audits(self, limit: int = 5) -> list[dict]:
        return self.command_service.list_recent_audits(limit=limit)

