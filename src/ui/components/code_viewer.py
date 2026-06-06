from __future__ import annotations

import difflib
from dataclasses import dataclass

import flet as ft
from typing import Callable

from src.ui import theme
from src.ui.components.syntax_highlighter import tokenize_line


_TOKEN_COLORS = {
    "plain": theme.TEXT,
    "comment": theme.SOFT_TEXT,
    "heading": theme.BLUE,
    "key": "#79c0ff",
    "keyword": theme.VIOLET,
    "number": theme.AMBER,
    "operator": theme.MUTED,
    "section": theme.BLUE,
    "string": theme.GREEN,
}


@dataclass(frozen=True)
class InlineCodeLine:
    text: str
    old_number: int | None
    new_number: int | None
    marker: str = " "


class CodeViewer:
    def __init__(
        self,
        content: str = "",
        language: str = "properties",
        read_only: bool = True,
        on_change: Callable[[str], None] | None = None,
        original_content: str | None = None,
    ) -> None:
        self.content = content
        self.language = language
        self.read_only = read_only
        self._on_change = on_change
        self.original_content = original_content
        self.inline_lines: list[InlineCodeLine] = []
        self.first_change_scroll_key: str | None = None
        self._lines_column = ft.Column(spacing=0)
        self._scrollable = ft.Column(
            [self._lines_column],
            scroll=ft.ScrollMode.ALWAYS,
            spacing=0,
            expand=True,
        )

    def _build_lines(self, text: str) -> None:
        self._lines_column.controls.clear()
        self.inline_lines = _inline_lines(self.original_content, text)
        self.first_change_scroll_key = None
        in_review = self.original_content is not None
        for index, line in enumerate(self.inline_lines):
            changed = line.marker in {"+", "-"}
            scroll_key = f"inline-change-{index}" if changed and self.first_change_scroll_key is None else None
            if scroll_key:
                self.first_change_scroll_key = scroll_key
            self._lines_column.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            self._review_gutter(line)
                            if in_review
                            else self._line_number_gutter(line.new_number or 1),
                            ft.Text(
                                " " if not line.text else None,
                                spans=_line_spans(line.text, self.language) if line.text else None,
                                size=13,
                                font_family="monospace",
                                color=(
                                    theme.GREEN
                                    if line.marker == "+"
                                    else theme.RED
                                    if line.marker == "-"
                                    else theme.TEXT
                                ),
                                selectable=True,
                                no_wrap=True,
                            ),
                        ],
                        spacing=0,
                    ),
                    bgcolor=(
                        theme.GREEN_SOFT
                        if line.marker == "+"
                        else theme.RED_SOFT
                        if line.marker == "-"
                        else None
                    ),
                    key=ft.ScrollKey(scroll_key) if scroll_key else None,
                )
            )

    def _line_number_gutter(self, line_number: int) -> ft.Container:
        return ft.Container(
            content=ft.Text(
                str(line_number),
                size=13,
                font_family="monospace",
                color=theme.MUTED,
                text_align=ft.TextAlign.RIGHT,
                no_wrap=True,
            ),
            bgcolor=theme.PANEL,
            width=56,
            padding=ft.Padding.only(top=1, right=8, bottom=1, left=8),
            border=ft.Border.only(right=ft.BorderSide(1, theme.LINE)),
        )

    def _review_gutter(self, line: InlineCodeLine) -> ft.Container:
        marker_color = (
            theme.GREEN
            if line.marker == "+"
            else theme.RED
            if line.marker == "-"
            else theme.MUTED
        )
        return ft.Container(
            content=ft.Row(
                [
                    _gutter_text(line.old_number),
                    _gutter_text(line.new_number),
                    ft.Text(
                        line.marker,
                        size=13,
                        font_family="monospace",
                        color=marker_color,
                        weight=ft.FontWeight.W_700 if line.marker != " " else None,
                    ),
                ],
                spacing=6,
            ),
            bgcolor=(
                theme.GREEN_SOFT
                if line.marker == "+"
                else theme.RED_SOFT
                if line.marker == "-"
                else theme.PANEL
            ),
            width=82,
            padding=ft.Padding.only(top=1, right=6, bottom=1, left=6),
            border=ft.Border.only(right=ft.BorderSide(1, theme.LINE)),
        )

    async def scroll_to_first_change(self) -> None:
        if self.first_change_scroll_key:
            await self._scrollable.scroll_to(
                scroll_key=self.first_change_scroll_key,
                duration=160,
            )

    def update_content(self, content: str) -> None:
        self.content = content
        self._build_lines(content)
        self._try_update(self._scrollable)

    def build(self) -> ft.Control:
        if self.content:
            self._build_lines(self.content)
        return ft.Container(
            content=self._scrollable,
            bgcolor=theme.PANEL,
            expand=True,
        )

    @staticmethod
    def _try_update(control: ft.Control) -> None:
        try:
            if control.page:
                control.update()
        except RuntimeError:
            pass


def _line_spans(line: str, language: str) -> list[ft.TextSpan]:
    return [
        ft.TextSpan(
            token.text,
            style=ft.TextStyle(
                color=_TOKEN_COLORS.get(token.role, theme.TEXT),
                weight=ft.FontWeight.W_600 if token.role in {"heading", "key", "section"} else None,
            ),
        )
        for token in tokenize_line(line, language)
    ]


def _gutter_text(value: int | None) -> ft.Text:
    return ft.Text(
        str(value) if value is not None else "",
        size=11,
        width=21,
        font_family="monospace",
        color=theme.MUTED,
        text_align=ft.TextAlign.RIGHT,
        no_wrap=True,
    )


def _inline_lines(original_content: str | None, content: str) -> list[InlineCodeLine]:
    new_lines = content.split("\n")
    if original_content is None:
        return [
            InlineCodeLine(text=line, old_number=None, new_number=number)
            for number, line in enumerate(new_lines, 1)
        ]

    old_lines = original_content.split("\n")
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    rows: list[InlineCodeLine] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            for offset, text in enumerate(old_lines[old_start:old_end]):
                rows.append(InlineCodeLine(text, old_start + offset + 1, new_start + offset + 1))
        if tag in {"replace", "delete"}:
            for offset, text in enumerate(old_lines[old_start:old_end]):
                rows.append(InlineCodeLine(text, old_start + offset + 1, None, "-"))
        if tag in {"replace", "insert"}:
            for offset, text in enumerate(new_lines[new_start:new_end]):
                rows.append(InlineCodeLine(text, None, new_start + offset + 1, "+"))
    return rows
