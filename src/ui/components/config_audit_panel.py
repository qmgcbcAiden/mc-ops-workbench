from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import flet as ft

from src.ui import theme


class ConfigAuditPanel:
    def __init__(
        self,
        on_view_diff: Callable[[str], None] | None = None,
        on_confirm: Callable[[str], None] | None = None,
        on_reject: Callable[[str], None] | None = None,
        on_rollback: Callable[[str], None] | None = None,
    ) -> None:
        self._on_view_diff = on_view_diff
        self._on_confirm = on_confirm
        self._on_reject = on_reject
        self._on_rollback = on_rollback
        self._records: list[dict[str, Any]] = []
        self._list = ft.ListView(expand=True, spacing=4, padding=8)

    def refresh(self, records: list[dict[str, Any]]) -> None:
        self._records = records
        self._list.controls.clear()
        for record in records:
            self._list.controls.append(self._build_record_card(record))
        self._try_update(self._list)

    def _build_record_card(self, record: dict[str, Any]) -> ft.Card:
        status = record.get("status", "")
        risk = record.get("risk_level", "LOW")
        audit_kind = record.get("audit_kind", "config_proposal")
        is_pending = status == "pending"
        is_high = risk == "HIGH"

        header_text = self._format_header(record)

        header = ft.Row(
            [
                ft.Icon(
                    ft.Icons.SAVE
                    if audit_kind == "file_edit"
                    else ft.Icons.WARNING_AMBER
                    if is_high
                    else ft.Icons.CHECK_CIRCLE,
                    color=(
                        ft.Colors.GREEN
                        if audit_kind == "file_edit" and status == "saved"
                        else ft.Colors.RED
                        if audit_kind == "file_edit"
                        else ft.Colors.ORANGE
                        if is_high
                        else ft.Colors.GREEN
                    ),
                    size=16,
                ),
                ft.Text(header_text, size=12, expand=True),
            ],
            spacing=8,
        )

        details = []
        changes = record.get("changes", [])
        for c in changes:
            key = c.get("key", "?")
            old = c.get("old_value", "?")
            new = c.get("new_value", "?")
            details.append(
                ft.Text(
                    f"{key}: {old} -> {new}",
                    size=11,
                    font_family="monospace",
                    color=theme.MUTED,
                )
            )

        if audit_kind == "file_edit":
            details.append(
                ft.Text(
                    f"{record.get('size_before', 0)} bytes -> {record.get('size_after', 0)} bytes",
                    size=11,
                    font_family="monospace",
                    color=theme.MUTED,
                )
            )
            if record.get("backup_path"):
                details.append(
                    ft.Text(
                        f"备份：{record['backup_path']}",
                        size=11,
                        color=theme.MUTED,
                    )
                )
            if record.get("error_message"):
                details.append(
                    ft.Text(
                        str(record["error_message"]),
                        size=11,
                        color=theme.RED,
                    )
                )

        if record.get("restart_required"):
            details.append(
                ft.Text(
                    "通常需要重启后生效",
                    size=11,
                    color=ft.Colors.ORANGE_600,
                )
            )

        warnings = record.get("warnings", [])
        for w in warnings:
            details.append(
                ft.Text(w, size=11, color=ft.Colors.ORANGE_500)
            )

        actions = []
        if record.get("diff") or record.get("diff_text"):
            actions.append(
                ft.TextButton(
                    "查看 diff",
                    icon=ft.Icons.DIFFERENCE,
                    on_click=lambda e, rid=_record_id(record): self._view_diff(rid),
                                    )
            )

        if is_pending:
            actions.append(
                ft.FilledButton(
                    "确认修改" if is_high else "应用修改",
                    icon=ft.Icons.CHECK,
                    on_click=lambda e, rid=_record_id(record): self._confirm(rid),
                                    )
            )
            actions.append(
                ft.TextButton(
                    "拒绝",
                    icon=ft.Icons.CLOSE,
                    on_click=lambda e, rid=_record_id(record): self._reject(rid),
                                    )
            )

        if status == "applied" and audit_kind != "file_edit":
            actions.append(
                ft.TextButton(
                    "回滚",
                    icon=ft.Icons.UNDO,
                    on_click=lambda e, rid=_record_id(record): self._rollback(rid),
                                    )
            )

        version_status = record.get("version_status", "")
        if version_status == "committed":
            actions.append(
                ft.Text("Git", size=10, color=ft.Colors.GREEN_400)
            )
        elif version_status == "failed":
            actions.append(
                ft.Text("版本快照失败", size=10, color=ft.Colors.RED_400)
            )

        content = ft.Column(
            [header] + details + [ft.Row(actions, spacing=4)],
            spacing=2,
        )

        return ft.Card(
            content=ft.Container(
                content=content,
                padding=12,
            ),
            bgcolor=theme.AMBER_SOFT if is_pending and is_high else None,
        )

    def _format_header(self, record: dict[str, Any]) -> str:
        created = record.get("created_at", "")
        short_time = ""
        if created:
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                short_time = dt.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                pass

        risk = record.get("risk_level", "LOW")
        status = record.get("status", "")
        audit_kind = record.get("audit_kind", "config_proposal")

        if audit_kind == "file_edit":
            status_text = "已保存" if status == "saved" else "失败"
            return f"{short_time}  用户 · 文件编辑 · {status_text}"

        if status == "applied":
            approved = "自动同意" if record.get("auto_approved") else "已确认"
            return f"{short_time}  Agent · {approved} · {risk} · 已应用"
        elif status == "pending":
            return f"{short_time}  等待确认 · {risk}"
        elif status == "rolled_back":
            return f"{short_time}  已回滚 · {risk}"
        elif status == "failed":
            return f"{short_time}  失败 · {risk}"
        return f"{short_time}  {risk} · {status}"

    def _view_diff(self, proposal_id: str) -> None:
        if self._on_view_diff:
            self._on_view_diff(proposal_id)

    def _confirm(self, proposal_id: str) -> None:
        if self._on_confirm:
            self._on_confirm(proposal_id)

    def _reject(self, proposal_id: str) -> None:
        if self._on_reject:
            self._on_reject(proposal_id)

    def _rollback(self, proposal_id: str) -> None:
        if self._on_rollback:
            self._on_rollback(proposal_id)

    @staticmethod
    def _try_update(control: ft.Control) -> None:
        try:
            if control.page:
                control.update()
        except RuntimeError:
            pass

    def build(self) -> ft.Control:
        return ft.Container(
            content=self._list,
            expand=True,
        )


def _record_id(record: dict[str, Any]) -> str:
    return str(record.get("proposal_id") or record.get("id") or "")
