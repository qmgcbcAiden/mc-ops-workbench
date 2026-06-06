from __future__ import annotations

from src.mc.command_policy import BLOCKED, HIGH, LOW, MEDIUM, classify_command


def test_classify_low_risk_command() -> None:
    risk = classify_command(" list ")

    assert risk.normalized_command == "list"
    assert risk.risk_level == LOW
    assert risk.confirmation_required is False


def test_classify_medium_risk_command() -> None:
    risk = classify_command("weather clear")

    assert risk.risk_level == MEDIUM
    assert risk.confirmation_required is False


def test_classify_high_risk_command_requires_confirmation() -> None:
    risk = classify_command("stop")

    assert risk.risk_level == HIGH
    assert risk.confirmation_required is True


def test_classify_player_admin_and_ban_commands_as_high_risk() -> None:
    commands = [
        "op Aiden233",
        "deop Aiden233",
        "ban Aiden233",
        "ban-ip Aiden233",
        "tempban Aiden233 1d",
        "tempbanip Aiden233 1d",
        "tempipban Aiden233 1d",
    ]

    for command in commands:
        risk = classify_command(command)
        assert risk.risk_level == HIGH
        assert risk.confirmation_required is True


def test_classify_blocks_shell_like_input() -> None:
    risk = classify_command("powershell Get-ChildItem")

    assert risk.risk_level == BLOCKED
    assert risk.confirmation_required is False
