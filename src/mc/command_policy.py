from __future__ import annotations

from dataclasses import dataclass


LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class CommandRisk:
    normalized_command: str
    risk_level: str
    confirmation_required: bool
    message: str


def normalize_command(command: str) -> str:
    return " ".join(command.strip().split()).lower()


def classify_command(command: str) -> CommandRisk:
    normalized = normalize_command(command)
    if not normalized:
        return CommandRisk(
            normalized_command="",
            risk_level=LOW,
            confirmation_required=False,
            message="请输入 Minecraft 控制台命令。",
        )

    blocked_tokens = (
        "rm ",
        "del ",
        "powershell",
        "cmd ",
        "python ",
        "../",
        "..\\",
        "c:\\",
        "copy ",
        "move ",
        "type ",
        "cat ",
        "echo ",
        ">",
        "<",
        "|",
        "&&",
        "||",
    )
    if any(token in normalized for token in blocked_tokens):
        return CommandRisk(
            normalized_command=normalized,
            risk_level=BLOCKED,
            confirmation_required=False,
            message="该输入看起来像 shell、路径或文件操作，不属于 Minecraft 命令。",
        )

    first = normalized.split(" ", 1)[0]
    high_risk = {
        "stop",
        "op",
        "deop",
        "ban",
        "ban-ip",
        "pardon",
        "pardon-ip",
        "tempban",
        "tempbanip",
        "tempipban",
        "whitelist",
        "save-off",
        "save-on",
    }
    medium_risk = {
        "tp",
        "give",
        "weather",
        "gamemode",
        "difficulty",
        "kick",
    }

    if first in high_risk:
        return CommandRisk(
            normalized_command=normalized,
            risk_level=HIGH,
            confirmation_required=True,
            message="高风险命令；P3 仅标记，不阻断发送。",
        )

    if first in medium_risk:
        return CommandRisk(
            normalized_command=normalized,
            risk_level=MEDIUM,
            confirmation_required=False,
            message="中风险命令；P3 仅标记，不阻断发送。",
        )

    return CommandRisk(
        normalized_command=normalized,
        risk_level=LOW,
        confirmation_required=False,
        message="低风险命令。",
    )
