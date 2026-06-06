from __future__ import annotations

from typing import Protocol

from src.mc.server_process import CommandResult


class CommandChannel(Protocol):
    def send(self, command: str) -> CommandResult: ...


class StdinCommandChannel:
    def __init__(self, process: object) -> None:
        self._process = process

    def send(self, command: str) -> CommandResult:
        return self._process.send_command(command)
