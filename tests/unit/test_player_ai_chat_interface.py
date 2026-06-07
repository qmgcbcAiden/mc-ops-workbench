from __future__ import annotations

from types import SimpleNamespace

from src.interface.player_ai_chat_interface import PlayerAiChatInterface


class _ServiceStub:
    def __init__(self) -> None:
        self.is_running = False
        self.start_count = 0
        self.stop_count = 0

    def start(self) -> bool:
        self.start_count += 1
        self.is_running = True
        return True

    def stop(self) -> None:
        self.stop_count += 1
        self.is_running = False


class _PolicyStub:
    def __init__(self) -> None:
        self.settings = {
            "enabled": True,
            "audience": "all",
            "list_mode": "blocklist",
            "access_entries": [],
        }

    def get_settings(self):
        return SimpleNamespace(to_dict=lambda: dict(self.settings))

    def save_settings(self, payload):
        self.settings = dict(payload)
        return SimpleNamespace(
            enabled=bool(payload["enabled"]),
            to_dict=lambda: dict(payload),
        )

    def list_known_players(self):
        return [{"name": "Steve"}]


def test_player_ai_interface_applies_enabled_state_immediately() -> None:
    service = _ServiceStub()
    policy = _PolicyStub()
    interface = PlayerAiChatInterface(service, policy)

    interface.save_settings({
        "enabled": False,
        "audience": "all",
        "list_mode": "blocklist",
        "access_entries": [],
    })
    interface.save_settings({
        "enabled": True,
        "audience": "operators",
        "list_mode": "allowlist",
        "access_entries": [{"display_name": "Admin"}],
    })

    assert service.stop_count == 1
    assert service.start_count == 1
    assert interface.get_settings()["audience"] == "operators"
    assert interface.list_known_players() == [{"name": "Steve"}]
