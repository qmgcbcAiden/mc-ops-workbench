from __future__ import annotations

import flet as ft

from src.ui import theme


_SMOOTH_ANIMATION = ft.Animation(220, ft.AnimationCurve.EASE_OUT)


def panel(
    title: str,
    subtitle: str | ft.Control | None,
    body: ft.Control,
    action: ft.Control | None = None,
    footer: ft.Control | None = None,
    min_height: int | None = None,
    footer_padding: ft.Padding | None = None,
) -> ft.Container:
    if isinstance(subtitle, ft.Control):
        subtitle_control = subtitle
    else:
        subtitle_control = ft.Text(subtitle or "", size=11, color=theme.MUTED, max_lines=1)

    header_items: list[ft.Control] = [
        ft.Column(
            controls=[
                ft.Text(title, size=14, weight=ft.FontWeight.W_700, color=theme.TEXT),
                subtitle_control,
            ],
            spacing=3,
            expand=True,
        )
    ]
    if action is not None:
        header_items.append(action)

    content = [
        ft.Container(
            content=ft.Row(
                controls=header_items,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=11, vertical=9),
            border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
        ),
        ft.Container(content=body, padding=ft.Padding.all(11), expand=True),
    ]
    if footer is not None:
        content.append(
            ft.Container(
                content=footer,
                padding=footer_padding or ft.Padding.symmetric(horizontal=11, vertical=9),
                border=ft.Border.only(top=ft.BorderSide(1, theme.LINE)),
                bgcolor=theme.TAB_BG,
            )
        )

    return ft.Container(
        content=ft.Column(controls=content, spacing=0, expand=True),
        bgcolor=theme.PANEL,
        border=ft.Border.all(1, theme.LINE),
        border_radius=theme.RADIUS,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        expand=True,
        height=min_height,
    )


def tag(text: str, color: str = theme.MUTED, bgcolor: str = theme.PANEL_SOFT) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=color),
        bgcolor=bgcolor,
        border=ft.Border.all(1, theme.LINE),
        border_radius=999,
        padding=ft.Padding.symmetric(horizontal=7, vertical=3),
    )


def status_pill(text: str, color: str = theme.GREEN, bgcolor: str = theme.GREEN_SOFT) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=color),
        bgcolor=bgcolor,
        border_radius=999,
        padding=ft.Padding.symmetric(horizontal=7, vertical=3),
    )


def metric_card(
    title: str,
    value: str,
    unit: str,
    hint: ft.Control,
    progress: float,
    color: str,
    highlight_progress: float | None = None,
    highlight_label: str | None = None,
    highlight_color: str = theme.GREEN,
    highlight_tooltip: str | None = None,
) -> ft.Container:
    return MetricCardController(
        title=title,
        value=value,
        unit=unit,
        hint=hint,
        progress=progress,
        color=color,
        highlight_progress=highlight_progress,
        highlight_label=highlight_label,
        highlight_color=highlight_color,
        highlight_tooltip=highlight_tooltip,
    ).control


class MetricCardController:
    def __init__(
        self,
        title: str,
        value: str,
        unit: str,
        hint: ft.Control,
        progress: float,
        color: str,
        highlight_progress: float | None = None,
        highlight_label: str | None = None,
        highlight_color: str = theme.GREEN,
        highlight_tooltip: str | None = None,
    ) -> None:
        self.title = ft.Text(
            title,
            size=12,
            color=theme.MUTED,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.hint_slot = ft.Container(content=hint)
        self.value_text = ft.Text(
            value,
            size=23,
            weight=ft.FontWeight.W_700,
            color=theme.TEXT,
        )
        self.unit = ft.Text(unit, size=12, weight=ft.FontWeight.W_600, color=theme.MUTED)
        self.progress_bar = _progress_bar(progress, color, "#27313d")
        self.highlight_bar = _progress_bar(
            highlight_progress or 0,
            highlight_color,
            "#00000000",
        )
        self.highlight_dot = ft.Container(
            width=6,
            height=6,
            border_radius=2,
            bgcolor=highlight_color,
        )
        self.highlight_label_text = ft.Text(
            highlight_label or "",
            size=11,
            color=theme.MUTED,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.highlight_label_row = ft.Row(
            controls=[self.highlight_dot, self.highlight_label_text],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.progress_container = ft.Container(
            content=ft.Stack(
                controls=[self.progress_bar, self.highlight_bar],
                fit=ft.StackFit.EXPAND,
            ),
            height=6,
            tooltip=highlight_tooltip,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        self.progress_block = ft.Column(
            controls=[self.progress_container, self.highlight_label_row],
            spacing=6,
        )
        self.control = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self.title,
                            self.hint_slot,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        controls=[
                            self.value_text,
                            self.unit,
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    self.progress_block,
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            bgcolor=theme.PANEL,
            border=ft.Border.all(1, theme.LINE),
            border_radius=theme.RADIUS,
            padding=ft.Padding.all(11),
            height=104 if highlight_label else 92,
            animate=_SMOOTH_ANIMATION,
            animate_opacity=_SMOOTH_ANIMATION,
        )
        self.update(
            title=title,
            value=value,
            unit=unit,
            hint=hint,
            progress=progress,
            color=color,
            highlight_progress=highlight_progress,
            highlight_label=highlight_label,
            highlight_color=highlight_color,
            highlight_tooltip=highlight_tooltip,
        )

    def update(
        self,
        title: str,
        value: str,
        unit: str,
        hint: ft.Control,
        progress: float,
        color: str,
        highlight_progress: float | None = None,
        highlight_label: str | None = None,
        highlight_color: str = theme.GREEN,
        highlight_tooltip: str | None = None,
    ) -> None:
        self.title.value = title
        self.hint_slot.content = hint
        self.value_text.value = value
        self.unit.value = unit
        self.progress_bar.value = _clamp01(progress)
        self.progress_bar.color = color
        self.progress_container.tooltip = highlight_tooltip
        self.highlight_bar.visible = highlight_progress is not None
        self.highlight_bar.value = _clamp01(highlight_progress or 0)
        self.highlight_bar.color = highlight_color
        self.highlight_label_row.visible = bool(highlight_label)
        self.highlight_dot.bgcolor = highlight_color
        self.highlight_label_text.value = highlight_label or ""
        self.control.height = 104 if highlight_label else 92


def _smooth_progress_bar(value: float, color: str, bgcolor: str) -> ft.Control:
    return _progress_bar(value, color, bgcolor)


def _progress_bar(value: float, color: str, bgcolor: str) -> ft.Control:
    return ft.ProgressBar(
        value=_clamp01(value),
        color=color,
        bgcolor=bgcolor,
        bar_height=6,
        border_radius=999,
    )


def _clamp01(value: float) -> float:
    return max(0, min(float(value), 1))


def text_field(
    value: str = "",
    hint_text: str | None = None,
    expand: bool = False,
    on_change=None,
    on_submit=None,
) -> ft.TextField:
    return ft.TextField(
        value=value,
        hint_text=hint_text,
        dense=True,
        height=34,
        border_radius=7,
        border_color=theme.LINE,
        focused_border_color=theme.LINE_STRONG,
        bgcolor=theme.INPUT_BG,
        color=theme.TEXT,
        hint_style=ft.TextStyle(color="#667488"),
        cursor_color=theme.BLUE,
        expand=expand,
        on_change=on_change,
        on_submit=on_submit,
    )
