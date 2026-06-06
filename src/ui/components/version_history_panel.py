from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import flet as ft

from src.ui import theme


class VersionHistoryPanel:
    def __init__(
        self,
        on_select_commit: Callable[[str], None] | None = None,
    ) -> None:
        self._on_select_commit = on_select_commit
        self._commits: list[dict[str, Any]] = []
        self._list = ft.ListView(expand=True, spacing=2, padding=8)

    def refresh(self, commits: list[dict[str, Any]]) -> None:
        self._commits = commits
        self._list.controls.clear()
        if not commits:
            self._list.controls.append(
                ft.Text("暂无版本历史", size=12, color=theme.MUTED)
            )
        for commit in commits:
            self._list.controls.append(self._build_commit_row(commit))
        self._try_update(self._list)

    def _build_commit_row(self, commit: dict[str, Any]) -> ft.Container:
        created = commit.get("created_at", "")
        short_time = ""
        if created:
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                short_time = dt.strftime("%m-%d %H:%M")
            except (ValueError, TypeError):
                pass

        message = commit.get("message", "")
        commit_id_short = (commit.get("commit_id", ""))[:7]
        actor = commit.get("actor", "system")
        source = commit.get("source", "system")
        risk = commit.get("risk_level", "")

        actor_label = f"{source}/{actor}" if actor != "system" else source

        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                message,
                                size=12,
                                expand=True,
                                no_wrap=False,
                                max_lines=1,
                            ),
                            ft.Text(
                                f"({commit_id_short})",
                                size=10,
                                font_family="monospace",
                                color=theme.MUTED,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Row(
                        [
                            ft.Text(short_time, size=10, color=theme.MUTED),
                            ft.Text(actor_label, size=10, color=theme.MUTED),
                            ft.Text(risk, size=10, color=theme.MUTED) if risk else ft.Text(""),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=1,
            ),
            padding=ft.Padding.only(left=8, right=8, top=4, bottom=4),
            border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
            on_click=lambda e, cid=commit.get("commit_id", ""): self._select(cid),
        )

    def _select(self, commit_id: str) -> None:
        if self._on_select_commit:
            self._on_select_commit(commit_id)

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
