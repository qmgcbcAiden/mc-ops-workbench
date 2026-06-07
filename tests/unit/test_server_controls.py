from __future__ import annotations

import threading

import flet as ft

from src.ui.components.server_controls import ServerControls


class _ServerInterfaceStub:
    def __init__(self, state: str = "stopped") -> None:
        self.state = state
        self.events: list[str] = []
        self.start_called = threading.Event()
        self.stop_called = threading.Event()

    def get_server_status(self) -> dict:
        return {"state": self.state}

    def start_server(self) -> dict:
        self.events.append("start_server")
        self.start_called.set()
        self.state = "starting"
        return {"state": "starting"}

    def stop_server(self) -> dict:
        self.events.append("stop_server")
        self.stop_called.set()
        self.state = "stopped"
        return {"state": "stopped"}


class _ControlStub:
    def __init__(self) -> None:
        self.update_count = 0

    def update(self) -> None:
        self.update_count += 1


def test_start_button_notifies_before_start_server_call() -> None:
    server = _ServerInterfaceStub(state="stopped")
    controls = ServerControls(server)
    events = server.events
    status_changes: list[dict | None] = []
    status_changed = threading.Event()
    controls.set_on_start_requested(lambda: events.append("before_start"))

    def record_status_change(status: dict | None) -> None:
        status_changes.append(status)
        status_changed.set()

    controls.set_on_status_change(record_status_change)

    controls._on_toggle(None)

    assert server.start_called.wait(0.5)
    assert status_changed.wait(0.5)
    assert events == ["before_start", "start_server"]
    assert status_changes == [{"state": "starting"}]


def test_start_button_returns_before_blocking_start_server_finishes() -> None:
    class BlockingServer(_ServerInterfaceStub):
        def __init__(self) -> None:
            super().__init__(state="stopped")
            self.release_start = threading.Event()

        def start_server(self) -> dict:
            self.events.append("start_server")
            self.start_called.set()
            self.release_start.wait(1)
            self.state = "starting"
            return {"state": "starting"}

    server = BlockingServer()
    controls = ServerControls(server)
    events = server.events
    controls.set_on_start_requested(lambda: events.append("before_start"))

    controls._on_toggle(None)

    assert server.start_called.wait(0.5)
    assert events == ["before_start", "start_server"]
    server.release_start.set()


def test_start_button_flushes_starting_state_before_blocking_start_finishes() -> None:
    class BlockingServer(_ServerInterfaceStub):
        def __init__(self) -> None:
            super().__init__(state="stopped")
            self.release_start = threading.Event()

        def start_server(self) -> dict:
            self.events.append("start_server")
            self.start_called.set()
            self.release_start.wait(1)
            self.state = "starting"
            return {"state": "starting"}

    server = BlockingServer()
    controls = ServerControls(server)
    control = _ControlStub()
    controls._control = control

    controls._on_toggle(None)

    assert server.start_called.wait(0.5)
    assert control.update_count == 1
    assert controls._toggle_btn.disabled is True
    assert controls._toggle_btn.content == "启动中"
    server.release_start.set()


def test_stop_button_does_not_notify_start_requested() -> None:
    server = _ServerInterfaceStub(state="running")
    controls = ServerControls(server)
    events = server.events
    controls.set_on_start_requested(lambda: events.append("before_start"))

    controls._on_toggle(None)

    assert server.stop_called.wait(0.5)
    assert events == ["stop_server"]


def test_settings_button_replaces_manual_refresh_action() -> None:
    server = _ServerInterfaceStub(state="stopped")
    controls = ServerControls(server)
    requested: list[str] = []
    controls.set_on_settings_requested(lambda: requested.append("settings"))

    row = controls.build()
    controls._settings_btn.on_click(None)

    assert row.controls[1] is controls._settings_btn
    assert controls._settings_btn.icon == ft.Icons.SETTINGS
    assert not hasattr(controls, "_refresh_btn")
    assert requested == ["settings"]
