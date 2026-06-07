from __future__ import annotations

import threading
from collections.abc import Callable

import flet as ft

from src.interface.server_interface import ServerInterface
from src.ui import theme
from src.ui.components.common import status_pill


class ServerControls:
    def __init__(self, server_interface: ServerInterface) -> None:
        self._interface = server_interface
        self._on_status_change: Callable[[dict | None], None] | None = None
        self._on_start_requested: Callable[[], None] | None = None
        self._on_settings_requested: Callable[[], None] | None = None

        self._status_pill = status_pill("未运行", color=theme.MUTED, bgcolor=theme.PANEL_SOFT)
        self._settings_btn = ft.IconButton(
            icon=ft.Icons.SETTINGS,
            icon_color=theme.MUTED,
            tooltip="基础设置",
            on_click=self._on_settings,
        )
        self._toggle_btn = ft.FilledButton(
            content="启动",
            icon=ft.Icons.PLAY_ARROW,
            style=_button_style(theme.GREEN, "#ffffff", "#2c7a56"),
            on_click=self._on_toggle,
        )
        self._control: ft.Row | None = None
        self._action_running = False
        self._action_lock = threading.Lock()

    def set_on_status_change(self, callback: Callable[[dict | None], None]) -> None:
        self._on_status_change = callback

    def set_on_start_requested(self, callback: Callable[[], None]) -> None:
        self._on_start_requested = callback

    def set_on_settings_requested(self, callback: Callable[[], None]) -> None:
        self._on_settings_requested = callback

    def build(self) -> ft.Row:
        self.refresh()
        self._control = ft.Row(
            controls=[self._status_pill, self._settings_btn, self._toggle_btn],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return self._control

    @property
    def control(self) -> ft.Row | None:
        return self._control

    def refresh(self) -> None:
        self._update_ui(self._interface.get_server_status())

    def get_status(self) -> dict:
        return self._interface.get_server_status()

    def _update_ui(self, status: dict) -> None:
        state = status.get("state", "stopped")
        labels = {
            "stopped": "未运行",
            "starting": "启动中",
            "running": "运行中",
            "stopping": "停止中",
            "crashed": "异常",
        }
        color_map = {
            "stopped": theme.MUTED,
            "starting": theme.BLUE,
            "running": theme.GREEN,
            "stopping": theme.AMBER,
            "crashed": theme.RED,
        }
        bg_map = {
            "stopped": theme.PANEL_SOFT,
            "starting": theme.BLUE_SOFT,
            "running": theme.GREEN_SOFT,
            "stopping": theme.AMBER_SOFT,
            "crashed": theme.RED_SOFT,
        }

        self._status_pill.content.value = status.get("label") or labels.get(state, state)
        self._status_pill.content.color = color_map.get(state, theme.MUTED)
        self._status_pill.bgcolor = bg_map.get(state, theme.PANEL_SOFT)

        self._toggle_btn.disabled = state in {"starting", "stopping"}
        if state == "running":
            self._toggle_btn.content = "停止"
            self._toggle_btn.icon = ft.Icons.STOP
            self._toggle_btn.style = _button_style(theme.RED_SOFT, theme.RED, "#71312e")
        elif state == "starting":
            self._toggle_btn.content = "启动中"
            self._toggle_btn.icon = ft.Icons.HOURGLASS_EMPTY
            self._toggle_btn.style = _button_style(theme.BLUE_SOFT, theme.BLUE, "#254d83")
        elif state == "stopping":
            self._toggle_btn.content = "停止中"
            self._toggle_btn.icon = ft.Icons.HOURGLASS_EMPTY
            self._toggle_btn.style = _button_style(theme.AMBER_SOFT, theme.AMBER, "#6b4a12")
        else:
            self._toggle_btn.content = "启动"
            self._toggle_btn.icon = ft.Icons.PLAY_ARROW
            self._toggle_btn.style = _button_style(theme.GREEN, "#ffffff", "#2c7a56")

    def _on_settings(self, _event) -> None:
        if self._on_settings_requested is not None:
            self._on_settings_requested()

    def _on_toggle(self, _event) -> None:
        if self._action_running:
            return
        state = self.get_status().get("state", "stopped")
        if state == "running":
            self._update_ui({"state": "stopping"})
            self._update_control()
            self._notify({"state": "stopping"})
            self._run_action(self._interface.stop_server)
        else:
            self._update_ui({"state": "starting"})
            self._update_control()
            self._notify_start_requested()
            self._run_action(self._interface.start_server)

    def _run_action(self, action: Callable[[], dict]) -> None:
        with self._action_lock:
            if self._action_running:
                return
            self._action_running = True

        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                result = {
                    "state": "stopped",
                    "status": "failed",
                    "message": str(exc),
                }
            finally:
                with self._action_lock:
                    self._action_running = False

            self._update_ui(result)
            self._update_control()
            self._notify(result)

        threading.Thread(target=worker, daemon=True).start()

    def _notify(self, result: dict | None) -> None:
        if self._on_status_change is not None:
            self._on_status_change(result)

    def _notify_start_requested(self) -> None:
        if self._on_start_requested is not None:
            self._on_start_requested()

    def _update_control(self) -> None:
        control = self._control
        if control is None:
            return
        try:
            control.update()
        except RuntimeError:
            pass
        except Exception:
            pass


def _button_style(bgcolor: str, color: str, border_color: str) -> ft.ButtonStyle:
    return ft.ButtonStyle(
        bgcolor=bgcolor,
        color=color,
        shape=ft.RoundedRectangleBorder(radius=7),
        side=ft.BorderSide(1, border_color),
        padding=ft.Padding.symmetric(horizontal=10, vertical=7),
    )
