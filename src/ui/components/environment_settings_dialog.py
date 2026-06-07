from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import flet as ft

from src.interface.chat_interface import ChatInterface
from src.interface.environment_settings_interface import EnvironmentSettingsInterface
from src.interface.java_environment_interface import JavaEnvironmentInterface
from src.interface.player_ai_chat_interface import PlayerAiChatInterface
from src.ui import theme


PROVIDER_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}
PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "qwen": "Qwen",
    "custom": "其它（OpenAI 兼容）",
}
_DIALOG_MAX_WIDTH = 720
_DIALOG_MIN_WIDTH = 360
_DIALOG_HORIZONTAL_MARGIN = 48
_DIALOG_MAX_HEIGHT = 680
_DIALOG_MIN_HEIGHT = 360
_DIALOG_VERTICAL_MARGIN = 120
_PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


@dataclass
class _ProviderRow:
    provider_id: str
    kind: ft.Dropdown
    name: ft.TextField
    api_key: ft.TextField
    base_url: ft.TextField
    fallback_model: ft.TextField
    api_key_env: ft.Text
    base_url_env: ft.Text
    model_env: ft.Text
    status: ft.Text


class EnvironmentSettingsDialog:
    def __init__(
        self,
        page: ft.Page,
        environment_interface: EnvironmentSettingsInterface,
        java_interface: JavaEnvironmentInterface,
        chat_interface: ChatInterface | None = None,
        player_ai_chat_interface: PlayerAiChatInterface | None = None,
        on_saved: Callable[[dict], None] | None = None,
    ) -> None:
        self._page = page
        self._interface = environment_interface
        self._java = java_interface
        self._chat = chat_interface
        self._player_ai_chat = player_ai_chat_interface
        self._on_saved = on_saved
        self._dialog: ft.AlertDialog | None = None
        self._inspection: dict[str, Any] = {}
        self._locked_fields: set[str] = set()
        self._provider_rows: list[_ProviderRow] = []
        self._model_checkboxes: list[ft.Checkbox] = []
        self._model_entries: dict[str, dict[str, Any]] = {}
        self._model_list: ft.Column | None = None
        self._model_refresh_generation = 0
        self._player_ai_entries: list[dict] = []
        self._player_ai_known_players: list[dict] = []

    def show(self, *, first_run: bool = False) -> None:
        try:
            self._inspection = self._interface.inspect()
        except Exception as exc:
            self._show_error(f"无法读取设置：{exc}")
            return

        self._locked_fields = set(self._inspection.get("locked_fields") or [])
        self._provider_rows = [
            self._make_provider_row(provider)
            for provider in self._inspection.get("providers", [])
        ]
        if not self._provider_rows:
            self._provider_rows = [self._make_provider_row(_empty_provider("deepseek"))]
        self._load_player_ai_settings()

        self._provider_list = ft.Column(
            controls=[self._provider_card(row) for row in self._provider_rows],
            spacing=8,
        )
        self._add_provider_button = ft.OutlinedButton(
            "添加服务商",
            icon=ft.Icons.ADD,
            on_click=self._add_provider_row,
            style=_outlined_button_style(),
        )
        self._server_dir = _settings_field(
            label="Minecraft 服务器目录",
            value=str(self._inspection.get("server_dir") or ""),
            expand=True,
        )
        self._java_xmx = _settings_field(
            label="服务器最大内存",
            value=str(self._inspection.get("java_xmx") or "2G"),
            hint_text="例如 2G",
        )
        self._server_jar = _settings_field(
            label="服务端 jar",
            value=str(self._inspection.get("server_jar") or "server.jar"),
            expand=True,
            embedded_label=False,
        )
        self._java_path = _settings_field(
            label="Java 路径",
            value=str(self._inspection.get("java_path") or "java"),
            expand=True,
            embedded_label=False,
        )
        self._feedback = ft.Text("", size=12, color=theme.MUTED, selectable=True)
        self._save_button = ft.FilledButton(
            "保存设置",
            icon=ft.Icons.SAVE,
            on_click=self._save,
            style=ft.ButtonStyle(
                bgcolor=theme.BLUE,
                color="#ffffff",
                shape=ft.RoundedRectangleBorder(radius=7),
            ),
        )

        server_detection = self._inspection.get("server_detection") or {}
        java_detection = self._inspection.get("java_detection") or {}
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS, color=theme.BLUE, size=20),
                    ft.Column(
                        controls=[
                            ft.Text(
                                "基础设置",
                                size=17,
                                weight=ft.FontWeight.W_700,
                                color=theme.TEXT,
                            ),
                            ft.Text(
                                "AI 服务商需提供 OpenAI-compatible 接口；其它参数会继续使用安全默认值。",
                                size=11,
                                color=theme.MUTED,
                            ),
                        ],
                        spacing=2,
                    ),
                ],
                spacing=9,
            ),
            content=ft.Container(
                width=_dialog_content_width(self._page),
                height=_dialog_content_height(self._page),
                content=ft.Column(
                    controls=[
                        _section_title(
                            "AI 服务商",
                            "可同时保留多个服务商；API Key 与对应 .env key 会直接显示，便于检查和修改。",
                        ),
                        self._provider_list,
                        ft.Row(
                            controls=[
                                self._add_provider_button,
                                ft.Text(
                                    "其它服务商必须兼容 OpenAI 的 /v1/chat/completions 与 /v1/models 接口。",
                                    size=11,
                                    color=theme.MUTED,
                                    expand=True,
                                ),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        self._build_model_settings(),
                        *(
                            [self._build_player_ai_settings()]
                            if self._player_ai_chat is not None
                            else []
                        ),
                        ft.Divider(height=1, color=theme.LINE),
                        _section_title(
                            "服务器",
                            _server_detection_summary(server_detection),
                        ),
                        ft.Row(
                            controls=[
                                self._server_dir,
                                ft.IconButton(
                                    icon=ft.Icons.FOLDER_OPEN,
                                    icon_color=theme.MUTED,
                                    tooltip="选择服务器目录",
                                    on_click=self._choose_server_directory,
                                ),
                            ],
                            spacing=6,
                        ),
                        self._java_xmx,
                        ft.Text(
                            f"按本机内存推荐：{self._inspection.get('recommended_java_xmx', '2G')}",
                            size=11,
                            color=theme.MUTED,
                        ),
                        ft.ExpansionTile(
                            title=ft.Text("手动调整", size=13, color=theme.TEXT),
                            subtitle=ft.Text(
                                "仅在自动检测不正确时修改",
                                size=11,
                                color=theme.MUTED,
                            ),
                            leading=ft.Icon(ft.Icons.TUNE, color=theme.MUTED, size=18),
                            controls=[
                                ft.Container(
                                    content=ft.Column(
                                        controls=[
                                            _field_group("服务端 jar", self._server_jar),
                                            _field_group("Java 路径", self._java_path),
                                            ft.Text(
                                                _java_detection_summary(java_detection),
                                                size=11,
                                                color=theme.MUTED,
                                                selectable=True,
                                            ),
                                            ft.Row(
                                                controls=[
                                                    ft.OutlinedButton(
                                                        "自动准备 Java",
                                                        icon=ft.Icons.DOWNLOAD,
                                                        on_click=self._prepare_java,
                                                        style=_outlined_button_style(),
                                                    ),
                                                ],
                                                alignment=ft.MainAxisAlignment.END,
                                            ),
                                        ],
                                        spacing=9,
                                    ),
                                    padding=ft.Padding.only(left=10, right=10, bottom=10),
                                )
                            ],
                            collapsed_bgcolor=theme.PANEL_SOFT,
                            bgcolor=theme.PANEL_SOFT,
                            collapsed_text_color=theme.TEXT,
                            text_color=theme.TEXT,
                            collapsed_icon_color=theme.MUTED,
                            icon_color=theme.MUTED,
                            shape=ft.RoundedRectangleBorder(radius=7),
                            collapsed_shape=ft.RoundedRectangleBorder(radius=7),
                        ),
                        self._feedback,
                    ],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(
                    "稍后设置" if first_run else "取消",
                    on_click=lambda _: self._close(),
                ),
                self._save_button,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            bgcolor=theme.PANEL,
            content_padding=ft.Padding.symmetric(horizontal=20, vertical=12),
            actions_padding=ft.Padding.only(left=20, right=20, bottom=16),
            shape=ft.RoundedRectangleBorder(radius=theme.RADIUS),
        )
        self._dialog = dialog
        self._show(dialog)
        self._refresh_models_silently()

    def _make_provider_row(self, provider: dict[str, Any]) -> _ProviderRow:
        provider_id = str(provider.get("id") or "deepseek")
        kind_value = provider_id if provider_id in {"deepseek", "qwen"} else "custom"
        row = _ProviderRow(
            provider_id=provider_id,
            kind=ft.Dropdown(
                value=kind_value,
                options=[
                    ft.DropdownOption(key="deepseek", text="DeepSeek"),
                    ft.DropdownOption(key="qwen", text="Qwen"),
                    ft.DropdownOption(key="custom", text="其它（OpenAI 兼容）"),
                ],
                label="服务商",
                dense=True,
                bgcolor=theme.INPUT_BG,
                color=theme.TEXT,
                border_color=theme.LINE,
                focused_border_color=theme.LINE_STRONG,
                border_radius=7,
            ),
            name=_settings_field(
                label="显示名称",
                value=str(provider.get("name") or provider.get("label") or ""),
            ),
            api_key=_settings_field(
                label="API Key",
                value=str(provider.get("api_key") or ""),
                password=True,
                can_reveal_password=True,
            ),
            base_url=_settings_field(
                label="Base URL",
                value=str(provider.get("base_url") or provider.get("default_base_url") or ""),
            ),
            fallback_model=_settings_field(
                label="回退模型",
                value=str(provider.get("fallback_model") or provider.get("default_model") or ""),
                hint_text="模型列表不可用时使用",
            ),
            api_key_env=ft.Text(
                f"Key: {provider.get('api_key_env_name', '')}",
                size=11,
                color=theme.MUTED,
            ),
            base_url_env=ft.Text(
                f"URL: {provider.get('base_url_env_name', '')}",
                size=11,
                color=theme.MUTED,
            ),
            model_env=ft.Text(
                f"Model: {provider.get('model_env_name', '')}",
                size=11,
                color=theme.MUTED,
            ),
            status=ft.Text(
                "已配置" if provider.get("api_key_configured") else "未配置",
                size=11,
                color=theme.GREEN if provider.get("api_key_configured") else theme.MUTED,
            ),
        )
        row.kind.on_select = lambda _event, target=row: self._on_provider_kind_change(target)
        row.api_key.on_change = (
            lambda _event, target=row: self._schedule_provider_model_refresh(target)
        )
        row.base_url.on_change = (
            lambda _event, target=row: self._schedule_provider_model_refresh(target)
        )
        self._apply_provider_lock(row)
        return row

    def _provider_card(self, row: _ProviderRow) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            row.kind,
                            row.name,
                            ft.OutlinedButton(
                                "测试连接",
                                icon=ft.Icons.CLOUD_DONE,
                                on_click=lambda _event, target=row: self._test_connection(target),
                                style=_outlined_button_style(),
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(controls=[row.api_key, row.base_url], spacing=8),
                    ft.Row(controls=[row.fallback_model], spacing=8),
                    ft.Row(
                        controls=[
                            row.api_key_env,
                            row.base_url_env,
                            row.model_env,
                            row.status,
                        ],
                        spacing=10,
                        wrap=True,
                    ),
                ],
                spacing=8,
            ),
            bgcolor=theme.PANEL_SOFT,
            border=ft.Border.all(1, theme.LINE),
            border_radius=7,
            padding=ft.Padding.all(10),
        )

    def _build_model_settings(self) -> ft.Control:
        models: list[dict] = []
        if self._chat is None:
            message = "模型服务未接入。"
        else:
            try:
                try:
                    models = self._chat.list_ai_models(
                        include_disabled=True,
                        discover=False,
                    )
                except TypeError:
                    models = self._chat.list_ai_models(include_disabled=True)
                message = ""
            except Exception as exc:
                models = []
                message = f"无法读取模型列表：{exc}"

        self._model_entries = {
            str(model.get("selection_id") or model.get("id")): dict(model)
            for model in models
            if isinstance(model, dict) and (model.get("selection_id") or model.get("id"))
        }
        self._model_list = ft.Column(spacing=6)
        self._render_model_list(message=message)

        return ft.ExpansionTile(
            title=ft.Text("启用模型", size=13, color=theme.TEXT),
            subtitle=ft.Text("防止模型菜单过长", size=11, color=theme.MUTED),
            leading=ft.Icon(ft.Icons.CHECKLIST, color=theme.MUTED, size=18),
            controls=[
                ft.Container(
                    content=self._model_list,
                    padding=ft.Padding.only(left=10, right=10, bottom=10),
                )
            ],
            collapsed_bgcolor=theme.PANEL_SOFT,
            bgcolor=theme.PANEL_SOFT,
            collapsed_text_color=theme.TEXT,
            text_color=theme.TEXT,
            collapsed_icon_color=theme.MUTED,
            icon_color=theme.MUTED,
            shape=ft.RoundedRectangleBorder(radius=7),
            collapsed_shape=ft.RoundedRectangleBorder(radius=7),
        )

    def _render_model_list(self, *, message: str = "") -> None:
        if self._model_list is None:
            return
        selected_values = {
            str(checkbox.data): bool(checkbox.value)
            for checkbox in self._model_checkboxes
        }
        controls: list[ft.Control] = [
            ft.Text(
                "只勾选常用模型，聊天输入框的模型菜单会只显示已启用模型。",
                size=11,
                color=theme.MUTED,
            )
        ]
        self._model_checkboxes = []
        provider_order = {
            row.provider_id: index
            for index, row in enumerate(self._provider_rows)
        }
        ordered_entries = sorted(
            self._model_entries.items(),
            key=lambda item: provider_order.get(
                str(item[1].get("provider") or ""),
                len(provider_order),
            ),
        )
        for selection_id, model in ordered_entries:
            checkbox = ft.Checkbox(
                label=(
                    f"{model.get('provider', '')} · "
                    f"{model.get('display_name') or model.get('id')}"
                ),
                value=selected_values.get(
                    selection_id,
                    bool(model.get("enabled", False)),
                ),
                data=selection_id,
                fill_color=theme.BLUE,
                check_color="#ffffff",
                label_style=ft.TextStyle(color=theme.TEXT, size=12),
            )
            self._model_checkboxes.append(checkbox)
            controls.append(checkbox)
        if message:
            controls.append(ft.Text(message, size=12, color=theme.MUTED))
        elif not self._model_entries:
            controls.append(
                ft.Text(
                    "配置 API Key 后会自动刷新可用模型。",
                    size=12,
                    color=theme.MUTED,
                )
            )
        self._model_list.controls = controls

    def _merge_provider_models(
        self,
        provider: str,
        model_ids: list[str],
        *,
        fallback_model: str = "",
    ) -> None:
        normalized = provider.strip().lower()
        self._model_entries = {
            selection_id: model
            for selection_id, model in self._model_entries.items()
            if str(model.get("provider") or "").strip().lower() != normalized
        }
        for index, model_id in enumerate(model_ids):
            clean_id = str(model_id).strip()
            if not clean_id:
                continue
            selection_id = f"{normalized}::{clean_id}"
            self._model_entries[selection_id] = {
                "id": clean_id,
                "selection_id": selection_id,
                "provider": normalized,
                "display_name": clean_id,
                "enabled": clean_id == fallback_model or (
                    not fallback_model and index == 0
                ),
            }
        self._render_model_list()
        self._update()

    def _refresh_models_silently(self) -> None:
        if self._chat is None:
            return
        self._model_refresh_generation += 1
        generation = self._model_refresh_generation

        def worker() -> None:
            try:
                try:
                    models = self._chat.list_ai_models(
                        include_disabled=True,
                        refresh=True,
                    )
                except TypeError:
                    models = self._chat.list_ai_models(include_disabled=True)
            except Exception:
                return
            if generation != self._model_refresh_generation:
                return
            self._model_entries = {
                str(model.get("selection_id") or model.get("id")): dict(model)
                for model in models
                if isinstance(model, dict)
                and (model.get("selection_id") or model.get("id"))
            }
            self._render_model_list()
            self._update()

        self._run_thread(worker)

    def _schedule_provider_model_refresh(self, row: _ProviderRow) -> None:
        api_key = str(row.api_key.value or "").strip()
        base_url = str(row.base_url.value or "").strip()
        if not api_key or not base_url.startswith(("http://", "https://")):
            return
        self._model_refresh_generation += 1
        generation = self._model_refresh_generation

        def worker() -> None:
            time.sleep(0.6)
            if generation != self._model_refresh_generation:
                return
            result = self._interface.test_ai_connection(
                row.provider_id,
                api_key,
                base_url,
            )
            if (
                generation == self._model_refresh_generation
                and result.get("status") == "ok"
            ):
                self._merge_provider_models(
                    row.provider_id,
                    list(result.get("models") or []),
                    fallback_model=str(row.fallback_model.value or "").strip(),
                )

        self._run_thread(worker)

    def _on_provider_kind_change(self, row: _ProviderRow) -> None:
        kind = str(row.kind.value or "deepseek")
        if kind in PROVIDER_BASE_URLS:
            row.provider_id = kind
            row.name.value = PROVIDER_LABELS[kind]
            row.base_url.value = PROVIDER_BASE_URLS[kind]
            row.api_key_env.value = f"Key: {kind.upper()}_API_KEY"
            row.base_url_env.value = f"URL: {kind.upper()}_BASE_URL"
            row.model_env.value = f"Model: {kind.upper()}_MODEL"
        else:
            slot = self._next_custom_slot()
            row.provider_id = f"custom_{slot}"
            row.name.value = f"其它服务商 {slot}"
            row.base_url.value = ""
            row.api_key_env.value = f"Key: AI_CUSTOM_PROVIDER_{slot}_API_KEY"
            row.base_url_env.value = f"URL: AI_CUSTOM_PROVIDER_{slot}_BASE_URL"
            row.model_env.value = f"Model: AI_CUSTOM_PROVIDER_{slot}_MODEL"
        self._apply_provider_lock(row)
        self._update()

    def _load_player_ai_settings(self) -> None:
        self._player_ai_settings = {
            "enabled": True,
            "audience": "all",
            "list_mode": "blocklist",
            "access_entries": [],
        }
        self._player_ai_entries = []
        self._player_ai_known_players = []
        if self._player_ai_chat is None:
            return
        try:
            self._player_ai_settings = self._player_ai_chat.get_settings()
            self._player_ai_known_players = self._player_ai_chat.list_known_players()
            known_by_name = {
                str(player.get("name") or "").lower(): player
                for player in self._player_ai_known_players
            }
            self._player_ai_entries = []
            for entry in self._player_ai_settings.get("access_entries", []):
                item = dict(entry)
                known = known_by_name.get(
                    str(item.get("display_name") or "").lower(),
                    {},
                )
                item["is_operator"] = bool(known.get("is_operator"))
                self._player_ai_entries.append(item)
        except Exception:
            self._player_ai_settings = {
                "enabled": True,
                "audience": "all",
                "list_mode": "blocklist",
                "access_entries": [],
            }

    def _build_player_ai_settings(self) -> ft.Control:
        settings = self._player_ai_settings
        self._player_ai_enabled = ft.Switch(
            label="启用游戏内 AI",
            value=bool(settings.get("enabled", True)),
            active_color=theme.BLUE,
            label_text_style=ft.TextStyle(color=theme.TEXT, size=12),
        )
        self._player_ai_audience = ft.SegmentedButton(
            segments=[
                ft.Segment(
                    value="all",
                    icon=ft.Icons.GROUPS,
                    label=ft.Text("所有玩家", size=12),
                ),
                ft.Segment(
                    value="operators",
                    icon=ft.Icons.ADMIN_PANEL_SETTINGS,
                    label=ft.Text("管理员", size=12),
                ),
            ],
            selected=[str(settings.get("audience") or "all")],
            show_selected_icon=False,
            style=_segmented_button_style(),
            on_change=self._update_player_ai_list_hint,
        )
        self._player_ai_list_mode = ft.SegmentedButton(
            segments=[
                ft.Segment(
                    value="allowlist",
                    icon=ft.Icons.VERIFIED_USER,
                    label=ft.Text("白名单", size=12),
                ),
                ft.Segment(
                    value="blocklist",
                    icon=ft.Icons.BLOCK,
                    label=ft.Text("黑名单", size=12),
                ),
            ],
            selected=[str(settings.get("list_mode") or "blocklist")],
            show_selected_icon=False,
            style=_segmented_button_style(),
            on_change=self._update_player_ai_list_hint,
        )
        self._player_ai_list_hint = ft.Text(
            _player_ai_list_hint(
                str(settings.get("audience") or "all"),
                str(settings.get("list_mode") or "blocklist"),
            ),
            size=11,
            color=theme.MUTED,
        )
        self._player_ai_player_input = _settings_field(
            label="玩家名",
            hint_text="Steve",
            expand=True,
        )
        self._player_ai_player_input.on_change = self._refresh_player_ai_suggestions
        self._player_ai_player_input.on_submit = self._add_player_ai_entry
        self._player_ai_suggestions = ft.Column(spacing=2, visible=False)
        self._player_ai_entry_list = ft.Column(spacing=0)
        self._render_player_ai_entries()

        return ft.ExpansionTile(
            title=ft.Text("游戏内 @AI", size=13, color=theme.TEXT),
            subtitle=ft.Text("权限、名单与独立短上下文", size=11, color=theme.MUTED),
            leading=ft.Icon(ft.Icons.FORUM, color=theme.MUTED, size=18),
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            self._player_ai_enabled,
                            ft.Text("可用玩家", size=11, color=theme.MUTED),
                            self._player_ai_audience,
                            ft.Text("名单规则", size=11, color=theme.MUTED),
                            self._player_ai_list_mode,
                            self._player_ai_list_hint,
                            ft.Row(
                                controls=[
                                    self._player_ai_player_input,
                                    ft.IconButton(
                                        icon=ft.Icons.PERSON_ADD,
                                        icon_color=theme.BLUE,
                                        tooltip="加入名单",
                                        on_click=self._add_player_ai_entry,
                                    ),
                                ],
                                spacing=4,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            self._player_ai_suggestions,
                            self._player_ai_entry_list,
                        ],
                        spacing=7,
                    ),
                    padding=ft.Padding.only(left=10, right=10, bottom=10),
                )
            ],
            collapsed_bgcolor=theme.PANEL_SOFT,
            bgcolor=theme.PANEL_SOFT,
            collapsed_text_color=theme.TEXT,
            text_color=theme.TEXT,
            collapsed_icon_color=theme.MUTED,
            icon_color=theme.MUTED,
            shape=ft.RoundedRectangleBorder(radius=7),
            collapsed_shape=ft.RoundedRectangleBorder(radius=7),
        )

    def _update_player_ai_list_hint(self, _event=None) -> None:
        self._player_ai_list_hint.value = _player_ai_list_hint(
            _selected_segment(self._player_ai_audience, "all"),
            _selected_segment(self._player_ai_list_mode, "blocklist"),
        )
        self._update()

    def _refresh_player_ai_suggestions(self, _event=None) -> None:
        query = str(self._player_ai_player_input.value or "").strip().lower()
        existing = {
            str(entry.get("display_name") or "").lower()
            for entry in self._player_ai_entries
        }
        matches = [
            player
            for player in self._player_ai_known_players
            if query
            and query in str(player.get("name") or "").lower()
            and str(player.get("name") or "").lower() not in existing
        ][:6]
        self._player_ai_suggestions.controls = [
            ft.TextButton(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.ADMIN_PANEL_SETTINGS
                            if player.get("is_operator")
                            else ft.Icons.PERSON,
                            size=15,
                            color=theme.AMBER
                            if player.get("is_operator")
                            else theme.MUTED,
                        ),
                        ft.Text(
                            str(player.get("name") or ""),
                            size=12,
                            color=theme.TEXT,
                        ),
                    ],
                    spacing=6,
                ),
                on_click=lambda _event, item=player: self._add_player_ai_entry(
                    player=item
                ),
            )
            for player in matches
        ]
        self._player_ai_suggestions.visible = bool(matches)
        self._update()

    def _add_player_ai_entry(self, _event=None, *, player: dict | None = None) -> None:
        name = str(
            (player or {}).get("name")
            or self._player_ai_player_input.value
            or ""
        ).strip()
        if not _PLAYER_NAME_RE.fullmatch(name):
            self._set_feedback("玩家名需为 3-16 位字母、数字或下划线。", error=True)
            return
        known = player or next(
            (
                item
                for item in self._player_ai_known_players
                if str(item.get("name") or "").lower() == name.lower()
            ),
            {},
        )
        by_key = {
            str(entry.get("display_name") or "").lower(): dict(entry)
            for entry in self._player_ai_entries
        }
        by_key[name.lower()] = {
            "player_key": name.lower(),
            "display_name": name,
            "player_uuid": known.get("uuid"),
            "is_operator": bool(known.get("is_operator")),
        }
        self._player_ai_entries = sorted(
            by_key.values(),
            key=lambda entry: str(entry.get("display_name") or "").lower(),
        )
        self._player_ai_player_input.value = ""
        self._player_ai_suggestions.visible = False
        self._render_player_ai_entries()
        self._update()

    def _remove_player_ai_entry(self, player_key: str) -> None:
        self._player_ai_entries = [
            entry
            for entry in self._player_ai_entries
            if str(entry.get("player_key") or "").lower() != player_key.lower()
        ]
        self._render_player_ai_entries()
        self._update()

    def _render_player_ai_entries(self) -> None:
        if not self._player_ai_entries:
            self._player_ai_entry_list.controls = [
                ft.Text("名单为空", size=11, color=theme.MUTED)
            ]
            return
        self._player_ai_entry_list.controls = [
            ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.ADMIN_PANEL_SETTINGS
                            if entry.get("is_operator")
                            else ft.Icons.PERSON,
                            size=16,
                            color=theme.AMBER
                            if entry.get("is_operator")
                            else theme.MUTED,
                        ),
                        ft.Text(
                            str(entry.get("display_name") or ""),
                            size=12,
                            color=theme.TEXT,
                            expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_color=theme.MUTED,
                            tooltip="移出名单",
                            on_click=lambda _event, key=str(
                                entry.get("player_key") or ""
                            ): self._remove_player_ai_entry(key),
                        ),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
                height=40,
            )
            for entry in self._player_ai_entries
        ]

    def _player_ai_payload(self) -> dict:
        return {
            "enabled": bool(self._player_ai_enabled.value),
            "audience": _selected_segment(self._player_ai_audience, "all"),
            "list_mode": _selected_segment(
                self._player_ai_list_mode,
                "blocklist",
            ),
            "access_entries": [
                {
                    "display_name": entry.get("display_name"),
                    "player_uuid": entry.get("player_uuid"),
                }
                for entry in self._player_ai_entries
            ],
        }

    def _add_provider_row(self, _event=None) -> None:
        slot = self._next_custom_slot()
        row = self._make_provider_row(_empty_provider(f"custom_{slot}"))
        self._provider_rows.append(row)
        self._provider_list.controls.append(self._provider_card(row))
        self._update()

    def _next_custom_slot(self) -> int:
        used = {
            int(row.provider_id.split("_", 1)[1])
            for row in self._provider_rows
            if row.provider_id.startswith("custom_")
            and row.provider_id.split("_", 1)[1].isdigit()
        }
        slot = 1
        while slot in used:
            slot += 1
        return slot

    def _choose_server_directory(self, _event=None) -> None:
        try:
            selected = ft.FilePicker().get_directory_path(
                dialog_title="选择 Minecraft 服务器目录",
                initial_directory=self._server_dir.value or None,
            )
        except Exception as exc:
            self._set_feedback(f"无法打开目录选择器：{exc}", error=True)
            return
        if selected:
            self._server_dir.value = selected
            self._update()

    def _test_connection(self, row: _ProviderRow) -> None:
        self._model_refresh_generation += 1
        generation = self._model_refresh_generation
        self._set_busy(testing=True)
        row.status.value = "正在测试连接..."
        row.status.color = theme.MUTED
        self._update()

        def worker() -> None:
            result = self._interface.test_ai_connection(
                row.provider_id,
                row.api_key.value or "",
                row.base_url.value or "",
            )
            if generation != self._model_refresh_generation:
                return
            self._set_busy(testing=False)
            row.status.value = str(result.get("message") or "测试完成。")
            row.status.color = theme.GREEN if result.get("status") == "ok" else theme.RED
            if result.get("status") == "ok":
                self._merge_provider_models(
                    row.provider_id,
                    list(result.get("models") or []),
                    fallback_model=str(row.fallback_model.value or "").strip(),
                )
            self._update()

        self._run_thread(worker)

    def _prepare_java(self, _event=None) -> None:
        self._set_feedback("正在检查并准备匹配的 Java...")

        def worker() -> None:
            result = self._java.ensure_environment()
            selected = result.get("selected_java") or {}
            if selected.get("java_path"):
                self._java_path.value = str(selected["java_path"])
            self._set_feedback(
                str(result.get("message") or "Java 检查完成。"),
                error=result.get("status") in {"failed", "needs_input"},
            )

        self._run_thread(worker)

    def _save(self, _event=None) -> None:
        self._model_refresh_generation += 1
        self._set_busy(saving=True)

        def worker() -> None:
            try:
                if self._player_ai_chat is not None:
                    self._player_ai_chat.save_settings(self._player_ai_payload())
                result = self._interface.save_basic_settings(
                    {
                        "provider": self._inspection.get("provider"),
                        "providers": self._provider_payloads(),
                        "server_dir": self._server_dir.value,
                        "java_xmx": self._java_xmx.value,
                        "server_jar": self._server_jar.value,
                        "java_path": self._java_path.value,
                    }
                )
                if self._chat is not None and self._model_checkboxes:
                    self._chat.set_enabled_ai_models([
                        str(checkbox.data)
                        for checkbox in self._model_checkboxes
                        if checkbox.value
                    ])
            except Exception as exc:
                self._set_busy(saving=False)
                self._set_feedback(str(exc), error=True)
                return
            self._close()
            if self._on_saved is not None:
                self._on_saved(result)

        self._run_thread(worker)

    def _provider_payloads(self) -> list[dict[str, Any]]:
        payloads = []
        for row in self._provider_rows:
            kind = str(row.kind.value or "")
            provider_id = kind if kind in {"deepseek", "qwen"} else row.provider_id
            payloads.append({
                "id": provider_id,
                "kind": "custom" if kind == "custom" else "builtin",
                "name": row.name.value or "",
                "api_key": row.api_key.value or "",
                "base_url": row.base_url.value or "",
                "fallback_model": row.fallback_model.value or "",
            })
        return payloads

    def _apply_provider_lock(self, row: _ProviderRow) -> None:
        api_env = _env_name_from_label(row.api_key_env.value)
        base_env = _env_name_from_label(row.base_url_env.value)
        model_env = _env_name_from_label(row.model_env.value)
        row.api_key.disabled = api_env in self._locked_fields
        row.base_url.disabled = base_env in self._locked_fields
        row.fallback_model.disabled = model_env in self._locked_fields
        row.kind.disabled = "AI_DEFAULT_PROVIDER" in self._locked_fields
        row.name.disabled = (
            row.provider_id.startswith("custom_")
            and f"AI_CUSTOM_PROVIDER_{row.provider_id.split('_', 1)[1]}_NAME"
            in self._locked_fields
        )

    def _set_busy(self, *, testing: bool = False, saving: bool = False) -> None:
        self._add_provider_button.disabled = testing or saving
        self._save_button.disabled = testing or saving
        self._update()

    def _set_feedback(self, message: str, error: bool = False) -> None:
        self._feedback.value = message
        self._feedback.color = theme.RED if error else theme.GREEN
        self._update()

    def _run_thread(self, callback: Callable[[], None]) -> None:
        run_thread = getattr(self._page, "run_thread", None)
        if callable(run_thread):
            run_thread(callback)
            return
        threading.Thread(target=callback, daemon=True).start()

    def _show(self, dialog: ft.AlertDialog) -> None:
        show_dialog = getattr(self._page, "show_dialog", None)
        if callable(show_dialog):
            show_dialog(dialog)
            return
        dialog.open = True
        self._update()

    def _close(self) -> None:
        pop_dialog = getattr(self._page, "pop_dialog", None)
        if callable(pop_dialog):
            pop_dialog()
            return
        if self._dialog is not None:
            self._dialog.open = False
        self._update()

    def _show_error(self, message: str) -> None:
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("设置不可用", color=theme.TEXT),
            content=ft.Text(message, color=theme.RED),
            actions=[ft.TextButton("关闭", on_click=lambda _: self._close())],
            bgcolor=theme.PANEL,
        )
        self._dialog = dialog
        self._show(dialog)

    def _update(self) -> None:
        try:
            self._page.update()
        except Exception:
            pass


def _settings_field(
    *,
    label: str,
    value: str = "",
    hint_text: str | None = None,
    password: bool = False,
    can_reveal_password: bool = False,
    expand: bool = False,
    embedded_label: bool = True,
) -> ft.TextField:
    return ft.TextField(
        label=label if embedded_label else None,
        value=value,
        hint_text=hint_text,
        password=password,
        can_reveal_password=can_reveal_password,
        dense=True,
        height=46,
        border_radius=7,
        border_color=theme.LINE,
        focused_border_color=theme.LINE_STRONG,
        bgcolor=theme.INPUT_BG,
        color=theme.TEXT,
        cursor_color=theme.BLUE,
        text_size=13,
        expand=expand,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=8)
        if not embedded_label
        else None,
    )


def _field_group(label: str, field: ft.TextField) -> ft.Column:
    return ft.Column(
        controls=[
            ft.Text(label, size=11, color=theme.MUTED),
            ft.Row(controls=[field], spacing=0),
        ],
        spacing=3,
    )


def _section_title(title: str, subtitle: str) -> ft.Control:
    return ft.Column(
        controls=[
            ft.Text(title, size=14, weight=ft.FontWeight.W_700, color=theme.TEXT),
            ft.Text(subtitle, size=11, color=theme.MUTED),
        ],
        spacing=2,
    )


def _server_detection_summary(detection: dict) -> str:
    if detection.get("ready"):
        source = "自动检测" if detection.get("source") == "detected" else "当前配置"
        return f"{source}已找到可启动的服务器文件。"
    return "尚未检测到启动脚本或服务端核心，请选择已有服务器目录。"


def _java_detection_summary(detection: dict) -> str:
    return str(detection.get("message") or "尚未检测 Java 环境。")


def _outlined_button_style() -> ft.ButtonStyle:
    return ft.ButtonStyle(
        color=theme.TEXT,
        side=ft.BorderSide(1, theme.LINE_STRONG),
        shape=ft.RoundedRectangleBorder(radius=7),
    )


def _segmented_button_style() -> ft.ButtonStyle:
    return ft.ButtonStyle(
        color={
            ft.ControlState.SELECTED: "#ffffff",
            ft.ControlState.DEFAULT: theme.TEXT,
        },
        bgcolor={
            ft.ControlState.SELECTED: theme.BLUE,
            ft.ControlState.DEFAULT: theme.INPUT_BG,
        },
        side=ft.BorderSide(1, theme.LINE_STRONG),
        shape=ft.RoundedRectangleBorder(radius=7),
    )


def _selected_segment(control: ft.SegmentedButton, default: str) -> str:
    selected = list(control.selected or [])
    return str(selected[0]) if selected else default


def _player_ai_list_hint(audience: str, list_mode: str) -> str:
    if audience == "operators" and list_mode == "allowlist":
        return "管理员自动允许；名单用于额外允许普通玩家。"
    if audience == "operators":
        return "仅管理员默认允许；名单用于排除指定管理员。"
    if list_mode == "allowlist":
        return "所有玩家自动允许；无需把玩家逐个加入名单。"
    return "所有玩家默认允许；名单用于排除指定玩家。"


def _dialog_content_width(page: ft.Page) -> int:
    width = getattr(page, "width", None)
    if isinstance(width, (int, float)) and width > 0:
        return int(
            max(
                _DIALOG_MIN_WIDTH,
                min(_DIALOG_MAX_WIDTH, width - _DIALOG_HORIZONTAL_MARGIN),
            )
        )
    return _DIALOG_MAX_WIDTH


def _dialog_content_height(page: ft.Page) -> int:
    height = getattr(page, "height", None)
    if isinstance(height, (int, float)) and height > 0:
        return int(
            max(
                _DIALOG_MIN_HEIGHT,
                min(_DIALOG_MAX_HEIGHT, height - _DIALOG_VERTICAL_MARGIN),
            )
        )
    return _DIALOG_MAX_HEIGHT


def _empty_provider(provider_id: str) -> dict[str, Any]:
    if provider_id in {"deepseek", "qwen"}:
        return {
            "id": provider_id,
            "label": PROVIDER_LABELS[provider_id],
            "name": PROVIDER_LABELS[provider_id],
            "api_key": "",
            "base_url": PROVIDER_BASE_URLS[provider_id],
            "fallback_model": "",
            "api_key_configured": False,
            "api_key_env_name": f"{provider_id.upper()}_API_KEY",
            "base_url_env_name": f"{provider_id.upper()}_BASE_URL",
            "model_env_name": f"{provider_id.upper()}_MODEL",
        }
    slot = provider_id.split("_", 1)[1] if "_" in provider_id else "1"
    return {
        "id": f"custom_{slot}",
        "label": f"其它服务商 {slot}",
        "name": f"其它服务商 {slot}",
        "api_key": "",
        "base_url": "",
        "fallback_model": "",
        "api_key_configured": False,
        "api_key_env_name": f"AI_CUSTOM_PROVIDER_{slot}_API_KEY",
        "base_url_env_name": f"AI_CUSTOM_PROVIDER_{slot}_BASE_URL",
        "model_env_name": f"AI_CUSTOM_PROVIDER_{slot}_MODEL",
    }


def _env_name_from_label(value: str) -> str:
    return value.split(":", 1)[1].strip() if ":" in value else value.strip()
