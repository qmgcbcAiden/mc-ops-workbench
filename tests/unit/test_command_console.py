from __future__ import annotations

from src.ui.components.command_console import CommandConsole


class CommandInterfaceStub:
    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def submit_command(
        self,
        command: str,
        requested_by: str = "ui",
        user_confirmed: bool = False,
    ) -> dict:
        self.submitted.append({
            "command": command,
            "requested_by": requested_by,
            "user_confirmed": user_confirmed,
        })
        return {
            "status": "executed",
            "command": command,
            "output": "There are 0 of a max of 20 players online.",
        }

    def list_command_audits(self, limit: int = 5) -> list[dict]:
        del limit
        return [
            {
                "created_at": "2026-05-25T06:00:00",
                "command": "list",
                "risk_level": "LOW",
                "status": "executed",
            }
        ]


def test_enter_submitting_command_requests_host_update_after_audit_refresh() -> None:
    updates: list[str] = []
    results: list[dict] = []
    interface = CommandInterfaceStub()
    console = CommandConsole(
        interface,
        on_change=lambda: updates.append("updated"),
        on_result=results.append,
    )
    console.command_input.value = "list"

    console.command_input.on_submit(None)

    assert interface.submitted == [
        {
            "command": "list",
            "requested_by": "ui",
            "user_confirmed": True,
        }
    ]
    history_list = console.history_panel.content.controls[0]
    assert len(history_list.controls) == 1
    assert results[0]["status"] == "executed"
    assert updates == ["updated"]


def test_manual_console_submission_is_treated_as_user_confirmed() -> None:
    interface = CommandInterfaceStub()
    console = CommandConsole(interface)
    console.command_input.value = "deop Aiden233"

    console.execute()

    assert interface.submitted == [
        {
            "command": "deop Aiden233",
            "requested_by": "ui",
            "user_confirmed": True,
        }
    ]


def test_command_history_opens_as_overlay_list() -> None:
    console = CommandConsole(CommandInterfaceStub())

    console.history_button.on_click(None)

    assert console.history_panel.visible is True
    assert console.history_button.selected is True
    assert len(console.history_panel.content.controls[0].controls) == 1
    footer = console.history_panel.content.controls[1]
    assert footer.content.controls[1].value == "命令历史"

    console.close_history()

    assert console.history_panel.visible is False
    assert console.history_button.selected is False
