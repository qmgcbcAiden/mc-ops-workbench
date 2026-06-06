from __future__ import annotations

import asyncio
import re
import threading
import time
from datetime import datetime

import flet as ft

from src.config.settings import Settings
from src.interface.dashboard_interface import DashboardInterfaces
from src.ui import theme
from src.ui.components.addon_diagnostics_panel import AddonDiagnosticsPanel
from src.ui.components.chat_panel import CHAT_FOOTER_LEFT_PADDING, CHAT_FOOTER_RIGHT_PADDING, ChatPanel
from src.ui.components.code_workbench import CodeWorkbench
from src.ui.components.command_console import CommandConsole
from src.ui.components.common import MetricCardController, panel, status_pill, tag, text_field
from src.ui.components.file_explorer import FileExplorer
from src.ui.components.log_viewer import LogViewer
from src.ui.components.server_controls import ServerControls


LATEST_LOG_TAB = "latest.log"
ADDON_DIAGNOSTICS_TAB = "__addon_diagnostics__"
DASHBOARD_REFRESH_INTERVAL_SECONDS = 1.0
STARTUP_LOG_FOLLOW_SECONDS = 8.0
STOP_LOG_FOLLOW_SECONDS = 4.0
STARTUP_LOG_FOLLOW_INTERVAL_SECONDS = 0.15
ACTIVE_SERVER_STATES = {"starting", "running", "stopping"}
TRANSITION_SERVER_STATES = {"starting", "stopping"}
WORKSPACE_HEIGHT = 600
WORKSPACE_MIN_HEIGHT = 440
WORKSPACE_VERTICAL_CHROME = 205
WORKSPACE_HORIZONTAL_PADDING = 28
WORKSPACE_GAP_WIDTH = 10
WORKSPACE_PANEL_CHROME = 46
WORKSPACE_SIDE_FLEX = 25
WORKSPACE_LOG_FLEX = 55
WORKSPACE_CHAT_FLEX = 40
WORKSPACE_TOTAL_FLEX = WORKSPACE_SIDE_FLEX + WORKSPACE_LOG_FLEX + WORKSPACE_CHAT_FLEX
CONSOLE_DOCK_HEIGHT = 108
COMMAND_HISTORY_PANEL_GAP = 8
COMMAND_DOCK_VERTICAL_PADDING = 9
COMMAND_TITLE_ROW_HEIGHT = 30
COMMAND_DOCK_SPACING = 8
COMMAND_ENTRY_SLOT_HEIGHT = (
    CONSOLE_DOCK_HEIGHT
    - (COMMAND_DOCK_VERTICAL_PADDING * 2)
    - COMMAND_TITLE_ROW_HEIGHT
    - COMMAND_DOCK_SPACING
)
COMMAND_ENTRY_HEIGHT = 44
COMMAND_EXECUTE_BUTTON_WIDTH = 64
COMMAND_EXECUTE_BUTTON_HEIGHT = COMMAND_ENTRY_HEIGHT
LOG_TOOLBAR_SPACING = 6
LOG_SEARCH_ANCHOR_SIZE = 34
LOG_SEARCH_BUTTON_SIZE = 30
LOG_SEARCH_FIELD_WIDTH = 260
_JAVA_PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_BAN_DURATION_RE = re.compile(r"^[1-9][0-9]{0,3}(s|m|h|d|w|mo|y)$", re.IGNORECASE)
_TEMP_BAN_TIME_OPTIONS = (
    ("30m", "30 \u5206\u949f"),
    ("1h", "1 \u5c0f\u65f6"),
    ("12h", "12 \u5c0f\u65f6"),
    ("1d", "1 \u5929"),
    ("7d", "7 \u5929"),
    ("30d", "30 \u5929"),
)
PLAYER_LIST_FILTERS = {
    "online": "\u5728\u7ebf\u73a9\u5bb6",
    "all": "\u5168\u90e8\u73a9\u5bb6",
    "operators": "\u7ba1\u7406\u5458",
    "banned_players": "\u5c01\u7981\u73a9\u5bb6",
    "banned_ips": "\u5c01\u7981 IP",
}
PLAYER_DIRECTORY_FILTER_KEYS = {"all", "operators", "banned_players", "banned_ips"}
_PLAYER_DIRECTORY_COMMAND_PREFIXES = (
    "op ",
    "deop ",
    "ban ",
    "ban-ip ",
    "pardon ",
    "pardon-ip ",
    "tempban ",
    "tempbanip ",
    "tempipban ",
)


class OpsHomePage:
    def __init__(
        self,
        page: ft.Page,
        interfaces: DashboardInterfaces,
        settings: Settings,
    ):
        self.page = page
        self.interfaces = interfaces
        self.settings = settings
        self.chat_session_id = self.interfaces.chat.create_session("server_ops")

        self.metrics_row = ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
        self._metric_cards: dict[str, MetricCardController] = {}
        self._last_player_data = _empty_player_data()
        self._last_metrics_data = _loading_metrics()
        self._last_server_capabilities = self._load_server_capabilities()
        self._rendered_player_signature = None
        self.player_list = ft.ListView(expand=True, spacing=0, padding=0)
        self.player_updated = ft.Text("", size=12, color=theme.MUTED)
        self._player_list_filter = "online"
        self._last_player_directory_data: dict = _empty_player_directory()
        self.player_filter_button: ft.PopupMenuButton | None = None
        self.file_body = ft.Container(expand=True)
        self.tabs_row = ft.Row(spacing=4, scroll=ft.ScrollMode.AUTO)
        self.log_filter_container = ft.Container()
        self._workspace_row: ft.Row | None = None
        self._player_workspace_container: ft.Container | None = None
        self._log_workspace_container: ft.Container | None = None
        self._chat_workspace_container: ft.Container | None = None
        self._log_workbench_container: ft.Container | None = None
        self._status_polling = False
        self._dashboard_polling = False
        self._dashboard_poll_lock = threading.Lock()
        self._dashboard_refresh_lock = threading.Lock()
        self._dashboard_stop_event = threading.Event()
        self._startup_log_follow_generation = 0
        self._log_search_expanded = False
        self._log_search_ref = ft.Ref[ft.TextField]()
        self._log_search_overlay: ft.Container | None = None

        self.file_explorer = FileExplorer(
            interfaces.file,
            on_file_selected=self._on_file_tree_select,
        )
        self.log_viewer = LogViewer(interfaces.log, page, interfaces.server)
        self.log_viewer.set_on_ask_ai(self._on_log_ask_ai)
        self.chat_panel = ChatPanel(
            interfaces.chat,
            page,
            self.chat_session_id,
            config_interface=interfaces.config,
            command_interface=interfaces.command,
            on_command_audit_change=self._refresh_command_audits,
            on_command_execution_start=self._start_command_log_refresh,
            on_command_result=self._on_chat_command_result,
            on_server_start_requested=self._on_server_start_requested,
            on_config_proposal=self._on_config_proposal_created,
            on_session_change=self._on_chat_session_change,
        )
        self.server_controls = ServerControls(interfaces.server)
        self.server_controls.set_on_status_change(self._on_server_status_changed)
        self.server_controls.set_on_start_requested(self._on_server_start_requested)
        self.command_console = CommandConsole(
            interfaces.command,
            on_change=self._update_command_console_controls,
            on_result=self._on_console_command_result,
        )
        self.code_workbench = CodeWorkbench(
            file_interface=interfaces.file,
            config_interface=interfaces.config,
            version_interface=interfaces.version,
            page=page,
            on_file_changed=self._refresh_file_tree,
            on_config_apply=self._apply_config_proposal_from_workbench,
            on_config_reject=self._reject_config_proposal_from_workbench,
            on_config_feedback=self._complete_config_feedback,
        )
        self.addon_diagnostics_panel = AddonDiagnosticsPanel(interfaces.addon, page)

        self.active_tab = LATEST_LOG_TAB
        self.open_tabs: dict[str, dict] = {
            LATEST_LOG_TAB: {"label": "latest.log", "kind": "log"}
        }
        self.latest_log_relative = self._resolve_latest_log_relative()
        self.log_search = ft.TextField(
            ref=self._log_search_ref,
            dense=True,
            height=34,
            border_radius=0,
            focused_bgcolor="#00000000",
            bgcolor="#00000000",
            color=theme.TEXT,
            cursor_color=theme.BLUE,
            text_size=13,
            text_vertical_align=0,
            border=ft.InputBorder.NONE,
            border_color="#00000000",
            focused_border_color="#00000000",
            content_padding=ft.Padding.only(left=0, right=12, top=0, bottom=0),
            width=LOG_SEARCH_FIELD_WIDTH - LOG_SEARCH_ANCHOR_SIZE,
            on_change=self._on_log_search,
        )

    def build(self) -> ft.Control:
        self.refresh_all(update=False, start_polling=False, capture_metrics=False)

        return ft.Container(
            content=ft.Column(
                controls=[
                    self.build_header(),
                    self.metrics_row,
                    self.build_workspace(),
                ],
                spacing=10,
                expand=True,
            ),
            bgcolor=theme.BG,
            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            expand=True,
        )

    def build_header(self) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Icon(ft.Icons.DNS, size=18, color="#ffffff"),
                        width=34,
                        height=34,
                        bgcolor=theme.BLUE,
                        border_radius=8,
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                "MC 运维工作台",
                                size=18,
                                weight=ft.FontWeight.W_700,
                                color=theme.TEXT,
                            ),
                            ft.Text(
                                f"{self.settings.mc_server_dir.name} · {self.settings.mc_command_mode}",
                                size=12,
                                color=theme.MUTED,
                                max_lines=1,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    self.server_controls.build(),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=theme.PANEL,
            border=ft.Border.all(1, theme.LINE),
            border_radius=theme.RADIUS,
            padding=ft.Padding.symmetric(horizontal=12, vertical=9),
        )

    def build_workspace(self) -> ft.Control:
        workspace_height = self._workspace_height()
        self.chat_panel.set_available_width(
            self._chat_panel_available_width(),
            composer_width=self._chat_panel_composer_width(),
        )
        self._player_workspace_container = ft.Container(
            content=ft.Column(
                controls=[self._build_player_panel(), self._build_file_panel()],
                spacing=10,
                expand=True,
            ),
            expand=WORKSPACE_SIDE_FLEX,
            height=workspace_height,
        )
        self._log_workspace_container = ft.Container(
            content=self._build_log_workbench(workspace_height),
            expand=WORKSPACE_LOG_FLEX,
            height=workspace_height,
        )
        self._chat_workspace_container = ft.Container(
            content=self._build_chat_panel(),
            expand=WORKSPACE_CHAT_FLEX,
            height=workspace_height,
        )
        self._workspace_row = ft.Row(
            controls=[
                self._player_workspace_container,
                self._log_workspace_container,
                self._chat_workspace_container,
            ],
            spacing=WORKSPACE_GAP_WIDTH,
            expand=True,
        )
        return self._workspace_row

    def _build_player_panel(self) -> ft.Control:
        self.player_filter_button = ft.PopupMenuButton(
            icon=ft.Icons.FILTER_LIST,
            icon_color=theme.TEXT,
            tooltip="\u7b5b\u9009\u73a9\u5bb6",
            items=self._player_filter_menu_items(),
        )
        return panel(
            title="\u73a9\u5bb6\u5217\u8868",
            subtitle=self.player_updated,
            body=self.player_list,
            action=self.player_filter_button,
            min_height=216,
        )

    def _player_filter_menu_items(self) -> list[ft.PopupMenuItem]:
        items = [
            ft.PopupMenuItem(
                content=ft.Text(label, size=13, color=theme.TEXT),
                icon=_player_filter_icon(key),
                checked=key == getattr(self, "_player_list_filter", "online"),
                height=38,
                on_click=lambda _event, key=key: self._set_player_filter(key),
            )
            for key, label in PLAYER_LIST_FILTERS.items()
        ]
        items.append(_menu_separator("\u540d\u518c"))
        items.append(
            ft.PopupMenuItem(
                content=ft.Text("\u91cd\u8f7d\u540d\u518c\u7f13\u5b58", size=13, color=theme.TEXT),
                icon=ft.Icons.REFRESH,
                height=38,
                on_click=lambda _event: self._refresh_player_directory_cache(),
            )
        )
        return items

    def _set_player_filter(self, filter_key: str) -> None:
        if filter_key not in PLAYER_LIST_FILTERS:
            filter_key = "online"
        self._player_list_filter = filter_key
        if filter_key in PLAYER_DIRECTORY_FILTER_KEYS:
            self._load_player_directory()
        self._rendered_player_signature = None
        self._render_players(getattr(self, "_last_player_data", _empty_player_data()))
        self._sync_player_filter_button()
        self._update_controls(self.player_list, self.player_updated, self.player_filter_button)

    def _refresh_player_directory_cache(self) -> None:
        self._load_player_directory(force=True)
        if getattr(self, "_player_list_filter", "online") in PLAYER_DIRECTORY_FILTER_KEYS:
            self._rendered_player_signature = None
            self._render_players(getattr(self, "_last_player_data", _empty_player_data()))
        self._sync_player_filter_button()
        self._update_controls(self.player_list, self.player_updated, self.player_filter_button)

    def _load_player_directory(self, force: bool = False) -> None:
        player_interface = getattr(self.interfaces, "player", None)
        method_name = "refresh_player_directory" if force else "get_player_directory"
        get_directory = getattr(player_interface, method_name, None)
        try:
            data = get_directory() if callable(get_directory) else _empty_player_directory()
        except Exception:
            data = _empty_player_directory(error=True)
        self._last_player_directory_data = data

    def _sync_player_filter_button(self) -> None:
        button = getattr(self, "player_filter_button", None)
        if button is not None:
            button.items = self._player_filter_menu_items()

    def _build_file_panel(self) -> ft.Control:
        self.file_explorer.load_tree()
        addon_button = ft.IconButton(
            icon=ft.Icons.EXTENSION,
            icon_color=theme.TEXT,
            tooltip="打开组件诊断",
            on_click=lambda _: self._open_addon_diagnostics(),
        )
        config_button = ft.IconButton(
            icon=ft.Icons.SETTINGS,
            icon_color=theme.TEXT,
            tooltip="打开配置工作台",
            on_click=lambda _: self._open_config_workbench(),
        )
        return panel(
            title="MC \u670d\u52a1\u5668\u6587\u4ef6",
            subtitle=str(self.settings.mc_server_dir),
            body=self.file_explorer.control,
            action=ft.Row(
                controls=[addon_button, config_button],
                spacing=2,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_log_workbench(self, height: int | None = None) -> ft.Control:
        self.log_filter_container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.ARTICLE, size=16, color=theme.MUTED),
                                    ft.Text(
                                        "服务器日志",
                                        size=15,
                                        weight=ft.FontWeight.W_700,
                                        color=theme.TEXT,
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            tag("latest.log"),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.GestureDetector(
                        content=ft.Stack(
                            controls=[
                                ft.Row(
                                    controls=[
                                        self.log_viewer.build_filter_row(),
                                        self._build_log_search_control(),
                                        self.log_viewer.build_selection_row(),
                                    ],
                                    spacing=LOG_TOOLBAR_SPACING,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                self._log_search_overlay,
                            ],
                            height=34,
                            clip_behavior=ft.ClipBehavior.NONE,
                        ),
                        on_enter=lambda _: self.log_viewer.start_drag_scroll("up"),
                        on_exit=lambda _: self.log_viewer.stop_drag_scroll(),
                        on_tap_up=lambda _: self.log_viewer.end_drag_select(),
                        on_pan_end=lambda _: self.log_viewer.end_drag_select(),
                        on_pan_cancel=lambda _: self.log_viewer.end_drag_select(),
                        on_long_press_cancel=lambda _: self.log_viewer.end_drag_select(),
                        on_long_press_up=lambda _: self.log_viewer.end_drag_select(),
                        on_long_press_end=lambda _: self.log_viewer.end_drag_select(),
                    ),
                ],
                spacing=6,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
        )

        self._rebuild_tabs()
        self._update_file_body(update=False)
        self.command_console.history_panel.left = 12
        self.command_console.history_panel.bottom = CONSOLE_DOCK_HEIGHT + COMMAND_HISTORY_PANEL_GAP

        self._log_workbench_container = ft.Container(
            content=ft.Stack(
                controls=[
                    ft.Column(
                        controls=[
                            ft.Container(
                                content=self.tabs_row,
                                bgcolor=theme.TAB_BG,
                                padding=ft.Padding.only(left=8, right=8, top=6, bottom=0),
                                border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
                            ),
                            self.log_filter_container,
                            ft.Container(content=self.file_body, expand=True),
                            self._build_console_dock(),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    self.command_console.history_panel,
                ],
                expand=True,
                clip_behavior=ft.ClipBehavior.NONE,
            ),
            bgcolor=theme.PANEL,
            border=ft.Border.all(1, theme.LINE),
            border_radius=theme.RADIUS,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            height=height or self._workspace_height(),
        )
        return self._log_workbench_container

    def _build_log_search_control(self) -> ft.Control:
        search_button = self._build_log_search_icon_button()
        collapse_button = self._build_log_search_icon_button()
        self._log_search_overlay = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=collapse_button,
                        width=LOG_SEARCH_ANCHOR_SIZE,
                        height=LOG_SEARCH_ANCHOR_SIZE,
                        alignment=ft.Alignment(0, 0),
                    ),
                    self.log_search,
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            width=LOG_SEARCH_FIELD_WIDTH,
            height=LOG_SEARCH_ANCHOR_SIZE,
            bgcolor=theme.INPUT_BG,
            border=ft.Border.all(1, theme.LINE_STRONG),
            border_radius=7,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            visible=self._log_search_expanded,
            left=self.log_viewer.filter_row_width + LOG_TOOLBAR_SPACING,
            top=0,
            shadow=ft.BoxShadow(
                blur_radius=16,
                color="#00000055",
                offset=ft.Offset(0, 6),
            ),
        )
        return ft.Container(
            content=search_button,
            width=LOG_SEARCH_ANCHOR_SIZE,
            height=LOG_SEARCH_ANCHOR_SIZE,
            alignment=ft.Alignment(0, 0),
        )

    def _build_log_search_icon_button(self) -> ft.IconButton:
        return ft.IconButton(
            icon=ft.Icons.SEARCH,
            icon_color=theme.MUTED,
            icon_size=18,
            width=LOG_SEARCH_BUTTON_SIZE,
            height=LOG_SEARCH_BUTTON_SIZE,
            padding=0,
            on_click=lambda _: self._toggle_log_search(),
        )

    def _build_console_dock(self) -> ft.Control:
        dock = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.TERMINAL, size=16, color=theme.MUTED),
                                    ft.Text("控制台", size=13, weight=ft.FontWeight.W_700, color=theme.TEXT),
                                    self.command_console.history_button,
                                ],
                                spacing=4,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            tag(f"命令模式 {self.settings.mc_command_mode}"),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        height=COMMAND_TITLE_ROW_HEIGHT,
                    ),
                    self._build_command_entry_control(),
                ],
                spacing=COMMAND_DOCK_SPACING,
            ),
            bgcolor=theme.TAB_BG,
            padding=ft.Padding.symmetric(horizontal=12, vertical=COMMAND_DOCK_VERTICAL_PADDING),
            border=ft.Border.only(top=ft.BorderSide(1, theme.LINE)),
            height=CONSOLE_DOCK_HEIGHT,
        )
        return ft.GestureDetector(
            content=dock,
            on_enter=lambda _: self.log_viewer.start_drag_scroll("down"),
            on_exit=lambda _: self.log_viewer.stop_drag_scroll(),
            on_tap_up=lambda _: self.log_viewer.end_drag_select(),
            on_pan_end=lambda _: self.log_viewer.end_drag_select(),
            on_pan_cancel=lambda _: self.log_viewer.end_drag_select(),
            on_long_press_cancel=lambda _: self.log_viewer.end_drag_select(),
            on_long_press_up=lambda _: self.log_viewer.end_drag_select(),
            on_long_press_end=lambda _: self.log_viewer.end_drag_select(),
        )

    def _build_command_entry_control(self) -> ft.Container:
        self.command_console.command_input.height = COMMAND_ENTRY_HEIGHT
        return ft.Container(
            content=ft.Container(
                content=ft.Row(
                    controls=[
                        self.command_console.command_input,
                        self._build_command_execute_button(),
                    ],
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                height=COMMAND_ENTRY_HEIGHT,
                bgcolor=theme.INPUT_BG,
                border=ft.Border.all(1, theme.LINE),
                border_radius=7,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            ),
            height=COMMAND_ENTRY_SLOT_HEIGHT,
            alignment=ft.Alignment(0, 0),
        )

    def _build_command_execute_button(self) -> ft.Container:
        return ft.Container(
            content=ft.Icon(ft.Icons.PLAY_ARROW, color="#ffffff", size=20),
            bgcolor=theme.BLUE,
            tooltip="执行",
            on_click=lambda _: self._on_execute_command(),
            width=COMMAND_EXECUTE_BUTTON_WIDTH,
            height=COMMAND_EXECUTE_BUTTON_HEIGHT,
            alignment=ft.Alignment(0, 0),
            ink=True,
            border_radius=ft.BorderRadius(
                top_left=0,
                top_right=7,
                bottom_left=0,
                bottom_right=7,
            ),
        )

    def _build_chat_panel(self) -> ft.Control:
        return self.chat_panel.build()

    def apply_responsive_layout(self, update: bool = True) -> None:
        workspace_height = self._workspace_height()
        for control in (
            self._player_workspace_container,
            self._log_workspace_container,
            self._chat_workspace_container,
            self._log_workbench_container,
        ):
            if control is not None:
                control.height = workspace_height
        self.chat_panel.set_available_width(
            self._chat_panel_available_width(),
            composer_width=self._chat_panel_composer_width(),
        )
        if update:
            self._update_controls(self._workspace_row)

    def _workspace_height(self) -> int:
        page_height = self._page_dimension("height")
        if page_height is None:
            return WORKSPACE_HEIGHT
        return max(WORKSPACE_MIN_HEIGHT, int(page_height - WORKSPACE_VERTICAL_CHROME))

    def _chat_panel_available_width(self) -> int | None:
        panel_width = self._chat_panel_outer_width()
        if panel_width is None:
            return None
        return max(0, int(panel_width - WORKSPACE_PANEL_CHROME))

    def _chat_panel_composer_width(self) -> int | None:
        panel_width = self._chat_panel_outer_width()
        if panel_width is None:
            return None
        return max(
            0,
            int(panel_width - CHAT_FOOTER_LEFT_PADDING - CHAT_FOOTER_RIGHT_PADDING),
        )

    def _chat_panel_outer_width(self) -> float | None:
        page_width = self._page_dimension("width")
        if page_width is None:
            return None
        usable_width = max(
            0,
            page_width - WORKSPACE_HORIZONTAL_PADDING - (WORKSPACE_GAP_WIDTH * 2),
        )
        return usable_width * WORKSPACE_CHAT_FLEX / WORKSPACE_TOTAL_FLEX

    def _page_dimension(self, name: str) -> float | None:
        value = _positive_float(getattr(self.page, name, None))
        if value is not None:
            return value
        window = getattr(self.page, "window", None)
        if window is None:
            return None
        return _positive_float(getattr(window, name, None))

    def _on_chat_session_change(self, session_id: str) -> None:
        self.chat_session_id = session_id

    def _rebuild_tabs(self) -> None:
        self.tabs_row.controls = [
            self._make_file_tab(key, tab["label"])
            for key, tab in self.open_tabs.items()
        ]

    def _make_file_tab(self, key: str, label: str) -> ft.Container:
        active = key == self.active_tab
        controls: list[ft.Control] = [
            ft.Text(
                label,
                size=13,
                weight=ft.FontWeight.W_700 if active else ft.FontWeight.W_600,
                color=theme.TEXT if active else theme.MUTED,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        ]
        if key != LATEST_LOG_TAB:
            controls.append(
                ft.Container(
                    content=ft.Icon(ft.Icons.CLOSE, size=13, color=theme.MUTED),
                    width=18,
                    height=18,
                    border_radius=6,
                    alignment=ft.Alignment(0, 0),
                    on_click=lambda _, tab_key=key: self._close_file_tab(tab_key),
                )
            )

        return ft.Container(
            content=ft.Row(
                controls=controls,
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=theme.PANEL if active else theme.TAB_BG,
            border=ft.Border.all(1, theme.LINE),
            border_radius=7,
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            on_click=lambda _: self._on_file_tab(key),
        )

    def _on_file_tab(self, tab_key: str) -> None:
        if tab_key not in self.open_tabs:
            return
        self.active_tab = tab_key
        self._rebuild_tabs()
        self._update_file_body(update=False)
        if tab_key == LATEST_LOG_TAB:
            self.log_viewer.start_auto_refresh()
        else:
            self.log_viewer.stop_auto_refresh()
            tab = self.open_tabs.get(tab_key, {})
            if tab.get("kind") == "workbench":
                self.code_workbench.open_file(
                    tab_key,
                    tab.get("label", tab_key),
                    editable=tab.get("editable", False),
                    managed_config=tab.get("managed_config", False),
                    versioned=tab.get("versioned", False),
                )
        self._update_workspace_controls()
        if tab_key != LATEST_LOG_TAB and tab.get("kind") == "workbench":
            self.code_workbench.scroll_to_review_change()

    def _close_file_tab(self, tab_key: str) -> None:
        if tab_key == LATEST_LOG_TAB:
            return
        tab = self.open_tabs.get(tab_key, {})
        if tab.get("kind") == "workbench" and self.code_workbench.has_unsaved_changes(tab_key):
            self.page.update()
            return
        if tab.get("kind") == "workbench":
            self.code_workbench.close_file(tab_key)
        self.open_tabs.pop(tab_key, None)
        if self.active_tab == tab_key:
            self.active_tab = LATEST_LOG_TAB
            self.log_viewer.start_auto_refresh()
        self._rebuild_tabs()
        self._update_file_body(update=False)
        self._update_workspace_controls()

    def _on_file_tree_select(self, relative_path: str) -> None:
        if relative_path == self.latest_log_relative:
            self._on_file_tab(LATEST_LOG_TAB)
            return

        preview = self.interfaces.file.preview_file(relative_path)
        editable = preview.get("editable", False)
        is_config = _is_config_file(relative_path)
        is_versioned = _is_versioned_text_config(relative_path)
        file_name = preview.get("file_name", relative_path)
        if editable:
            self.open_tabs[relative_path] = {
                "label": file_name,
                "kind": "workbench",
                "preview": preview,
                "editable": editable,
                "managed_config": is_config,
                "versioned": is_versioned,
            }
            self.code_workbench.open_file(
                relative_path,
                file_name,
                editable=True,
                managed_config=is_config,
                versioned=is_versioned,
            )
        else:
            self.open_tabs[relative_path] = {
                "label": file_name,
                "kind": "file",
                "preview": preview,
                "editable": editable,
            }
        self.active_tab = relative_path
        self.log_viewer.stop_auto_refresh()
        self._rebuild_tabs()
        self._update_file_body(update=False)
        self._update_workspace_controls()
        if editable and is_config:
            self.code_workbench.scroll_to_review_change()

    def _open_config_workbench(self) -> None:
        self._on_file_tree_select("server.properties")

    def _open_addon_diagnostics(self) -> None:
        self.open_tabs[ADDON_DIAGNOSTICS_TAB] = {
            "label": "组件诊断",
            "kind": "addon_diagnostics",
        }
        self.active_tab = ADDON_DIAGNOSTICS_TAB
        self.log_viewer.stop_auto_refresh()
        self._rebuild_tabs()
        self._update_file_body(update=False)
        self._update_workspace_controls()

    def _on_config_proposal_created(self, proposal: dict) -> None:
        relative_path = proposal.get("relative_path", "server.properties")
        self._on_file_tree_select(relative_path)
        proposal_id = proposal.get("proposal_id")
        if proposal_id:
            self.code_workbench.review_proposal(proposal_id)

    def _apply_config_proposal_from_workbench(
        self,
        proposal_id: str,
        high_risk_confirmed: bool,
    ) -> dict:
        return self.interfaces.chat.execute_config_proposal_action(
            self.chat_session_id,
            proposal_id,
            high_risk_confirmed=high_risk_confirmed,
        )

    def _reject_config_proposal_from_workbench(self, proposal_id: str) -> dict:
        return self.interfaces.chat.record_rejected_config_proposal_action(
            self.chat_session_id,
            proposal_id,
        )

    def _complete_config_feedback(self, recorded_result: dict, action_kind: str) -> None:
        self.chat_panel.complete_config_feedback(recorded_result, action_kind)

    def _update_file_body(self, update: bool = True) -> None:
        tab = self.open_tabs.get(self.active_tab, self.open_tabs[LATEST_LOG_TAB])
        if tab["kind"] == "log":
            self.log_filter_container.visible = True
            self.file_body.content = ft.Container(
                content=self.log_viewer.control,
                padding=ft.Padding.all(11),
                expand=True,
            )
        elif tab.get("kind") == "addon_diagnostics":
            self.log_filter_container.visible = False
            self.file_body.content = ft.Container(
                content=self.addon_diagnostics_panel.build(),
                padding=ft.Padding.all(0),
                expand=True,
            )
        elif tab.get("kind") == "workbench":
            self.log_filter_container.visible = False
            self.file_body.content = ft.Container(
                content=self.code_workbench.build(),
                padding=ft.Padding.all(0),
                expand=True,
            )
        else:
            self.log_filter_container.visible = False
            self.file_body.content = self._build_preview(tab.get("preview", {}))

        if update:
            self._update_workspace_controls()

    def _build_preview(self, preview: dict) -> ft.Control:
        label = preview.get("file_name", "")
        size = preview.get("size_bytes", 0)
        truncated = preview.get("truncated", False)
        pill_text = f"已截断 · {size} bytes" if truncated else f"{size} bytes"
        content = preview.get("content", "")
        if not content:
            content = "该文件不是安全文本文件，当前仅展示元信息。"
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.INSERT_DRIVE_FILE, size=16, color=theme.MUTED),
                            ft.Text(
                                label,
                                size=14,
                                weight=ft.FontWeight.W_700,
                                color=theme.TEXT,
                                expand=True,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            status_pill(
                                pill_text,
                                color=theme.MUTED,
                                bgcolor=theme.PANEL_SOFT,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=ft.Text(
                            content,
                            selectable=True,
                            color="#c9d1d9",
                            font_family="Consolas",
                            size=12,
                        ),
                        bgcolor=theme.INPUT_BG,
                        border=ft.Border.all(1, theme.LINE),
                        border_radius=7,
                        padding=ft.Padding.all(10),
                        expand=True,
                    ),
                ],
                spacing=8,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=ft.Padding.all(11),
            expand=True,
        )

    def refresh_all(
        self,
        update: bool = True,
        start_polling: bool = True,
        capture_metrics: bool = True,
    ) -> None:
        players = self.interfaces.player.get_current_players()
        metrics = (
            self.interfaces.system.capture_metrics(server_pid=self._get_server_pid())
            if capture_metrics
            else _loading_metrics()
        )
        self._last_metrics_data = metrics
        logs: list[dict] = []

        self._render_metrics(players, metrics, logs)
        self._render_players(players)
        self.log_viewer.keyword = self.log_search.value or ""
        self.log_viewer.clear(prime_tail=True)
        self.command_console.refresh_audits()
        self.server_controls.refresh()
        self._refresh_file_tree()
        status = self.server_controls.get_status()
        if start_polling:
            self._start_dashboard_polling()
        if status.get("state") in ACTIVE_SERVER_STATES:
            self.log_viewer.start_auto_refresh()
            if status.get("state") in TRANSITION_SERVER_STATES:
                self._start_status_polling()
        self._update_file_body(update=False)

        if update:
            self.page.update()

    def start_background_refresh(self) -> None:
        self._refresh_dashboard_snapshot()
        self._start_dashboard_polling()

    def _get_filtered_logs(self) -> list[dict]:
        return self.log_viewer.visible_events(limit=100)

    def _get_server_pid(self) -> int | None:
        status = self.interfaces.server.get_server_status()
        return status.get("pid")

    def _on_server_status_changed(self, result: dict | None = None) -> None:
        status = result or self.interfaces.server.get_server_status()
        state = status.get("state", "stopped")
        self._refresh_file_tree()
        if state in ACTIVE_SERVER_STATES:
            self.log_viewer.restart_auto_refresh()
            self.log_viewer.refresh_live_once()
            if state in TRANSITION_SERVER_STATES:
                self._start_status_polling()
            self._start_dashboard_polling()
            self._refresh_dashboard_snapshot_soon()
        elif state in {"stopped", "crashed"}:
            self.log_viewer.refresh_live_once()
            self._start_stop_log_follow()
            self.log_viewer.stop_auto_refresh()
            self._render_players(_empty_player_data(state))
            self._refresh_dashboard_snapshot_soon()
        self._update_dashboard_controls(include_players=True, include_file_tree=True)

    def _on_server_start_requested(self) -> None:
        self.log_viewer.clear(prime_tail=True)
        self.log_viewer.restart_auto_refresh()
        self.log_viewer.refresh_live_once()
        self._start_startup_log_follow()
        self._update_controls(getattr(self.log_viewer, "control", None))

    def _start_startup_log_follow(self) -> None:
        self._start_log_follow(STARTUP_LOG_FOLLOW_SECONDS)

    def _start_stop_log_follow(self) -> None:
        self._start_log_follow(STOP_LOG_FOLLOW_SECONDS)

    def _start_log_follow(self, duration_seconds: float) -> None:
        self._startup_log_follow_generation += 1
        generation = self._startup_log_follow_generation
        if hasattr(self.page, "run_task"):
            try:
                self.page.run_task(self._startup_log_follow_loop_async, generation, duration_seconds)
                return
            except Exception:
                pass
        if hasattr(self.page, "run_thread"):
            try:
                self.page.run_thread(self._startup_log_follow_loop, generation, duration_seconds)
                return
            except Exception:
                pass
        threading.Thread(
            target=self._startup_log_follow_loop,
            args=(generation, duration_seconds),
            daemon=True,
        ).start()

    def _startup_log_follow_loop(self, generation: int, duration_seconds: float) -> None:
        deadline = time.monotonic() + duration_seconds
        while generation == self._startup_log_follow_generation and time.monotonic() < deadline:
            self.log_viewer.refresh_live_once()
            time.sleep(STARTUP_LOG_FOLLOW_INTERVAL_SECONDS)

    async def _startup_log_follow_loop_async(self, generation: int, duration_seconds: float) -> None:
        deadline = time.monotonic() + duration_seconds
        while generation == self._startup_log_follow_generation and time.monotonic() < deadline:
            self.log_viewer.refresh_live_once()
            await asyncio.sleep(STARTUP_LOG_FOLLOW_INTERVAL_SECONDS)

    def _start_status_polling(self) -> None:
        if self._status_polling:
            return
        self._status_polling = True
        threading.Thread(target=self._status_poll_loop, daemon=True).start()

    def _status_poll_loop(self) -> None:
        try:
            while self._status_polling:
                time.sleep(0.5)
                status = self.interfaces.server.get_server_status()
                self.server_controls.refresh()
                try:
                    self._update_controls(self._server_controls_control())
                except Exception:
                    pass
                state = status.get("state")
                if state not in TRANSITION_SERVER_STATES:
                    self._on_server_status_changed(status)
                    break
        finally:
            self._status_polling = False

    def _start_dashboard_polling(self) -> None:
        with self._dashboard_poll_lock:
            if self._dashboard_polling:
                return
            self._dashboard_polling = True
            self._dashboard_stop_event.clear()

        if hasattr(self.page, "run_thread"):
            try:
                self.page.run_thread(self._dashboard_poll_loop)
                return
            except Exception:
                pass

        if hasattr(self.page, "run_task"):
            try:
                self.page.run_task(self._dashboard_poll_loop_async)
                return
            except Exception:
                pass

        threading.Thread(target=self._dashboard_poll_loop, daemon=True).start()

    def _refresh_dashboard_snapshot_soon(self) -> None:
        if hasattr(self.page, "run_thread"):
            try:
                self.page.run_thread(self._refresh_dashboard_snapshot)
                return
            except Exception:
                pass
        threading.Thread(target=self._refresh_dashboard_snapshot, daemon=True).start()

    def _stop_dashboard_polling(self) -> None:
        with self._dashboard_poll_lock:
            self._dashboard_polling = False
            self._dashboard_stop_event.set()

    def shutdown(self) -> None:
        self._stop_dashboard_polling()
        self._status_polling = False
        self._startup_log_follow_generation += 1
        try:
            self.log_viewer.stop_auto_refresh()
        except Exception:
            pass

    def _dashboard_poll_loop(self) -> None:
        try:
            while self._dashboard_polling:
                if not self._refresh_dashboard_snapshot():
                    break
                if self._dashboard_stop_event.wait(DASHBOARD_REFRESH_INTERVAL_SECONDS):
                    break
        finally:
            with self._dashboard_poll_lock:
                self._dashboard_polling = False
                self._dashboard_stop_event.set()

    async def _dashboard_poll_loop_async(self) -> None:
        try:
            while self._dashboard_polling:
                if not self._refresh_dashboard_snapshot():
                    break
                if await self._wait_dashboard_interval(DASHBOARD_REFRESH_INTERVAL_SECONDS):
                    break
        finally:
            with self._dashboard_poll_lock:
                self._dashboard_polling = False
                self._dashboard_stop_event.set()

    async def _wait_dashboard_interval(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while not self._dashboard_stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.1, remaining))
        return True

    def _refresh_dashboard_snapshot(self) -> bool:
        with self._dashboard_refresh_lock:
            return self._refresh_dashboard_snapshot_locked()

    def _refresh_dashboard_snapshot_locked(self) -> bool:
        try:
            status = self.interfaces.server.get_server_status()
            state = status.get("state", "stopped")
            metrics = self.interfaces.system.capture_metrics(server_pid=status.get("pid"))
            self._last_metrics_data = metrics
            logs = self._get_filtered_logs()
            cached_players = self._cached_player_data(state)
            self.server_controls.refresh()
            self._refresh_file_tree()
            self._render_metrics(cached_players, metrics, logs)
            self._update_dashboard_controls(include_players=False, include_file_tree=True)

            players = (
                self.interfaces.player.get_current_players()
                if state in ACTIVE_SERVER_STATES
                else _empty_player_data(state)
            )
            self._render_players(players)
            self._render_metrics(players, metrics, logs)
            self._update_dashboard_controls(include_players=True, include_file_tree=False)
            if state in {"stopped", "crashed"}:
                self.log_viewer.refresh_live_once()
                self.log_viewer.stop_auto_refresh()
            return self._dashboard_polling or state in ACTIVE_SERVER_STATES
        except Exception:
            return self._dashboard_polling

    def _refresh_file_tree(self) -> None:
        file_explorer = getattr(self, "file_explorer", None)
        if file_explorer is None:
            return
        try:
            file_explorer.refresh_tree()
        except Exception:
            pass

    def _cached_player_data(self, server_state: str) -> dict:
        if server_state not in ACTIVE_SERVER_STATES:
            return _empty_player_data(server_state)
        cached = getattr(self, "_last_player_data", None)
        if not cached:
            return _empty_player_data(server_state)
        return {**cached, "server_state": server_state}

    def _render_metrics(self, players: dict, metrics: dict, logs: list[dict]) -> None:
        if not hasattr(self, "_metric_cards"):
            self._metric_cards = {}
        online_count = players["online_count"]
        max_players = players["max_players"]
        loading = bool(metrics.get("loading"))
        cpu = float(metrics.get("cpu_percent") or 0)
        memory = float(metrics.get("memory_percent") or 0)
        server_cpu = float(metrics.get("server_cpu_percent") or 0)
        server_memory = float(metrics.get("server_memory_percent") or 0)
        warn_count = sum(1 for item in logs if item.get("level") == "WARN")
        error_count = sum(1 for item in logs if item.get("level") == "ERROR")
        event_label = f"{error_count} ERROR" if error_count else f"{warn_count} WARN"
        event_color = theme.RED if error_count else theme.AMBER
        cpu_value = "--" if loading else _format_percent_value(cpu)
        memory_value = "--" if loading else _format_percent_value(memory)
        cpu_hint = "\u91c7\u6837\u4e2d" if loading else "\u504f\u9ad8" if cpu >= 75 else "\u6b63\u5e38"

        specs = [
            (
                "players",
                {
                    "title": "\u5728\u7ebf\u73a9\u5bb6",
                    "value": str(online_count),
                    "unit": "\u4eba",
                    "hint": tag(f"{online_count} / {max_players}"),
                    "progress": online_count / max_players if max_players else 0,
                    "color": theme.GREEN,
                },
            ),
            (
                "cpu",
                {
                    "title": "CPU",
                    "value": cpu_value,
                    "unit": "%",
                    "hint": ft.Text(cpu_hint, size=12, color=theme.MUTED),
                    "progress": 0 if loading else cpu / 100,
                    "color": theme.RED if cpu >= 75 else theme.AMBER if cpu >= 60 else theme.BLUE,
                    "highlight_progress": 0 if loading else server_cpu / 100,
                    "highlight_label": (
                        "\u670d\u52a1\u7aef\u8fdb\u7a0b \u7b49\u5f85\u91c7\u6837"
                        if loading
                        else f"\u670d\u52a1\u7aef\u8fdb\u7a0b {_format_percent_value(server_cpu)}%"
                    ),
                    "highlight_color": theme.GREEN,
                    "highlight_tooltip": (
                        "\u4e3b\u8fdb\u5ea6\u6761\u4e3a\u6574\u673a CPU\uff1b"
                        "\u4eae\u8272\u90e8\u5206\u4e3a Minecraft \u670d\u52a1\u7aef\u8fdb\u7a0b\u6811 CPU\u3002"
                    ),
                },
            ),
            (
                "memory",
                {
                    "title": "\u5185\u5b58",
                    "value": memory_value,
                    "unit": "%",
                    "hint": ft.Text(_memory_hint(metrics), size=12, color=theme.MUTED),
                    "progress": 0 if loading else memory / 100,
                    "color": theme.RED if memory >= 80 else theme.AMBER if memory >= 65 else theme.VIOLET,
                    "highlight_progress": 0 if loading else server_memory / 100,
                    "highlight_label": _server_memory_hint(metrics),
                    "highlight_color": theme.GREEN,
                    "highlight_tooltip": (
                        "\u4e3b\u8fdb\u5ea6\u6761\u4e3a\u6574\u673a\u5185\u5b58\uff1b"
                        "\u4eae\u8272\u90e8\u5206\u4e3a Minecraft \u670d\u52a1\u7aef\u8fdb\u7a0b\u6811\u5185\u5b58\u3002"
                    ),
                },
            ),
            (
                "events",
                {
                    "title": "\u6700\u8fd1\u4e8b\u4ef6",
                    "value": str(len(logs)),
                    "unit": "\u6761",
                    "hint": status_pill(event_label, color=event_color, bgcolor=theme.AMBER_SOFT),
                    "progress": min(len(logs) * 4 / 100, 1),
                    "color": event_color,
                },
            ),
        ]
        if set(self._metric_cards) != {key for key, _ in specs}:
            self._metric_cards = {}
            self.metrics_row.controls = []
            for key, args in specs:
                card = MetricCardController(**args)
                self._metric_cards[key] = card
                self.metrics_row.controls.append(ft.Container(content=card.control, expand=1))
            return

        for key, args in specs:
            self._metric_cards[key].update(**args)

    def _load_server_capabilities(self) -> dict:
        capability_interface = getattr(self.interfaces, "capability", None)
        get_capabilities = getattr(capability_interface, "get_server_capabilities", None)
        if callable(get_capabilities):
            try:
                return get_capabilities()
            except Exception:
                return _empty_server_capabilities()
        return _empty_server_capabilities()

    def _render_players(self, data: dict) -> None:
        self._last_player_data = data
        capabilities = getattr(
            self,
            "_last_server_capabilities",
            _empty_server_capabilities(),
        )
        current_filter = getattr(self, "_player_list_filter", "online")
        if current_filter not in PLAYER_LIST_FILTERS:
            current_filter = "online"
            self._player_list_filter = current_filter
        if current_filter in PLAYER_DIRECTORY_FILTER_KEYS:
            self._load_player_directory()
        directory_data = getattr(self, "_last_player_directory_data", _empty_player_directory())
        signature = _player_render_signature(
            data,
            capabilities,
            current_filter,
            directory_data,
        )
        if signature != getattr(self, "_rendered_player_signature", None):
            self._rendered_player_signature = signature
            if current_filter == "online":
                self.player_list.controls = self._online_player_controls(data, capabilities)
            else:
                self.player_list.controls = self._directory_player_controls(
                    directory_data,
                    current_filter,
                    capabilities,
                )
        self.player_updated.value = (
            f"{PLAYER_LIST_FILTERS[current_filter]} \u00b7 \u6700\u540e\u5237\u65b0 "
            + _display_time()
        )
        self._sync_player_filter_button()

    def _online_player_controls(self, data: dict, capabilities: dict) -> list[ft.Control]:
        controls = []
        for player in data["players"]:
            menu_items = self._player_context_menu_items(player, capabilities)
            controls.append(
                _player_row(
                    player,
                    menu_items,
                    lambda event, items=menu_items: self._on_player_context_menu_select(
                        event,
                        items,
                    ),
                )
            )
        if controls:
            return controls
        state = data.get("server_state", "stopped")
        if state not in ("running", "starting"):
            return [_empty_state("\u670d\u52a1\u5668\u672a\u8fd0\u884c")]
        return [_empty_state("\u5f53\u524d\u6ca1\u6709\u5728\u7ebf\u73a9\u5bb6")]

    def _directory_player_controls(
        self,
        data: dict,
        current_filter: str,
        capabilities: dict,
    ) -> list[ft.Control]:
        if data.get("error"):
            return [_empty_state("\u540d\u518c\u8bfb\u53d6\u5931\u8d25")]
        if current_filter == "banned_ips":
            banned_ips = list(data.get("banned_ips", []))
            if not banned_ips:
                return [_empty_state("\u6ca1\u6709\u88ab\u5c01\u7981\u7684 IP")]
            return [_banned_ip_directory_row(entry) for entry in banned_ips]

        players = list(data.get("players", []))
        if current_filter == "operators":
            players = [player for player in players if player.get("is_operator")]
            empty_text = "\u6ca1\u6709\u7ba1\u7406\u5458"
        elif current_filter == "banned_players":
            players = [player for player in players if player.get("is_banned")]
            empty_text = "\u6ca1\u6709\u88ab\u5c01\u7981\u7684\u73a9\u5bb6"
        else:
            empty_text = "\u8fd8\u6ca1\u6709\u8bb0\u5f55\u5230\u73a9\u5bb6"
        controls = []
        for player in players:
            menu_items = self._player_context_menu_items(player, capabilities)
            controls.append(
                _player_row(
                    player,
                    menu_items,
                    lambda event, items=menu_items: self._on_player_context_menu_select(
                        event,
                        items,
                    ),
                    detail_text=_player_directory_detail(player),
                    status_control=_directory_player_status(player),
                )
            )
        return controls if controls else [_empty_state(empty_text)]

    def _refresh_players(self) -> None:
        self._last_server_capabilities = self._load_server_capabilities()
        players = self.interfaces.player.get_current_players()
        metrics = self.interfaces.system.capture_metrics(server_pid=self._get_server_pid())
        self._last_metrics_data = metrics
        if getattr(self, "_player_list_filter", "online") in PLAYER_DIRECTORY_FILTER_KEYS:
            self._load_player_directory(force=True)
        self._render_players(players)
        self._render_metrics(players, metrics, self._get_filtered_logs())
        self._update_dashboard_controls(include_players=True, include_file_tree=False)

    def _player_context_menu_items(
        self,
        player: dict,
        capabilities: dict,
    ) -> list[ft.PopupMenuItem]:
        command_interface = getattr(self.interfaces, "command", None)
        if command_interface is None:
            return []
        player_name = _safe_player_name(player)
        if player_name is None:
            return []

        items = [_operator_menu_item(player, player_name, self)]
        ban_item = (
            _menu_item(
                "\u89e3\u9664\u5c01\u7981",
                ft.Icons.REMOVE_CIRCLE_OUTLINE,
                lambda _event, name=player_name: self._execute_player_command(
                    f"pardon {name}",
                ),
            )
            if player.get("is_banned")
            else _menu_item(
                "\u6c38\u4e45\u5c01\u7981",
                ft.Icons.BLOCK,
                lambda _event, name=player_name: self._execute_player_command(
                    f"ban {name}",
                ),
                color=theme.RED,
            )
        )
        items.extend(
            [
                _menu_separator("\u5c01\u7981"),
                ban_item,
            ]
        )

        if capabilities.get("supports_temp_ban"):
            command_name = capabilities.get("temp_ban_command") or "tempban"
            items.extend(
                self._temporary_ban_menu_items(
                    player_name,
                    command_name,
                    "\u4e34\u65f6\u5c01\u7981",
                    ft.Icons.TIMER,
                )
            )

        items.extend(
            [
                _menu_separator("\u5c01\u7981 IP"),
                _menu_item(
                    "\u6c38\u4e45\u5c01\u7981 IP",
                    ft.Icons.LANGUAGE,
                    lambda _event, name=player_name: self._execute_player_command(
                        f"ban-ip {name}",
                    ),
                    color=theme.RED,
                ),
            ]
        )
        if capabilities.get("supports_temp_ip_ban"):
            command_name = capabilities.get("temp_ip_ban_command") or "tempbanip"
            items.extend(
                self._temporary_ban_menu_items(
                    player_name,
                    command_name,
                    "\u4e34\u65f6\u5c01\u7981 IP",
                    ft.Icons.SCHEDULE,
                )
            )

        items.extend(
            [
                _menu_separator("\u73a9\u5bb6 + IP"),
                _menu_item(
                    "\u6c38\u4e45\u5c01\u7981\u73a9\u5bb6\u548c IP",
                    ft.Icons.SECURITY,
                    lambda _event, name=player_name: self._execute_player_commands(
                        [f"ban {name}", f"ban-ip {name}"],
                    ),
                    color=theme.RED,
                ),
            ]
        )
        if capabilities.get("supports_temp_ban") and capabilities.get("supports_temp_ip_ban"):
            ban_command = capabilities.get("temp_ban_command") or "tempban"
            ip_ban_command = capabilities.get("temp_ip_ban_command") or "tempbanip"
            items.extend(
                self._temporary_combined_ban_menu_items(
                    player_name,
                    ban_command,
                    ip_ban_command,
                )
            )
        return items

    def _temporary_ban_menu_items(
        self,
        player_name: str,
        command_name: str,
        label_prefix: str,
        icon: ft.IconData,
    ) -> list[ft.PopupMenuItem]:
        items = []
        for duration, label in _TEMP_BAN_TIME_OPTIONS:
            items.append(
                _menu_item(
                    f"{label_prefix} \u00b7 {label}",
                    icon,
                    lambda _event, duration=duration: self._execute_player_command(
                        _temporary_ban_command(command_name, player_name, duration),
                    ),
                )
            )
        items.append(
            _menu_item(
                f"{label_prefix} \u00b7 \u81ea\u5b9a\u4e49\u65f6\u95f4...",
                ft.Icons.EDIT_CALENDAR,
                lambda _event: self._show_custom_ban_duration_dialog(
                    player_name,
                    command_name,
                    label_prefix,
                ),
            )
        )
        return items

    def _temporary_combined_ban_menu_items(
        self,
        player_name: str,
        ban_command_name: str,
        ip_ban_command_name: str,
    ) -> list[ft.PopupMenuItem]:
        label_prefix = "\u4e34\u65f6\u5c01\u7981\u73a9\u5bb6\u548c IP"
        items = []
        for duration, label in _TEMP_BAN_TIME_OPTIONS:
            items.append(
                _menu_item(
                    f"{label_prefix} \u00b7 {label}",
                    ft.Icons.SECURITY,
                    lambda _event, duration=duration: self._execute_player_commands(
                        _temporary_combined_ban_commands(
                            ban_command_name,
                            ip_ban_command_name,
                            player_name,
                            duration,
                        ),
                    ),
                    color=theme.RED,
                )
            )
        items.append(
            _menu_item(
                f"{label_prefix} \u00b7 \u81ea\u5b9a\u4e49\u65f6\u95f4...",
                ft.Icons.EDIT_CALENDAR,
                lambda _event: self._show_custom_combined_ban_duration_dialog(
                    player_name,
                    ban_command_name,
                    ip_ban_command_name,
                ),
                color=theme.RED,
            )
        )
        return items

    def _show_custom_ban_duration_dialog(
        self,
        player_name: str,
        command_name: str,
        label_prefix: str,
    ) -> None:
        duration_input = text_field(hint_text="\u4f8b\u5982 30m\u30011h\u30017d\u300130d")
        error_text = ft.Text("", size=12, color=theme.RED)
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(label_prefix, color=theme.TEXT, size=16, weight=ft.FontWeight.W_700),
            content=ft.Column(
                controls=[
                    ft.Text(
                        "\u8f93\u5165\u5c01\u7981\u65f6\u957f\uff0c\u652f\u6301 s/m/h/d/w/mo/y\u3002",
                        size=12,
                        color=theme.MUTED,
                    ),
                    duration_input,
                    error_text,
                ],
                spacing=8,
                tight=True,
            ),
            actions=[],
            bgcolor=theme.PANEL,
        )

        def close(_event=None) -> None:
            self._close_dialog(dialog)

        def submit(_event=None) -> None:
            duration = _normalize_ban_duration(duration_input.value or "")
            if duration is None:
                error_text.value = "\u8bf7\u8f93\u5165\u5982 30m\u30011h\u30017d \u8fd9\u6837\u7684\u65f6\u957f\u3002"
                self._update_page()
                return
            self._close_dialog(dialog)
            self._execute_player_command(
                _temporary_ban_command(command_name, player_name, duration),
            )

        duration_input.on_submit = submit
        dialog.actions = [
            ft.TextButton("\u53d6\u6d88", on_click=close),
            ft.TextButton("\u7ee7\u7eed", icon=ft.Icons.CHEVRON_RIGHT, on_click=submit),
        ]
        self._show_dialog(dialog)

    def _show_custom_combined_ban_duration_dialog(
        self,
        player_name: str,
        ban_command_name: str,
        ip_ban_command_name: str,
    ) -> None:
        label_prefix = "\u4e34\u65f6\u5c01\u7981\u73a9\u5bb6\u548c IP"
        duration_input = text_field(hint_text="\u4f8b\u5982 30m\u30011h\u30017d\u300130d")
        error_text = ft.Text("", size=12, color=theme.RED)
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(label_prefix, color=theme.TEXT, size=16, weight=ft.FontWeight.W_700),
            content=ft.Column(
                controls=[
                    ft.Text(
                        "\u8f93\u5165\u5c01\u7981\u65f6\u957f\uff0c\u5c06\u540c\u65f6\u53d1\u9001\u73a9\u5bb6\u548c IP \u4e24\u6761\u5c01\u7981\u547d\u4ee4\u3002",
                        size=12,
                        color=theme.MUTED,
                    ),
                    duration_input,
                    error_text,
                ],
                spacing=8,
                tight=True,
            ),
            actions=[],
            bgcolor=theme.PANEL,
        )

        def close(_event=None) -> None:
            self._close_dialog(dialog)

        def submit(_event=None) -> None:
            duration = _normalize_ban_duration(duration_input.value or "")
            if duration is None:
                error_text.value = "\u8bf7\u8f93\u5165\u5982 30m\u30011h\u30017d \u8fd9\u6837\u7684\u65f6\u957f\u3002"
                self._update_page()
                return
            self._close_dialog(dialog)
            self._execute_player_commands(
                _temporary_combined_ban_commands(
                    ban_command_name,
                    ip_ban_command_name,
                    player_name,
                    duration,
                ),
            )

        duration_input.on_submit = submit
        dialog.actions = [
            ft.TextButton("\u53d6\u6d88", on_click=close),
            ft.TextButton("\u7ee7\u7eed", icon=ft.Icons.CHEVRON_RIGHT, on_click=submit),
        ]
        self._show_dialog(dialog)

    def _execute_player_command(self, command: str) -> None:
        self._execute_player_commands([command])

    def _execute_player_commands(self, commands: list[str]) -> None:
        self._handle_player_command_results(self._submit_player_commands(commands))

    def _submit_player_commands(self, commands: list[str]) -> list[dict]:
        results = []
        for command in commands:
            results.append(
                self.interfaces.command.submit_command(
                    command=command,
                    requested_by="player_menu",
                    user_confirmed=True,
                )
            )
        return results

    def _handle_player_command_result(self, result: dict) -> None:
        self._handle_player_command_results([result])

    def _handle_player_command_results(self, results: list[dict]) -> None:
        if not results:
            return
        result = _summarize_command_results(results)
        if hasattr(self, "command_console"):
            self.command_console.refresh_audits()
        self._start_command_log_refresh()
        self._refresh_player_views_after_command_results(results)
        if any(item.get("status") == "file_updated" for item in results):
            self._refresh_file_tree()
        if result.get("status") in {"file_updated", "no_change"}:
            self._show_snackbar(_command_feedback_message(result), False)
        elif result.get("status") != "executed":
            message = _command_feedback_message(result)
            self._show_snackbar(message, result.get("status") in {"failed", "blocked"})

    def _on_player_context_menu_select(
        self,
        event,
        menu_items: list[ft.PopupMenuItem] | None = None,
    ) -> None:
        item = getattr(event, "item", None)
        handler = getattr(item, "data", None)
        if not callable(handler) and menu_items is not None:
            try:
                index = int(getattr(event, "item_index", -1))
            except (TypeError, ValueError):
                index = -1
            if 0 <= index < len(menu_items):
                handler = getattr(menu_items[index], "data", None)
        if callable(handler):
            handler(event)

    def _show_dialog(self, dialog: ft.DialogControl) -> None:
        show_dialog = getattr(self.page, "show_dialog", None)
        if callable(show_dialog):
            show_dialog(dialog)
            return
        dialog.open = True
        self._update_page()

    def _close_dialog(self, dialog: ft.DialogControl) -> None:
        pop_dialog = getattr(self.page, "pop_dialog", None)
        if callable(pop_dialog):
            pop_dialog()
            return
        dialog.open = False
        self._update_page()

    def _show_snackbar(self, message: str, is_error: bool = False) -> None:
        snackbar = ft.SnackBar(
            ft.Text(message, color=theme.TEXT),
            bgcolor=theme.RED_SOFT if is_error else theme.PANEL_RAISED,
            show_close_icon=True,
        )
        show_dialog = getattr(self.page, "show_dialog", None)
        if callable(show_dialog):
            show_dialog(snackbar)

    def _update_page(self) -> None:
        try:
            self.page.update()
        except Exception:
            pass

    def _update_controls(self, *controls: ft.Control | None) -> None:
        targets = [control for control in controls if control is not None]
        if not targets:
            return
        try:
            self.page.update(*targets)
        except TypeError:
            try:
                self.page.update()
            except Exception:
                pass
        except Exception:
            pass

    def _server_controls_control(self) -> ft.Control | None:
        return getattr(self.server_controls, "control", None)

    def _file_explorer_control(self) -> ft.Control | None:
        return getattr(self.file_explorer, "control", None)

    def _update_dashboard_controls(
        self,
        include_players: bool = True,
        include_file_tree: bool = False,
    ) -> None:
        controls: list[ft.Control | None] = [
            self.metrics_row,
            self._server_controls_control(),
        ]
        if include_players:
            controls.extend([self.player_list, self.player_updated])
        if include_file_tree:
            controls.append(self._file_explorer_control())
        self._update_controls(*controls)

    def _update_workspace_controls(self) -> None:
        self._update_controls(self.tabs_row, self.log_filter_container, self.file_body)

    def _update_command_console_controls(self) -> None:
        self._update_controls(
            self.command_console.history_panel,
            self.command_console.history_button,
        )

    def _update_log_search_control(self) -> None:
        self._update_controls(self._log_search_overlay)

    def _on_log_search(self, _event) -> None:
        self.log_viewer.keyword = self.log_search.value or ""
        self.log_viewer.refresh()
        self._render_metrics(
            self._last_player_data,
            getattr(self, "_last_metrics_data", _loading_metrics()),
            self._get_filtered_logs(),
        )
        self.log_viewer._update_live_log_control()
        self._update_controls(self.metrics_row)

    def _toggle_log_search(self) -> None:
        if self._log_search_expanded:
            self._collapse_log_search()
            return
        self._expand_log_search()

    def _expand_log_search(self) -> None:
        self._log_search_expanded = True
        if self._log_search_overlay is not None:
            self._log_search_overlay.visible = True
        self._update_log_search_control()
        focus = getattr(self._log_search_ref.current, "focus", None)
        run_task = getattr(self.page, "run_task", None)
        if callable(focus) and callable(run_task):
            try:
                run_task(focus)
            except Exception:
                pass

    def _collapse_log_search(self) -> None:
        self._log_search_expanded = False
        if self._log_search_overlay is not None:
            self._log_search_overlay.visible = False
        self._update_log_search_control()

    def _on_execute_command(self) -> None:
        self.command_console.execute()

    def _on_console_command_result(self, result: dict) -> None:
        self._start_command_log_refresh()
        self._refresh_player_views_after_command_results([result])
        if result.get("status") != "executed":
            message = _command_feedback_message(result)
            self._show_snackbar(message, result.get("status") in {"failed", "blocked"})

    def _on_chat_command_result(self, result: dict) -> None:
        self._refresh_player_views_after_command_results([result])

    def _refresh_player_views_after_command_results(self, results: list[dict]) -> None:
        if not results:
            return
        refresh_players = any(_should_refresh_players_after_command(item) for item in results)
        if not refresh_players:
            return
        refresh_directory = any(
            _should_refresh_player_directory_after_command(item)
            for item in results
        )
        if (
            refresh_directory
            and getattr(self, "_player_list_filter", "online") not in PLAYER_DIRECTORY_FILTER_KEYS
        ):
            self._load_player_directory(force=True)
            self._rendered_player_signature = None
        self._refresh_players()

    def _refresh_command_audits(self) -> None:
        self.command_console.refresh_audits()

    def _start_command_log_refresh(self) -> None:
        self.log_viewer.start_auto_refresh()

    def _on_log_ask_ai(self) -> None:
        selection = self.log_viewer.get_selected_raw_logs()
        if not selection.get("line_count"):
            return
        result = self.interfaces.chat.attach_log_selection(
            self.chat_session_id, selection
        )
        self.chat_panel.add_attachment(result)
        self.log_viewer.clear_selection()

    def _resolve_latest_log_relative(self) -> str:
        try:
            return self.settings.mc_log_path.resolve(strict=False).relative_to(
                self.settings.mc_server_dir.resolve(strict=False)
            ).as_posix()
        except ValueError:
            return "logs/latest.log"


def _blue_btn_style() -> ft.ButtonStyle:
    return ft.ButtonStyle(
        bgcolor=theme.BLUE,
        color=theme.TEXT,
        shape=ft.RoundedRectangleBorder(radius=7),
        side=ft.BorderSide(1, theme.BLUE),
        padding=ft.Padding.symmetric(horizontal=10, vertical=7),
    )


def _player_row(
    player: dict,
    context_menu_items: list[ft.PopupMenuItem] | None = None,
    on_context_menu_select=None,
    detail_text: str | None = None,
    status_control: ft.Control | None = None,
) -> ft.Control:
    detail = detail_text
    if detail is None:
        detail = str(player.get("uuid") or "")[:8] or "UUID \u67e5\u8be2\u4e2d"
    status = status_control or status_pill("\u5728\u7ebf")
    row = ft.Container(
        content=ft.Row(
            controls=[
                _player_avatar(player),
                ft.Column(
                    controls=[
                        ft.Text(
                            player["name"],
                            size=13,
                            weight=ft.FontWeight.W_700,
                            color=theme.TEXT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            detail,
                            size=12,
                            color=theme.MUTED,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                status,
            ],
            spacing=9,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(vertical=7),
        border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
    )
    if not context_menu_items:
        return row
    return ft.ContextMenu(
        content=row,
        secondary_items=context_menu_items,
        tertiary_trigger=None,
        on_select=on_context_menu_select,
    )


def _menu_item(
    label: str,
    icon: ft.IconData,
    on_click,
    color: str = theme.TEXT,
) -> ft.PopupMenuItem:
    return ft.PopupMenuItem(
        content=ft.Text(label, size=13, color=color),
        icon=icon,
        height=38,
        data=on_click,
    )


def _operator_menu_item(player: dict, player_name: str, home) -> ft.PopupMenuItem:
    if player.get("is_operator") is True:
        return _menu_item(
            "\u53d6\u6d88\u7ba1\u7406\u5458",
            ft.Icons.PERSON_REMOVE,
            lambda _event, name=player_name: home._execute_player_command(
                f"deop {name}",
            ),
        )
    return _menu_item(
        "\u8bbe\u4e3a\u7ba1\u7406\u5458",
        ft.Icons.ADMIN_PANEL_SETTINGS,
        lambda _event, name=player_name: home._execute_player_command(
            f"op {name}",
        ),
    )


def _menu_separator(label: str) -> ft.PopupMenuItem:
    return ft.PopupMenuItem(
        content=ft.Text(label, size=11, color=theme.MUTED, weight=ft.FontWeight.W_700),
        height=28,
        disabled=True,
    )


def _safe_player_name(player: dict) -> str | None:
    name = str(player.get("name") or "").strip()
    if _JAVA_PLAYER_NAME_RE.fullmatch(name):
        return name
    return None


def _normalize_ban_duration(value: str) -> str | None:
    normalized = value.strip().lower()
    if not _BAN_DURATION_RE.fullmatch(normalized):
        return None
    return normalized


def _temporary_ban_command(command_name: str, player_name: str, duration: str) -> str:
    return f"{command_name} {player_name} {duration}"


def _temporary_combined_ban_commands(
    ban_command_name: str,
    ip_ban_command_name: str,
    player_name: str,
    duration: str,
) -> list[str]:
    return [
        _temporary_ban_command(ban_command_name, player_name, duration),
        _temporary_ban_command(ip_ban_command_name, player_name, duration),
    ]


def _should_refresh_players_after_command(result: dict) -> bool:
    if not _is_successful_command_result(result):
        return False
    normalized = _result_command(result)
    return normalized.startswith(
        _PLAYER_DIRECTORY_COMMAND_PREFIXES
        + (
            "kick ",
        )
    )


def _should_refresh_player_directory_after_command(result: dict) -> bool:
    if not _is_successful_command_result(result):
        return False
    return _result_command(result).startswith(_PLAYER_DIRECTORY_COMMAND_PREFIXES)


def _is_successful_command_result(result: dict) -> bool:
    return result.get("status") in {"executed", "file_updated", "no_change"}


def _result_command(result: dict) -> str:
    return str(
        result.get("normalized_command")
        or result.get("command")
        or ""
    ).strip().lower()


def _summarize_command_results(results: list[dict]) -> dict:
    success_statuses = {"executed", "file_updated", "no_change"}
    failed = [result for result in results if result.get("status") not in success_statuses]
    if failed:
        commands = ", ".join(result.get("command", "") for result in failed)
        message = "; ".join(
            result.get("error_message") or result.get("message") or result.get("status", "")
            for result in failed
        )
        return {
            "status": "failed",
            "message": f"\u90e8\u5206\u547d\u4ee4\u6267\u884c\u5931\u8d25\uff1a{commands}",
            "error_message": message,
        }
    if len(results) == 1:
        return results[0]
    if any(result.get("status") == "file_updated" for result in results):
        commands = ", ".join(result.get("command", "") for result in results)
        return {
            "status": "file_updated",
            "message": f"\u5df2\u901a\u8fc7\u914d\u7f6e\u6587\u4ef6\u5b8c\u6210 {len(results)} \u4e2a\u73a9\u5bb6\u64cd\u4f5c\u3002",
            "output": commands,
        }
    commands = ", ".join(result.get("command", "") for result in results)
    return {
        "status": "executed",
        "message": f"\u5df2\u53d1\u9001 {len(results)} \u6761 Minecraft \u547d\u4ee4\u3002",
        "output": commands,
    }


def _command_feedback_message(result: dict) -> str:
    message = str(
        result.get("error_message")
        or result.get("message")
        or result.get("output")
        or "\u547d\u4ee4\u672a\u6267\u884c\u3002"
    )
    if result.get("status") not in {"failed", "blocked"}:
        return message
    translations = {
        "Server service is not configured.": "\u670d\u52a1\u5668\u547d\u4ee4\u670d\u52a1\u672a\u914d\u7f6e\u3002",
    }
    if message in translations:
        return translations[message]
    if not re.search(r"[\u4e00-\u9fff]", message):
        return "\u547d\u4ee4\u6267\u884c\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u670d\u52a1\u5668\u72b6\u6001\u540e\u91cd\u8bd5\u3002"
    return message


def _player_avatar(player: dict) -> ft.Control:
    name = player.get("name", "?")
    initial = name[0].upper() if name else "?"
    avatar_url = player.get("avatar_url", "")
    bgcolor = _avatar_color(name)
    fallback = ft.Container(
        content=ft.Text(initial, size=14, weight=ft.FontWeight.W_700, color="#ffffff"),
        bgcolor=bgcolor,
        border_radius=6,
        width=28,
        height=28,
        alignment=ft.Alignment(0, 0),
    )
    if not avatar_url:
        content: ft.Control = fallback
    else:
        content = ft.Image(
            src=avatar_url,
            width=28,
            height=28,
            fit=ft.BoxFit.COVER,
            border_radius=6,
            error_content=fallback,
        )
    return ft.Container(
        content=content,
        width=28,
        height=28,
        border_radius=6,
        border=ft.Border.all(1, theme.LINE),
    )


def _avatar_color(name: str) -> str:
    colors = [
        "#748a56",
        "#4d7893",
        "#9b5548",
        "#b8783b",
        "#6a5c9e",
        "#3a7d8c",
        "#c4553b",
        "#4f7a4c",
    ]
    idx = sum(ord(c) for c in name) % len(colors)
    return colors[idx]


def _chat_message(kind: str, label: str, text: str | None) -> ft.Control:
    if kind == "user":
        bgcolor = theme.BLUE
        border_color = theme.BLUE
        color = "#ffffff"
        alignment = ft.Alignment(1, 0)
    elif kind == "tool":
        bgcolor = theme.GREEN_SOFT
        border_color = "#235d47"
        color = "#a5e8c8"
        alignment = ft.Alignment(-1, 0)
    else:
        bgcolor = theme.PANEL_SOFT
        border_color = theme.LINE
        color = theme.TEXT
        alignment = ft.Alignment(-1, 0)

    return ft.Container(
        content=ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        label,
                        size=11,
                        weight=ft.FontWeight.W_700,
                        color=color,
                        opacity=0.72,
                    ),
                    ft.Text(text or "", size=13, color=color, selectable=True),
                ],
                spacing=4,
            ),
            bgcolor=bgcolor,
            border=ft.Border.all(1, border_color),
            border_radius=theme.RADIUS,
            padding=ft.Padding.symmetric(horizontal=9, vertical=8),
            width=275,
        ),
        alignment=alignment,
    )


def _empty_state(text: str) -> ft.Control:
    return ft.Container(
        content=ft.Text(text, size=13, color=theme.MUTED, text_align=ft.TextAlign.CENTER),
        bgcolor=theme.PANEL_SOFT,
        border=ft.Border.all(1, theme.LINE),
        border_radius=theme.RADIUS,
        padding=ft.Padding.symmetric(vertical=14, horizontal=10),
        alignment=ft.Alignment(0, 0),
    )


def _empty_player_directory(error: bool = False) -> dict:
    return {
        "players": [],
        "banned_ips": [],
        "counts": {
            "players": 0,
            "operators": 0,
            "banned_players": 0,
            "banned_ips": 0,
        },
        "captured_at": "",
        "ip_mapping_note": "",
        "error": error,
    }


def _player_filter_icon(filter_key: str) -> ft.IconData:
    return {
        "online": ft.Icons.PEOPLE,
        "all": ft.Icons.GROUP,
        "operators": ft.Icons.ADMIN_PANEL_SETTINGS,
        "banned_players": ft.Icons.BLOCK,
        "banned_ips": ft.Icons.LANGUAGE,
    }.get(filter_key, ft.Icons.FILTER_LIST)


def _directory_player_status(player: dict) -> ft.Control:
    if player.get("is_online"):
        return status_pill("\u5728\u7ebf")
    if player.get("is_banned"):
        return status_pill("\u5df2\u5c01\u7981", color=theme.RED, bgcolor=theme.RED_SOFT)
    if player.get("is_operator"):
        level = player.get("operator_level")
        label = f"\u7ba1\u7406 Lv.{level}" if level is not None else "\u7ba1\u7406\u5458"
        return status_pill(label, color=theme.BLUE, bgcolor=theme.BLUE_SOFT)
    return status_pill("\u5386\u53f2", color=theme.MUTED, bgcolor=theme.PANEL)


def _banned_ip_directory_row(entry: dict) -> ft.Control:
    players = entry.get("players") or []
    player_text = (
        "\u5173\u8054 " + ", ".join(str(player) for player in players[:3])
        if players
        else "\u672a\u5339\u914d\u5230\u73a9\u5bb6"
    )
    if len(players) > 3:
        player_text += f" \u7b49 {len(players)} \u4eba"
    detail_parts = [player_text]
    expires = entry.get("expires")
    if expires:
        detail_parts.append("\u5230\u671f " + str(expires))
    reason = entry.get("reason")
    if reason:
        detail_parts.append(str(reason))
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(
                    content=ft.Icon(ft.Icons.LANGUAGE, size=16, color=theme.RED),
                    width=28,
                    height=28,
                    bgcolor=theme.RED_SOFT,
                    border_radius=6,
                    alignment=ft.Alignment(0, 0),
                ),
                ft.Column(
                    controls=[
                        ft.Text(
                            str(entry.get("ip") or "?"),
                            size=13,
                            weight=ft.FontWeight.W_700,
                            color=theme.TEXT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            " \u00b7 ".join(detail_parts),
                            size=11,
                            color=theme.MUTED,
                            max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Row(
                            controls=[
                                status_pill("\u5c01\u7981 IP", color=theme.RED, bgcolor=theme.RED_SOFT),
                                status_pill(
                                    "\u5df2\u6620\u5c04" if players else "\u5f85\u6620\u5c04",
                                    color=theme.BLUE if players else theme.MUTED,
                                    bgcolor=theme.BLUE_SOFT if players else theme.PANEL,
                                ),
                            ],
                            spacing=5,
                        ),
                    ],
                    spacing=4,
                    expand=True,
                ),
            ],
            spacing=9,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        padding=ft.Padding.symmetric(horizontal=9, vertical=8),
        border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
    )


def _player_directory_detail(player: dict) -> str:
    parts = []
    uuid_value = player.get("uuid")
    if uuid_value:
        parts.append("UUID " + str(uuid_value)[:8])
    known_ips = player.get("known_ips") or []
    if known_ips:
        parts.append("IP " + ", ".join(str(ip) for ip in known_ips[:2]))
    ban = player.get("ban") or {}
    reason = ban.get("reason")
    if reason:
        parts.append(str(reason))
    return " \u00b7 ".join(parts) if parts else "\u5386\u53f2\u73a9\u5bb6"


def _memory_hint(metrics: dict) -> str:
    if metrics.get("loading"):
        return "\u91c7\u6837\u4e2d"
    used = metrics.get("memory_used_mb")
    total = metrics.get("memory_total_mb")
    if used is None or total is None:
        return "\u672a\u77e5"
    return f"{used / 1024:.1f} / {total / 1024:.1f} GB"


def _server_memory_hint(metrics: dict) -> str:
    if metrics.get("loading"):
        return "\u670d\u52a1\u7aef\u8fdb\u7a0b \u7b49\u5f85\u91c7\u6837"
    used = float(metrics.get("server_memory_used_mb") or 0)
    percent = float(metrics.get("server_memory_percent") or 0)
    if used <= 0:
        return "\u670d\u52a1\u7aef\u8fdb\u7a0b 0%"
    return f"\u670d\u52a1\u7aef\u8fdb\u7a0b {used / 1024:.1f} GB ({_format_percent_value(percent)}%)"


def _format_percent_value(value: float) -> str:
    if value <= 0:
        return "0"
    if value < 0.1:
        return "<0.1"
    if value < 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return str(round(value))


def _display_time() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _positive_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _empty_player_data(server_state: str = "stopped") -> dict:
    return {
        "online_count": 0,
        "max_players": 20,
        "players": [],
        "captured_at": "",
        "server_state": server_state,
    }


def _empty_server_capabilities() -> dict:
    return {
        "server_core": {
            "name": "Unknown",
            "version": None,
            "source": "",
        },
        "plugins": [],
        "supports_plugins": False,
        "supports_temp_ban": False,
        "supports_temp_ip_ban": False,
        "temp_ban_command": None,
        "temp_ip_ban_command": None,
        "temp_ban_provider": None,
    }


def _player_render_signature(
    data: dict,
    capabilities: dict,
    current_filter: str = "online",
    directory_data: dict | None = None,
) -> tuple:
    players = tuple(
        (
            player.get("name"),
            player.get("uuid"),
            player.get("avatar_url"),
            bool(player.get("is_operator")),
            player.get("operator_level"),
        )
        for player in data.get("players", [])
    )
    menu_capabilities = (
        bool(capabilities.get("supports_temp_ban")),
        bool(capabilities.get("supports_temp_ip_ban")),
        capabilities.get("temp_ban_command"),
        capabilities.get("temp_ip_ban_command"),
    )
    directory_players = ()
    directory_ips = ()
    if directory_data:
        directory_players = tuple(
            (
                player.get("name"),
                player.get("uuid"),
                bool(player.get("is_online")),
                bool(player.get("is_operator")),
                player.get("operator_level"),
                bool(player.get("is_banned")),
                tuple(player.get("known_ips") or []),
                str(player.get("ban") or ""),
            )
            for player in directory_data.get("players", [])
        )
        directory_ips = tuple(
            (
                entry.get("ip"),
                tuple(entry.get("players") or []),
                entry.get("expires"),
                entry.get("reason"),
            )
            for entry in directory_data.get("banned_ips", [])
        )
    return (
        current_filter,
        data.get("server_state", "stopped"),
        players,
        directory_players,
        directory_ips,
        menu_capabilities,
    )


def _loading_metrics() -> dict:
    return {
        "loading": True,
        "cpu_percent": None,
        "memory_percent": None,
        "memory_used_mb": None,
        "memory_total_mb": None,
        "server_cpu_percent": 0.0,
        "server_memory_percent": 0.0,
        "server_memory_used_mb": 0.0,
        "server_pid": None,
    }


def _is_config_file(relative_path: str) -> bool:
    from src.mc.config_files import is_config_file_allowed

    return is_config_file_allowed(relative_path)


def _is_versioned_text_config(relative_path: str) -> bool:
    from src.mc.server_files import is_versioned_text_config

    return is_versioned_text_config(relative_path)
