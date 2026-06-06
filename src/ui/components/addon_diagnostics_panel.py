from __future__ import annotations

from typing import Any

import flet as ft

from src.interface.addon_diagnostic_interface import AddonDiagnosticInterface
from src.ui import theme
from src.ui.components.common import status_pill, tag


class AddonDiagnosticsPanel:
    def __init__(
        self,
        addon_interface: AddonDiagnosticInterface,
        page: ft.Page,
    ) -> None:
        self._interface = addon_interface
        self._page = page
        self._report: dict[str, Any] = self._safe_report()
        self._feedback = ft.Text("", size=11, color=theme.MUTED, max_lines=2)
        self._summary_row = ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self._diagnostic_list = ft.ListView(expand=True, spacing=6, padding=0)
        self._asset_count = ft.Text("", size=12, color=theme.MUTED)
        self._scan_button = ft.IconButton(
            icon=ft.Icons.PLAYLIST_ADD_CHECK,
            icon_color=theme.TEXT,
            icon_size=18,
            width=32,
            height=32,
            tooltip="本地扫描",
            on_click=lambda _: self.scan(refresh_online=False),
        )
        self._refresh_button = ft.IconButton(
            icon=ft.Icons.CLOUD_SYNC,
            icon_color=theme.TEXT,
            icon_size=18,
            width=32,
            height=32,
            tooltip="刷新在线元数据",
            on_click=lambda _: self.scan(refresh_online=True),
        )
        self._proposal_button = ft.IconButton(
            icon=ft.Icons.RATE_REVIEW,
            icon_color=theme.TEXT,
            icon_size=18,
            width=32,
            height=32,
            tooltip="生成修复草案",
            on_click=lambda _: self.propose_remediation(),
        )
        self._validation_button = ft.IconButton(
            icon=ft.Icons.ROCKET_LAUNCH,
            icon_color=theme.TEXT,
            icon_size=18,
            width=32,
            height=32,
            tooltip="启动验证入口",
            on_click=lambda _: self.prepare_runtime_validation(),
        )
        self._control = self._build_control()
        self._render()

    def build(self) -> ft.Control:
        self.refresh()
        return self._control

    def refresh(self) -> None:
        self._report = self._safe_report()
        self._render()

    def scan(self, refresh_online: bool = False) -> None:
        self._set_busy(True)
        try:
            self._report = self._interface.scan_addons(refresh_online=refresh_online)
            self._feedback.value = (
                "已刷新在线元数据。"
                if refresh_online
                else "已完成本地组件扫描。"
            )
        except Exception as exc:
            self._feedback.value = f"扫描失败：{exc}"
        finally:
            self._set_busy(False)
            self._render()
            self._safe_update()

    def propose_remediation(self) -> None:
        diagnostics = self._report.get("diagnostics") or []
        selected = [
            diagnostic["id"]
            for diagnostic in diagnostics
            if diagnostic.get("severity") in {"BLOCKER", "HIGH"}
        ] or [diagnostic["id"] for diagnostic in diagnostics if diagnostic.get("id")]
        if not selected:
            self._feedback.value = "没有可生成草案的诊断项。"
            self._safe_update()
            return
        try:
            result = self._interface.create_addon_remediation_plan(selected)
        except Exception as exc:
            self._feedback.value = f"草案生成失败：{exc}"
            self._safe_update()
            return
        self._feedback.value = result.get("message") or f"草案状态：{result.get('status')}"
        self._safe_update()

    def prepare_runtime_validation(self) -> None:
        try:
            result = self._interface.create_runtime_validation_plan()
        except Exception as exc:
            self._feedback.value = f"运行时验证入口创建失败：{exc}"
            self._safe_update()
            return
        self._feedback.value = result.get("message") or "运行时验证需要用户确认启动或重启服务器。"
        self._safe_update()

    def _build_control(self) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Icon(ft.Icons.EXTENSION, size=16, color=theme.MUTED),
                                        ft.Text(
                                            "组件诊断",
                                            size=14,
                                            weight=ft.FontWeight.W_700,
                                            color=theme.TEXT,
                                        ),
                                        self._asset_count,
                                    ],
                                    spacing=7,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    expand=True,
                                ),
                                self._scan_button,
                                self._refresh_button,
                                self._validation_button,
                                self._proposal_button,
                            ],
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                        border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
                    ),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                self._summary_row,
                                self._feedback,
                            ],
                            spacing=6,
                        ),
                        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                        bgcolor=theme.TAB_BG,
                        border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
                    ),
                    ft.Container(content=self._diagnostic_list, padding=ft.Padding.all(10), expand=True),
                    ft.Container(
                        content=ft.Text(
                            "启动/重启验证必须由用户确认；本页不会自动移动、删除或禁用 jar。",
                            size=11,
                            color=theme.MUTED,
                            max_lines=2,
                        ),
                        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                        bgcolor=theme.TAB_BG,
                        border=ft.Border.only(top=ft.BorderSide(1, theme.LINE)),
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            expand=True,
        )

    def _render(self) -> None:
        report = self._report or {}
        assets = report.get("assets") or []
        diagnostics = report.get("diagnostics") or []
        summary = report.get("summary") or {}
        self._asset_count.value = f"{len(assets)} 个组件"
        self._summary_row.controls = [
            status_pill(f"BLOCKER {summary.get('blockers', 0)}", color=theme.RED, bgcolor=theme.RED_SOFT),
            status_pill(f"HIGH {summary.get('high', 0)}", color=theme.RED, bgcolor=theme.RED_SOFT),
            status_pill(f"MEDIUM {summary.get('medium', 0)}", color=theme.AMBER, bgcolor=theme.AMBER_SOFT),
            status_pill(f"LOW {summary.get('low', 0)}", color=theme.GREEN, bgcolor=theme.GREEN_SOFT),
            tag("证据优先"),
        ]
        if not self._feedback.value:
            self._feedback.value = report.get("message") or "尚未执行组件诊断扫描。"
        self._diagnostic_list.controls = (
            [_diagnostic_card(item) for item in diagnostics]
            if diagnostics
            else [_empty_card(report.get("message") or "点击本地扫描开始检查 mods/ 与 plugins/。")]
        )
        self._proposal_button.disabled = not diagnostics

    def _safe_report(self) -> dict[str, Any]:
        try:
            return self._interface.get_latest_addon_report()
        except Exception as exc:
            return {
                "status": "failed",
                "message": f"组件诊断报告读取失败：{exc}",
                "assets": [],
                "diagnostics": [],
                "summary": {},
            }

    def _set_busy(self, busy: bool) -> None:
        for button in (
            self._scan_button,
            self._refresh_button,
            self._validation_button,
            self._proposal_button,
        ):
            button.disabled = busy

    def _safe_update(self) -> None:
        try:
            self._control.update()
        except Exception:
            try:
                self._page.update()
            except Exception:
                pass


def _diagnostic_card(diagnostic: dict[str, Any]) -> ft.Control:
    severity = str(diagnostic.get("severity") or "INFO")
    color, bgcolor = _severity_color(severity)
    affected = ", ".join(diagnostic.get("affected_files") or [])
    evidence = diagnostic.get("evidence_type") or "unknown"
    confidence = diagnostic.get("confidence") or "unknown"
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        status_pill(severity, color=color, bgcolor=bgcolor),
                        tag(str(diagnostic.get("category") or "diagnostic")),
                        ft.Container(expand=True),
                        tag(str(evidence)),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(
                    str(diagnostic.get("message") or ""),
                    size=13,
                    weight=ft.FontWeight.W_600,
                    color=theme.TEXT,
                ),
                ft.Text(
                    f"证据：{evidence} · 置信度：{confidence}"
                    + (f" · 文件：{affected}" if affected else ""),
                    size=11,
                    color=theme.MUTED,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                _actions_text(diagnostic.get("suggested_actions") or []),
            ],
            spacing=5,
        ),
        bgcolor=theme.PANEL_RAISED,
        border=ft.Border.all(1, theme.LINE),
        border_radius=7,
        padding=ft.Padding.all(9),
    )


def _actions_text(actions: list[dict[str, Any]]) -> ft.Control:
    labels = [
        str(action.get("label") or action.get("type") or "")
        for action in actions
        if isinstance(action, dict)
    ]
    text = "建议：" + "；".join(label for label in labels if label)
    return ft.Text(
        text if labels else "建议：先查看证据来源，再决定是否隔离或替换组件。",
        size=11,
        color=theme.MUTED,
        max_lines=2,
        overflow=ft.TextOverflow.ELLIPSIS,
    )


def _empty_card(message: str) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Icon(ft.Icons.INFO_OUTLINE, color=theme.MUTED, size=22),
                ft.Text(message, size=13, color=theme.MUTED, text_align=ft.TextAlign.CENTER),
            ],
            spacing=8,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        alignment=ft.Alignment(0, 0),
        expand=True,
        bgcolor=theme.PANEL_SOFT,
        border=ft.Border.all(1, theme.LINE),
        border_radius=7,
        padding=ft.Padding.all(16),
    )


def _severity_color(severity: str) -> tuple[str, str]:
    if severity in {"BLOCKER", "HIGH"}:
        return theme.RED, theme.RED_SOFT
    if severity == "MEDIUM":
        return theme.AMBER, theme.AMBER_SOFT
    if severity == "LOW":
        return theme.GREEN, theme.GREEN_SOFT
    return theme.MUTED, theme.PANEL_SOFT
