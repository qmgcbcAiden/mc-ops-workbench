from __future__ import annotations

import flet as ft


class DiffViewer:
    def __init__(self, diff_text: str = "") -> None:
        self.diff_text = diff_text
        self._lines_column = ft.Column(spacing=0, expand=True, scroll=ft.ScrollMode.ALWAYS)

    def update_diff(self, diff_text: str) -> None:
        self.diff_text = diff_text
        self._lines_column.controls.clear()
        for line in diff_text.split("\n"):
            self._lines_column.controls.append(self._render_line(line))
        self._try_update(self._lines_column)

    def _render_line(self, line: str) -> ft.Container:
        if line.startswith("+++") or line.startswith("---"):
            color = ft.Colors.BLUE_300
            bg = "#13243d"
        elif line.startswith("@@"):
            color = ft.Colors.CYAN_300
            bg = "#102c34"
        elif line.startswith("+"):
            color = ft.Colors.GREEN_300
            bg = "#10271f"
        elif line.startswith("-"):
            color = ft.Colors.RED_300
            bg = "#351817"
        else:
            color = ft.Colors.GREY_500
            bg = None

        return ft.Container(
            content=ft.Text(
                line or " ",
                size=13,
                font_family="monospace",
                color=color,
                no_wrap=True,
            ),
            bgcolor=bg,
            padding=ft.Padding.only(left=8, right=8, top=1, bottom=1),
        )

    def build(self) -> ft.Control:
        self._lines_column.controls.clear()
        if self.diff_text:
            for line in self.diff_text.split("\n"):
                self._lines_column.controls.append(self._render_line(line))
        else:
            self._lines_column.controls.append(
                ft.Container(
                    content=ft.Text("从审计或版本记录中选择一项以查看 Diff", size=12, color=ft.Colors.GREY_500),
                    padding=ft.Padding.all(8),
                )
            )
        return ft.Container(
            content=ft.Column(
                [self._lines_column],
                expand=True,
                scroll=ft.ScrollMode.ALWAYS,
            ),
            expand=True,
        )

    @staticmethod
    def _try_update(control: ft.Control) -> None:
        try:
            if control.page:
                control.update()
        except RuntimeError:
            pass
