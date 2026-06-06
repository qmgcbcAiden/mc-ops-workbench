from __future__ import annotations

from collections.abc import Callable

import flet as ft

from src.interface.command_interface import CommandInterface
from src.ui import theme
from src.ui.components.common import status_pill, text_field
from src.ui.theme import risk_color


class CommandConsole:
    def __init__(
        self,
        command_interface: CommandInterface,
        on_change: Callable[[], None] | None = None,
        on_result: Callable[[dict], None] | None = None,
    ) -> None:
        self._interface = command_interface
        self._on_change = on_change
        self._on_result = on_result
        self._audit_list = ft.ListView(expand=True, spacing=6, padding=0)
        self._input = text_field(
            value="list",
            expand=True,
            on_submit=self._on_execute,
        )
        self._input.border = ft.InputBorder.NONE
        self._input.border_radius = 0
        self._input.border_color = "#00000000"
        self._input.focused_border_color = "#00000000"
        self._input.bgcolor = "#00000000"
        self._input.focused_bgcolor = "#00000000"
        self._input.fill_color = "#00000000"
        self._input.focus_color = "#00000000"
        self._input.hover_color = "#00000000"
        self._input.text_vertical_align = 0
        self._input.content_padding = ft.Padding.only(left=12, right=10, top=0, bottom=0)
        self._history_button = ft.IconButton(
            icon=ft.Icons.HISTORY,
            icon_color=theme.MUTED,
            selected_icon_color=theme.BLUE,
            icon_size=17,
            width=30,
            height=30,
            tooltip="命令历史",
            on_click=lambda _: self.toggle_history(),
        )
        self._history_panel = ft.Container(
            content=ft.Column(
                controls=[
                    self._audit_list,
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.HISTORY, size=15, color=theme.MUTED),
                                ft.Text("命令历史", size=12, weight=ft.FontWeight.W_700, color=theme.TEXT),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.CLOSE,
                                    icon_size=15,
                                    icon_color=theme.MUTED,
                                    tooltip="关闭",
                                    on_click=lambda _: self.close_history(),
                                ),
                            ],
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        border=ft.Border.only(top=ft.BorderSide(1, theme.LINE)),
                        padding=ft.Padding.only(left=3, top=4),
                    ),
                ],
                expand=True,
                spacing=4,
            ),
            visible=False,
            width=390,
            height=270,
            bgcolor=theme.PANEL_RAISED,
            border=ft.Border.all(1, theme.LINE_STRONG),
            border_radius=7,
            padding=ft.Padding.all(8),
            shadow=ft.BoxShadow(
                blur_radius=16,
                color="#00000066",
                offset=ft.Offset(0, 7),
            ),
        )

    @property
    def command_input(self) -> ft.TextField:
        return self._input

    @property
    def history_button(self) -> ft.IconButton:
        return self._history_button

    @property
    def history_panel(self) -> ft.Container:
        return self._history_panel

    def get_command(self) -> str:
        return (self._input.value or "").strip()

    def execute(self) -> dict:
        command = self.get_command()
        result = self._interface.submit_command(
            command=command,
            requested_by="ui",
            user_confirmed=True,
        )
        self.refresh_audits()
        if self._on_result is not None:
            self._on_result(result)
        if self._on_change is not None:
            self._on_change()
        return result

    def refresh_audits(self) -> None:
        audits = self._interface.list_command_audits(limit=20)
        self._audit_list.controls = [_audit_row(audit) for audit in audits] if audits else [_empty_audit_state()]
        self._try_update(self._audit_list)

    def toggle_history(self) -> None:
        if self._history_panel.visible:
            self.close_history()
            return
        self.refresh_audits()
        self._history_panel.visible = True
        self._history_button.selected = True
        self._try_update(self._history_panel)
        self._try_update(self._history_button)
        self._notify_change()

    def close_history(self) -> None:
        self._history_panel.visible = False
        self._history_button.selected = False
        self._try_update(self._history_panel)
        self._try_update(self._history_button)
        self._notify_change()

    def _on_execute(self, _event) -> None:
        self.execute()

    def _notify_change(self) -> None:
        if self._on_change is not None:
            self._on_change()

    @staticmethod
    def _try_update(control: ft.Control) -> None:
        try:
            if control.page:
                control.update()
        except RuntimeError:
            pass


def _audit_row(item: dict) -> ft.Control:
    color, bgcolor = risk_color(item.get("risk_level", "UNREVIEWED"))
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text(
                            item.get("command", ""),
                            size=12,
                            color=theme.TEXT,
                            expand=True,
                            no_wrap=False,
                        ),
                        status_pill(item.get("status", ""), color=color, bgcolor=bgcolor),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    controls=[
                        ft.Text(_short_time(item.get("created_at")), size=10, color=theme.MUTED),
                        ft.Text(item.get("requested_by", "ui"), size=10, color=theme.MUTED),
                        ft.Text(item.get("risk_level", ""), size=10, color=theme.MUTED),
                    ],
                    spacing=8,
                ),
            ],
            spacing=4,
        ),
        bgcolor=theme.PANEL_SOFT,
        border=ft.Border.all(1, theme.LINE),
        border_radius=7,
        padding=ft.Padding.symmetric(vertical=7, horizontal=9),
    )


def _empty_audit_state() -> ft.Control:
    return ft.Container(
        content=ft.Text("暂无命令历史", size=12, color=theme.MUTED),
        alignment=ft.Alignment(0, 0),
        padding=ft.Padding.symmetric(vertical=16),
    )


def _short_time(value: str | None) -> str:
    if not value:
        return "--:--:--"
    if "T" in value:
        return value.split("T", 1)[1][:8]
    return value[:8]
