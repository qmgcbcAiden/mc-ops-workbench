from __future__ import annotations

from typing import Any, Callable

import flet as ft

from src.interface.config_edit_interface import ConfigEditInterface
from src.interface.config_version_interface import ConfigVersionInterface
from src.interface.file_interface import FileInterface
from src.ui import theme
from src.ui.components.code_viewer import CodeViewer
from src.ui.components.config_audit_panel import ConfigAuditPanel
from src.ui.components.diff_viewer import DiffViewer
from src.ui.components.file_editor import FileEditor
from src.ui.components.version_history_panel import VersionHistoryPanel


class CodeWorkbench:
    def __init__(
        self,
        file_interface: FileInterface,
        config_interface: ConfigEditInterface,
        version_interface: ConfigVersionInterface,
        page: ft.Page,
        on_file_changed: Callable[[], None] | None = None,
        on_config_apply: Callable[[str, bool], dict] | None = None,
        on_config_reject: Callable[[str], dict] | None = None,
        on_config_feedback: Callable[[dict, str], None] | None = None,
    ) -> None:
        self._file_interface = file_interface
        self._config_interface = config_interface
        self._version_interface = version_interface
        self._page = page
        self._on_file_changed = on_file_changed
        self._on_config_apply = on_config_apply
        self._on_config_reject = on_config_reject
        self._on_config_feedback = on_config_feedback

        self._current_path: str | None = None
        self._current_content: str = ""
        self._current_file_name: str = ""
        self._current_language: str = "text"
        self._editable = False
        self._managed_config = False
        self._versioned = False
        self._editors: dict[str, FileEditor] = {}
        self._pending_proposal: dict[str, Any] | None = None

        # Toolbar
        self._toolbar_file_label = ft.Text("", size=13, weight=ft.FontWeight.W_500, color=theme.TEXT)
        self._read_only_badge = ft.Text("只读", size=11, color=theme.MUTED)
        self._dirty_badge = ft.Text("", size=11, color=ft.Colors.ORANGE_500)
        self._reload_button = ft.IconButton(
            icon=ft.Icons.REFRESH,
            icon_color=theme.MUTED,
            icon_size=17,
            width=30,
            height=30,
            tooltip="重新加载",
            visible=False,
            on_click=lambda _: self._reload_current_file(),
        )
        self._format_button = ft.IconButton(
            icon=ft.Icons.FORMAT_ALIGN_LEFT,
            icon_color=theme.MUTED,
            icon_size=17,
            width=30,
            height=30,
            tooltip="格式化",
            visible=False,
            on_click=lambda _: self._format_current_file(),
        )
        self._save_button = ft.IconButton(
            icon=ft.Icons.SAVE,
            icon_color=theme.MUTED,
            icon_size=17,
            width=30,
            height=30,
            tooltip="保存",
            visible=False,
            disabled=True,
            on_click=lambda _: self._save_current_file(),
        )
        self._audit_button = ft.IconButton(
            icon=ft.Icons.FACT_CHECK,
            icon_color=theme.MUTED,
            selected_icon_color=theme.BLUE,
            icon_size=17,
            width=30,
            height=30,
            tooltip="审计记录",
            visible=False,
            on_click=lambda _: self._toggle_panel(0),
        )
        self._version_button = ft.IconButton(
            icon=ft.Icons.HISTORY,
            icon_color=theme.MUTED,
            selected_icon_color=theme.BLUE,
            icon_size=17,
            width=30,
            height=30,
            tooltip="版本历史",
            visible=False,
            on_click=lambda _: self._toggle_panel(1),
        )
        self._diff_button = ft.IconButton(
            icon=ft.Icons.DIFFERENCE,
            icon_color=theme.MUTED,
            selected_icon_color=theme.BLUE,
            icon_size=17,
            width=30,
            height=30,
            tooltip="查看 Diff",
            visible=False,
            on_click=lambda _: self._toggle_panel(2),
        )
        self._toolbar = ft.Row(
            [
                self._toolbar_file_label,
                self._read_only_badge,
                self._dirty_badge,
                ft.Container(expand=True),
                self._audit_button,
                self._version_button,
                self._diff_button,
                self._reload_button,
                self._format_button,
                self._save_button,
            ],
            spacing=8,
            height=30,
        )

        # Main content area
        self._main_area = ft.Container(
            content=ft.Text("选择一个文件以查看", size=14, color=theme.MUTED),
            expand=3,
            padding=0,
            bgcolor=theme.PANEL,
        )
        self._review_viewer: CodeViewer | None = None
        self._review_status = ft.Text(
            "是否采纳此修改？",
            size=12,
            weight=ft.FontWeight.W_600,
            color=theme.TEXT,
            expand=True,
        )
        self._review_changes = ft.Text("", size=11, color=theme.MUTED)
        self._accept_button = ft.FilledButton(
            content="采纳",
            icon=ft.Icons.CHECK,
            tooltip="采纳此配置修改",
            style=_action_button_style(theme.BLUE, "#ffffff", theme.BLUE),
            on_click=lambda _: self._accept_pending_proposal(),
        )
        self._reject_button = ft.OutlinedButton(
            content="拒绝",
            icon=ft.Icons.CLOSE,
            tooltip="拒绝此配置修改",
            style=_action_button_style(theme.PANEL_RAISED, theme.TEXT, theme.LINE_STRONG),
            on_click=lambda _: self._reject_pending_proposal(),
        )
        self._review_bar = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.RATE_REVIEW, size=16, color=theme.BLUE),
                            self._review_status,
                            self._reject_button,
                            self._accept_button,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._review_changes,
                ],
                spacing=3,
            ),
            visible=False,
            bgcolor=theme.PANEL_RAISED,
            border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
            padding=ft.Padding.symmetric(horizontal=12, vertical=7),
        )

        # Configuration history popover
        self._audit_panel = ConfigAuditPanel(
            on_view_diff=self._on_audit_view_diff,
            on_confirm=self._on_audit_confirm,
            on_reject=self._on_audit_reject,
            on_rollback=self._on_audit_rollback,
        )
        self._version_panel = VersionHistoryPanel(
            on_select_commit=self._on_version_select,
        )
        self._diff_viewer = DiffViewer()
        self._panel_index: int | None = None
        self._panel_title = ft.Text("", size=12, weight=ft.FontWeight.W_700, color=theme.TEXT)
        self._panel_content = ft.Container(
            content=self._audit_panel.build(),
            expand=True,
        )
        self._panel_overlay = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            self._panel_title,
                            ft.Container(expand=True),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                icon_size=15,
                                icon_color=theme.MUTED,
                                tooltip="关闭",
                                on_click=lambda _: self._close_panel(),
                            ),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._panel_content,
                ],
                expand=True,
                spacing=4,
            ),
            visible=False,
            width=390,
            height=270,
            top=8,
            right=8,
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
        self._work_area = ft.Stack(
            [self._main_area, self._panel_overlay],
            expand=True,
            clip_behavior=ft.ClipBehavior.NONE,
        )

        # Status bar
        self._status_lines = ft.Text("", size=11, color=theme.MUTED)
        self._status_bytes = ft.Text("", size=11, color=theme.MUTED)
        self._status_git = ft.Text("", size=11, color=theme.MUTED)
        self._status_feedback_sep = ft.Text(" · ", size=11, color=theme.SOFT_TEXT, visible=False)
        self._status_feedback = ft.Text("", size=11, color=theme.MUTED)
        self._status_bar = ft.Row(
            [
                self._status_lines,
                ft.Text(" · ", size=11, color=theme.SOFT_TEXT),
                self._status_bytes,
                ft.Text(" · ", size=11, color=theme.SOFT_TEXT),
                self._status_git,
                self._status_feedback_sep,
                self._status_feedback,
            ],
            spacing=0,
            height=24,
        )

    def open_file(
        self,
        relative_path: str,
        file_name: str | None = None,
        editable: bool = False,
        managed_config: bool = False,
        versioned: bool = False,
    ) -> None:
        if file_name is None:
            file_name = relative_path.rsplit("/", 1)[-1] if "/" in relative_path else relative_path

        self._current_path = relative_path
        self._current_file_name = file_name
        self._editable = editable
        self._managed_config = managed_config
        self._versioned = versioned
        self._toolbar_file_label.value = file_name
        self._read_only_badge.value = (
            "受控配置"
            if managed_config
            else "配置"
            if versioned
            else "可编辑"
            if editable
            else "只读"
        )
        self._read_only_badge.visible = True
        self._audit_button.visible = versioned
        self._version_button.visible = versioned
        self._diff_button.visible = versioned
        if not versioned:
            self._close_panel()

        preview = self._file_interface.preview_file(relative_path)
        content = preview.get("content", "") if isinstance(preview, dict) else ""
        language = preview.get("language", "text") if isinstance(preview, dict) else "text"
        self._current_content = content
        self._current_language = language

        if editable:
            editor = self._editors.get(relative_path)
            if not editor or not editor.dirty:
                editor = self._create_editor(relative_path, file_name, content)
                self._editors[relative_path] = editor
            self._main_area.content = editor.build()
        else:
            self._editors.pop(relative_path, None)
            viewer = CodeViewer(content=content, language=language, read_only=True)
            self._main_area.content = viewer.build()

        lines = content.count("\n") + 1 if content else 0
        size_bytes = len(content.encode("utf-8")) if content else 0
        self.update_status_bar(
            lines=lines,
            size_bytes=size_bytes,
            git_status=self._current_git_status(editor if editable else None),
            feedback=editor.message if editable else "",
            feedback_color=editor.message_color if editable else None,
        )

        if versioned:
            self._refresh_audit(relative_path)
        else:
            self._clear_pending_proposal()
        if versioned:
            self._refresh_version_history(relative_path)
        self._sync_editor_toolbar()

        self._try_update(self._toolbar)
        self._try_update(self._main_area)
        self._try_update(self._panel_overlay)

    def open_log(self, log_viewer: ft.Control) -> None:
        self._current_path = "__log__"
        self._toolbar_file_label.value = "latest.log"
        self._read_only_badge.visible = True
        self._editable = False
        self._managed_config = False
        self._versioned = False
        self._audit_button.visible = False
        self._version_button.visible = False
        self._diff_button.visible = False
        self._sync_editor_toolbar(force_hidden=True)
        self._main_area.content = log_viewer
        self._try_update(self._toolbar)
        self._try_update(self._main_area)

    def refresh_audit(self) -> None:
        if self._versioned and self._current_path and self._current_path != "__log__":
            self._refresh_audit(self._current_path)

    def has_unsaved_changes(self, relative_path: str) -> bool:
        editor = self._editors.get(relative_path)
        return bool(editor and editor.dirty)

    def close_file(self, relative_path: str) -> bool:
        if self.has_unsaved_changes(relative_path):
            return False
        self._editors.pop(relative_path, None)
        return True

    def scroll_to_review_change(self) -> None:
        if not self._review_viewer:
            return
        run_task = getattr(self._page, "run_task", None)
        if callable(run_task):
            try:
                run_task(self._review_viewer.scroll_to_first_change)
            except Exception:
                pass

    def update_status_bar(self, text: str = "", lines: int = 0, size_bytes: int = 0,
                          git_status: str = "", feedback: str = "",
                          feedback_color: str | None = None) -> None:
        self._status_lines.value = f"{lines} 行" if lines else text
        self._status_bytes.value = f"{size_bytes} bytes" if size_bytes else ""
        self._status_git.value = git_status
        self._status_feedback.value = feedback
        self._status_feedback_sep.visible = bool(feedback)
        self._status_feedback.color = feedback_color or theme.MUTED
        self._try_update(self._status_bar)

    def build(self) -> ft.Control:
        return ft.Column(
            [
                ft.Container(
                    content=self._toolbar,
                    padding=ft.Padding.only(left=12, right=12),
                    border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
                    height=32,
                ),
                self._review_bar,
                self._work_area,
                ft.Container(
                    content=self._status_bar,
                    padding=ft.Padding.only(left=12, right=12),
                    bgcolor=theme.PANEL,
                    border=ft.Border.only(top=ft.BorderSide(1, theme.LINE)),
                    height=24,
                ),
            ],
            expand=True,
            spacing=0,
        )

    # --- private ---

    @staticmethod
    def _try_update(control: ft.Control) -> None:
        try:
            if control.page:
                control.update()
        except RuntimeError:
            pass

    def _refresh_audit(self, relative_path: str) -> None:
        proposals: list[dict[str, Any]] = []
        pending = None
        if self._managed_config:
            proposals = self._config_interface.list_recent_proposals(
                limit=50,
                relative_path=relative_path,
            )
            pending = next(
                (proposal for proposal in proposals if proposal.get("status") == "pending"),
                None,
            )
        proposal_records = [
            dict(proposal, audit_kind="config_proposal")
            for proposal in proposals
            if proposal.get("status") != "pending"
        ]
        edit_records = [
            dict(record, audit_kind="file_edit", risk_level="LOW")
            for record in self._file_interface.list_edit_audits(relative_path, limit=20)
        ]
        records = sorted(
            proposal_records + edit_records,
            key=lambda record: str(record.get("created_at", "")),
            reverse=True,
        )
        self._audit_panel.refresh(records[:20])
        if pending:
            self._show_pending_proposal(pending)
        else:
            self._clear_pending_proposal()

    def review_proposal(self, proposal_id: str) -> None:
        if (
            self._pending_proposal
            and self._pending_proposal.get("proposal_id") == proposal_id
        ):
            return
        proposal = self._config_interface.get_proposal(proposal_id)
        if proposal and proposal.get("status") == "pending":
            self._show_pending_proposal(proposal)

    def _refresh_version_history(self, relative_path: str) -> None:
        if self._version_interface.enabled:
            commits = self._version_interface.list_history(relative_path)
            self._version_panel.refresh(commits)

    def _toggle_panel(self, idx: int) -> None:
        if self._panel_overlay.visible and self._panel_index == idx:
            self._close_panel()
            return
        self._show_panel(idx)

    def _show_panel(self, idx: int) -> None:
        self._panel_index = idx
        if idx == 0:
            self._panel_title.value = "审计记录"
            self._panel_content.content = self._audit_panel.build()
        elif idx == 1:
            self._panel_title.value = "版本历史"
            self._panel_content.content = self._version_panel.build()
        elif idx == 2:
            self._panel_title.value = "Diff"
            self._panel_content.content = self._diff_viewer.build()
        self._panel_overlay.visible = True
        self._audit_button.selected = idx == 0
        self._version_button.selected = idx == 1
        self._diff_button.selected = idx == 2
        self._try_update(self._panel_content)
        self._try_update(self._panel_overlay)
        self._try_update(self._toolbar)

    def _close_panel(self) -> None:
        self._panel_overlay.visible = False
        self._panel_index = None
        for button in (self._audit_button, self._version_button, self._diff_button):
            button.selected = False
        self._try_update(self._panel_overlay)
        self._try_update(self._toolbar)

    def _on_audit_view_diff(self, proposal_id: str) -> None:
        proposal = self._config_interface.get_proposal(proposal_id)
        if proposal:
            diff_text = proposal.get("diff", "")
            self._diff_viewer.update_diff(diff_text)
            self._show_panel(2)

    def _on_audit_confirm(self, proposal_id: str) -> None:
        proposal = self._config_interface.get_proposal(proposal_id) or {}
        result = self._execute_apply(
            proposal_id,
            high_risk_confirmed=proposal.get("risk_level") == "HIGH",
        )
        if result.get("status") == "saved":
            self._refresh_current_file()
        else:
            self.refresh_audit()

    def _on_audit_reject(self, proposal_id: str) -> None:
        self._execute_reject(proposal_id)
        self.refresh_audit()

    def _on_audit_rollback(self, proposal_id: str) -> None:
        result = self._config_interface.rollback_change(
            proposal_id=proposal_id,
            confirmed_by="ui_workbench",
        )
        if result.get("status") == "rolled_back":
            self._refresh_current_file()
        else:
            self.refresh_audit()

    def _on_version_select(self, commit_id: str) -> None:
        diff_result = self._version_interface.diff_commit(commit_id)
        if diff_result.get("status") == "ok":
            diff_text = diff_result.get("diff_text", "")
            self._diff_viewer.update_diff(diff_text)
            self._show_panel(2)

    def _create_editor(self, relative_path: str, file_name: str, content: str) -> FileEditor:
        return FileEditor(
            file_interface=self._file_interface,
            page=self._page,
            relative_path=relative_path,
            file_name=file_name,
            original_content=content,
            language=self._current_language,
            on_saved=self._on_direct_file_saved,
            on_state_change=self._on_editor_state_change,
        )

    def _current_editor(self) -> FileEditor | None:
        if not self._current_path or self._current_path == "__log__":
            return None
        return self._editors.get(self._current_path)

    def _save_current_file(self) -> None:
        editor = self._current_editor()
        if editor:
            editor.save()

    def _reload_current_file(self) -> None:
        editor = self._current_editor()
        if editor:
            editor.reload()

    def _format_current_file(self) -> None:
        editor = self._current_editor()
        if editor:
            editor.format_document()

    def _on_editor_state_change(self, editor: FileEditor) -> None:
        if editor is not self._current_editor():
            return
        content = editor.content
        self.update_status_bar(
            lines=editor.line_count,
            size_bytes=editor.byte_count,
            git_status=self._current_git_status(editor),
            feedback=editor.message,
            feedback_color=editor.message_color,
        )
        self._sync_editor_toolbar()

    def _on_direct_file_saved(self, _result: dict) -> None:
        editor = self._current_editor()
        if editor:
            self._current_content = editor.content
        if self._on_file_changed:
            self._on_file_changed()
        if self._versioned and self._current_path and self._current_path != "__log__":
            self._refresh_audit(self._current_path)
            self._refresh_version_history(self._current_path)
        self._sync_editor_toolbar()

    def _sync_editor_toolbar(self, force_hidden: bool = False) -> None:
        editor = self._current_editor()
        visible = bool(
            not force_hidden
            and self._editable
            and editor
            and not self._pending_proposal
            and self._current_path != "__log__"
        )
        for button in (self._reload_button, self._format_button, self._save_button):
            button.visible = visible
        saving = bool(editor and editor.saving)
        self._reload_button.disabled = not visible or saving
        self._format_button.disabled = not visible or saving
        self._save_button.disabled = not visible or saving or not bool(editor and editor.dirty)
        self._dirty_badge.value = "未保存" if visible and editor and editor.dirty else ""
        self._try_update(self._toolbar)

    def _current_git_status(self, editor: FileEditor | None = None) -> str:
        suffix = f" · {editor.status_text}" if editor else ""
        if self._managed_config:
            return f"{self._current_language.upper()} · 配置审计已启用{suffix}"
        if self._versioned and self._current_path and self._current_path != "__log__":
            return f"{self._current_language.upper()} · 配置版本已启用{suffix}"
        return f"{self._current_language.upper()}{suffix}"

    def _refresh_current_file(self) -> None:
        if self._on_file_changed:
            self._on_file_changed()
        if self._current_path and self._current_path != "__log__":
            self.open_file(
                self._current_path,
                self._toolbar_file_label.value,
                editable=self._editable,
                managed_config=self._managed_config,
                versioned=self._versioned,
            )

    def _show_pending_proposal(self, proposal: dict[str, Any]) -> None:
        self._pending_proposal = proposal
        self._read_only_badge.value = "审阅修改"
        self._review_status.value = "是否采纳此配置修改？"
        self._review_status.color = theme.TEXT
        self._review_changes.value = _proposal_changes_text(proposal)
        self._accept_button.disabled = False
        self._reject_button.disabled = False
        self._review_bar.visible = True
        self._close_panel()
        content = proposal.get("preview_content") or self._current_content
        self._review_viewer = CodeViewer(
            content=content,
            language=self._current_language,
            read_only=True,
            original_content=self._current_content,
        )
        self._main_area.content = self._review_viewer.build()
        lines = content.count("\n") + 1 if content else 0
        size_bytes = len(content.encode("utf-8")) if content else 0
        self.update_status_bar(
            lines=lines,
            size_bytes=size_bytes,
            git_status=f"{self._current_language.upper()} · 待采纳",
        )
        self._try_update(self._review_bar)
        self._try_update(self._main_area)
        self._sync_editor_toolbar(force_hidden=True)

    def _clear_pending_proposal(self) -> None:
        self._pending_proposal = None
        self._review_viewer = None
        self._review_bar.visible = False
        self._accept_button.disabled = False
        self._reject_button.disabled = False
        self._try_update(self._review_bar)
        self._sync_editor_toolbar()

    def _start_review_action(self, message: str) -> dict[str, Any] | None:
        proposal = self._pending_proposal
        if not proposal:
            return None
        self._review_status.value = message
        self._review_status.color = theme.BLUE
        self._accept_button.disabled = True
        self._reject_button.disabled = True
        self._try_update(self._review_bar)
        try:
            self._page.update()
        except Exception:
            pass
        return proposal

    def _accept_pending_proposal(self) -> None:
        proposal = self._start_review_action("正在采纳修改...")
        if not proposal:
            return
        try:
            result = self._execute_apply(
                proposal.get("proposal_id", ""),
                high_risk_confirmed=proposal.get("risk_level") == "HIGH",
            )
        except Exception as exc:
            result = {"status": "failed", "error_message": f"采纳修改失败：{exc}"}
        self._clear_pending_proposal()
        self._refresh_current_file()
        self._request_feedback(result, "config_apply")

    def _reject_pending_proposal(self) -> None:
        proposal = self._start_review_action("正在拒绝修改...")
        if not proposal:
            return
        try:
            result = self._execute_reject(proposal.get("proposal_id", ""))
        except Exception as exc:
            result = {"status": "failed", "error_message": f"拒绝修改失败：{exc}"}
        self._clear_pending_proposal()
        self._refresh_current_file()
        self._request_feedback(result, "config_reject")

    def _execute_apply(self, proposal_id: str, high_risk_confirmed: bool) -> dict:
        if self._on_config_apply:
            return self._on_config_apply(proposal_id, high_risk_confirmed)
        return self._config_interface.apply_proposal(
            proposal_id=proposal_id,
            confirmed_by="ui_workbench",
            high_risk_confirmed=high_risk_confirmed,
        )

    def _execute_reject(self, proposal_id: str) -> dict:
        if self._on_config_reject:
            return self._on_config_reject(proposal_id)
        return self._config_interface.reject_proposal(
            proposal_id=proposal_id,
            confirmed_by="ui_workbench",
        )

    def _request_feedback(self, result: dict, action_kind: str) -> None:
        if self._on_config_feedback:
            self._on_config_feedback(result, action_kind)

def _action_button_style(bgcolor: str, color: str, border_color: str) -> ft.ButtonStyle:
    return ft.ButtonStyle(
        bgcolor=bgcolor,
        color=color,
        side=ft.BorderSide(1, border_color),
        shape=ft.RoundedRectangleBorder(radius=7),
        padding=ft.Padding.symmetric(horizontal=10, vertical=7),
    )


def _proposal_changes_text(proposal: dict[str, Any]) -> str:
    changes = [
        f"{change.get('key')}: {change.get('old_value')} -> {change.get('new_value')}"
        for change in proposal.get("changes", [])
    ]
    return "拟应用：" + "；".join(changes)
