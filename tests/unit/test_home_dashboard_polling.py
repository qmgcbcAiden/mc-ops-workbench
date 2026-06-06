from __future__ import annotations

import threading
from types import SimpleNamespace

import flet as ft

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.interface.command_interface import CommandInterface
from src.repositories.command_repository import CommandRepository
from src.service.command_service import CommandService
from src.ui.pages.home import (
    DASHBOARD_REFRESH_INTERVAL_SECONDS,
    OpsHomePage,
    STOP_LOG_FOLLOW_SECONDS,
    _empty_server_capabilities,
    _format_percent_value,
    _normalize_ban_duration,
    _player_row,
    _temporary_ban_command,
    _temporary_combined_ban_commands,
)


class _PageStub:
    def __init__(self) -> None:
        self.update_count = 0

    def update(self) -> None:
        self.update_count += 1


class _RecordingPageStub(_PageStub):
    def __init__(self, home_ref) -> None:
        super().__init__()
        self._home_ref = home_ref
        self.snapshots: list[tuple[int, int]] = []

    def update(self) -> None:
        super().update()
        home = self._home_ref()
        self.snapshots.append(
            (
                len(home.interfaces.system.pids),
                home.interfaces.player.calls,
            )
        )


class _PageTaskStub(_PageStub):
    def __init__(self) -> None:
        super().__init__()
        self.thread_handler = None
        self.thread_args = None

    def run_thread(self, handler, *args, **kwargs):
        del kwargs
        self.thread_handler = handler
        self.thread_args = args
        return None


class _PageAsyncTaskStub(_PageTaskStub):
    def __init__(self) -> None:
        super().__init__()
        self.task_handler = None
        self.task_args = None

    def run_task(self, handler, *args, **kwargs):
        del kwargs
        self.task_handler = handler
        self.task_args = args
        return None


class _PageAsyncOnlyStub(_PageStub):
    def __init__(self) -> None:
        super().__init__()
        self.task_handler = None
        self.task_args = None

    def run_task(self, handler, *args, **kwargs):
        del kwargs
        self.task_handler = handler
        self.task_args = args
        return None


class _ServerStub:
    def __init__(self, state: str = "running", pid: int | None = 4321) -> None:
        self.state = state
        self.pid = pid

    def get_server_status(self) -> dict:
        return {"state": self.state, "pid": self.pid}


class _PlayerStub:
    def __init__(self) -> None:
        self.calls = 0
        self.directory_calls = 0
        self.refresh_directory_calls = 0

    def get_current_players(self) -> dict:
        self.calls += 1
        return {
            "online_count": 0,
            "max_players": 20,
            "players": [],
            "captured_at": "",
            "server_state": "running",
        }

    def get_player_directory(self) -> dict:
        self.directory_calls += 1
        return {
            "players": [
                {
                    "name": "Steve",
                    "uuid": "12345678-abcd-1234-abcd-123456789012",
                    "avatar_url": "",
                    "is_operator": True,
                    "operator_level": 4,
                    "is_banned": False,
                    "known_ips": [],
                    "sources": ["ops"],
                }
            ],
            "banned_ips": [],
            "counts": {
                "players": 1,
                "operators": 1,
                "banned_players": 0,
                "banned_ips": 0,
            },
            "captured_at": "",
            "ip_mapping_note": "",
        }

    def refresh_player_directory(self) -> dict:
        self.refresh_directory_calls += 1
        return self.get_player_directory()


class _CommandStub:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    def classify_command(self, command: str) -> dict:
        return {
            "normalized_command": command.lower(),
            "risk_level": "HIGH",
            "confirmation_required": True,
            "message": "",
        }

    def submit_command(
        self,
        command: str,
        requested_by: str = "ui",
        user_confirmed: bool = False,
    ) -> dict:
        self.commands.append({
            "command": command,
            "requested_by": requested_by,
            "user_confirmed": user_confirmed,
        })
        return {
            "command": command,
            "normalized_command": command.lower(),
            "status": "executed",
            "risk_level": "HIGH",
            "confirmation_required": user_confirmed,
            "message": "sent",
        }


class _RecordingCommandServer:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def send_command(self, command: str) -> dict:
        self.commands.append(command)
        return {
            "status": "executed",
            "output": "sent",
            "error_message": None,
        }


class _SystemStub:
    def __init__(self) -> None:
        self.pids: list[int | None] = []

    def capture_metrics(self, server_pid: int | None = None) -> dict:
        self.pids.append(server_pid)
        return {
            "cpu_percent": 37.0,
            "memory_percent": 48.0,
            "memory_used_mb": 2048.0,
            "memory_total_mb": 8192.0,
            "server_cpu_percent": 6.0,
            "server_memory_percent": 3.0,
            "server_memory_used_mb": 256.0,
            "server_pid": server_pid,
        }


class _ChatStub:
    def __init__(self) -> None:
        self.attach_calls: list[dict] = []

    def attach_log_selection(self, session_id: str, selection: dict) -> dict:
        self.attach_calls.append({
            "session_id": session_id,
            "selection": selection,
        })
        return {
            "attachment_id": "att_log_1",
            "label": selection.get("label", "latest.log"),
            "line_count": selection.get("line_count", 0),
            "time_range": selection.get("time_range", ""),
        }


class _ChatPanelStub:
    def __init__(self) -> None:
        self.attachments: list[dict] = []

    def add_attachment(self, attachment: dict) -> None:
        self.attachments.append(attachment)


class _LogViewerStub:
    def __init__(self) -> None:
        self.start_count = 0
        self.stop_count = 0
        self.refresh_live_count = 0
        self.clear_calls: list[bool] = []
        self.clear_selection_count = 0
        self.selection = {
            "raw_text": "",
            "label": "",
            "line_count": 0,
            "source": "",
            "time_range": "",
            "event_ids": [],
        }
        self.control = ft.ListView()

    def visible_events(self, limit: int = 100) -> list[dict]:
        del limit
        return []

    def clear(self, prime_tail: bool = False) -> None:
        self.clear_calls.append(prime_tail)

    def get_selected_raw_logs(self) -> dict:
        return self.selection

    def clear_selection(self) -> None:
        self.clear_selection_count += 1
        self.selection = {
            "raw_text": "",
            "label": "",
            "line_count": 0,
            "source": "",
            "time_range": "",
            "event_ids": [],
        }

    def start_auto_refresh(self) -> None:
        self.start_count += 1

    def restart_auto_refresh(self) -> None:
        self.start_count += 1

    def refresh_live_once(self) -> bool:
        self.refresh_live_count += 1
        return True

    def stop_auto_refresh(self) -> None:
        self.stop_count += 1


class _ServerControlsStub:
    def __init__(self) -> None:
        self.refresh_count = 0

    def refresh(self) -> None:
        self.refresh_count += 1


class _FileExplorerStub:
    def __init__(self) -> None:
        self.refresh_count = 0

    def refresh_tree(self) -> None:
        self.refresh_count += 1


def _make_home(state: str = "running") -> OpsHomePage:
    home = object.__new__(OpsHomePage)
    server = _ServerStub(state=state)
    home.page = _PageStub()
    home.interfaces = SimpleNamespace(
        server=server,
        player=_PlayerStub(),
        system=_SystemStub(),
        chat=_ChatStub(),
    )
    home.metrics_row = ft.ResponsiveRow(spacing=12, run_spacing=12)
    home.player_list = ft.ListView(expand=True, spacing=0, padding=0)
    home.player_updated = ft.Text("")
    home.log_viewer = _LogViewerStub()
    home.chat_panel = _ChatPanelStub()
    home.chat_session_id = "session_1"
    home.server_controls = _ServerControlsStub()
    home.file_explorer = _FileExplorerStub()
    home._status_polling = False
    home._dashboard_polling = False
    home._dashboard_poll_lock = threading.Lock()
    home._dashboard_refresh_lock = threading.Lock()
    home._dashboard_stop_event = threading.Event()
    home._startup_log_follow_generation = 0
    return home


def _menu_label(item: ft.PopupMenuItem) -> str:
    content = item.content
    return content.value if hasattr(content, "value") else str(content)


def _menu_index(items: list[ft.PopupMenuItem], label: str) -> int:
    labels = [_menu_label(item) for item in items]
    return labels.index(label)


def test_dashboard_polling_uses_flet_run_thread_when_available() -> None:
    home = _make_home()
    home.page = _PageTaskStub()
    home._dashboard_stop_event.set()

    home._start_dashboard_polling()

    assert home._dashboard_polling is True
    assert home._dashboard_stop_event.is_set() is False
    assert home.page.thread_handler == home._dashboard_poll_loop
    home._stop_dashboard_polling()


def test_dashboard_polling_prefers_run_thread_over_run_task_when_both_exist() -> None:
    home = _make_home()
    home.page = _PageAsyncTaskStub()

    home._start_dashboard_polling()

    assert home._dashboard_polling is True
    assert home.page.thread_handler == home._dashboard_poll_loop
    assert home.page.task_handler is None
    home._stop_dashboard_polling()


def test_dashboard_polling_falls_back_to_flet_run_task() -> None:
    home = _make_home()
    home.page = _PageAsyncOnlyStub()

    home._start_dashboard_polling()

    assert home._dashboard_polling is True
    assert home.page.task_handler == home._dashboard_poll_loop_async
    home._stop_dashboard_polling()


def test_background_refresh_starts_after_build_when_requested() -> None:
    home = _make_home()
    home.page = _PageTaskStub()

    home.start_background_refresh()

    assert home._dashboard_polling is True
    assert home.page.thread_handler == home._dashboard_poll_loop
    assert home.page.update_count == 2
    home._stop_dashboard_polling()


def test_dashboard_snapshot_refreshes_metrics_and_players() -> None:
    home = _make_home(state="running")

    should_continue = home._refresh_dashboard_snapshot()

    assert should_continue is True
    assert home.interfaces.system.pids == [4321]
    assert home.interfaces.player.calls == 1
    assert len(home.metrics_row.controls) == 4
    assert home.server_controls.refresh_count == 1
    assert home.file_explorer.refresh_count == 1
    assert home.page.update_count == 2


def test_dashboard_snapshot_renders_metrics_before_player_refresh() -> None:
    home = _make_home(state="running")
    home.page = _RecordingPageStub(lambda: home)

    home._refresh_dashboard_snapshot()

    assert home.page.snapshots[0] == (1, 0)
    assert home.page.snapshots[1] == (1, 1)


def test_dashboard_snapshot_refreshes_file_tree() -> None:
    home = _make_home(state="running")

    home._refresh_dashboard_snapshot()

    assert home.file_explorer.refresh_count == 1


def test_command_execution_starts_realtime_log_refresh() -> None:
    home = _make_home(state="running")

    home._start_command_log_refresh()

    assert home.log_viewer.start_count == 1


def test_log_ask_ai_adds_draft_attachment_and_clears_selection() -> None:
    home = _make_home(state="running")
    home.log_viewer.selection = {
        "raw_text": "[12:00:00] ERROR boom",
        "label": "latest.log",
        "line_count": 1,
        "source": "latest.log",
        "time_range": "12:00:00-12:00:00",
        "event_ids": [1],
    }

    home._on_log_ask_ai()

    assert home.interfaces.chat.attach_calls == [
        {
            "session_id": "session_1",
            "selection": {
                "raw_text": "[12:00:00] ERROR boom",
                "label": "latest.log",
                "line_count": 1,
                "source": "latest.log",
                "time_range": "12:00:00-12:00:00",
                "event_ids": [1],
            },
        }
    ]
    assert home.chat_panel.attachments == [
        {
            "attachment_id": "att_log_1",
            "label": "latest.log",
            "line_count": 1,
            "time_range": "12:00:00-12:00:00",
        }
    ]
    assert home.log_viewer.clear_selection_count == 1


def test_server_start_request_clears_existing_logs_before_refreshing() -> None:
    home = _make_home(state="stopped")
    home.page = _PageAsyncOnlyStub()

    home._on_server_start_requested()

    assert home.log_viewer.clear_calls == [True]
    assert home.log_viewer.start_count == 1
    assert home.log_viewer.refresh_live_count == 1
    assert home.page.task_handler == home._startup_log_follow_loop_async


def test_server_start_status_refreshes_live_logs_immediately() -> None:
    home = _make_home(state="starting")

    home._on_server_status_changed({"state": "starting", "pid": 4321})

    assert home.log_viewer.start_count == 1
    assert home.log_viewer.refresh_live_count == 1


def test_server_stop_status_flushes_tail_logs_before_stopping_refresh() -> None:
    home = _make_home(state="stopped")
    home.page = _PageAsyncOnlyStub()
    home._refresh_file_tree = lambda: None
    home._render_players = lambda _players: None
    home._refresh_dashboard_snapshot_soon = lambda: None
    home._update_dashboard_controls = lambda **_kwargs: None

    home._on_server_status_changed({"state": "stopped", "pid": None})

    assert home.log_viewer.refresh_live_count == 1
    assert home.log_viewer.stop_count == 1
    assert home.page.task_handler == home._startup_log_follow_loop_async
    assert home.page.task_args == (home._startup_log_follow_generation, STOP_LOG_FOLLOW_SECONDS)


def test_successful_console_command_does_not_show_snackbar() -> None:
    home = _make_home(state="running")
    shown = []
    home._show_snackbar = lambda *args: shown.append(args)

    home._on_console_command_result(
        {"status": "executed", "output": "Command sent to server stdin."}
    )

    assert shown == []
    assert home.log_viewer.start_count == 1


def test_failed_console_command_still_shows_snackbar() -> None:
    home = _make_home(state="running")
    shown = []
    home._show_snackbar = lambda *args: shown.append(args)

    home._on_console_command_result(
        {"status": "failed", "error_message": "服务端 stdin 不可用。"}
    )

    assert shown == [("服务端 stdin 不可用。", True)]
    assert home.log_viewer.start_count == 1


def test_failed_console_command_converts_english_error_to_chinese_snackbar() -> None:
    home = _make_home(state="running")
    shown = []
    home._show_snackbar = lambda *args: shown.append(args)

    home._on_console_command_result(
        {"status": "failed", "error_message": "Server service is not configured."}
    )

    assert shown == [("服务器命令服务未配置。", True)]


def test_failed_console_command_uses_chinese_fallback_for_unknown_english_error() -> None:
    home = _make_home(state="running")
    shown = []
    home._show_snackbar = lambda *args: shown.append(args)

    home._on_console_command_result(
        {"status": "failed", "error_message": "Connection was closed unexpectedly."}
    )

    assert shown == [("命令执行失败，请检查服务器状态后重试。", True)]


def test_successful_player_menu_command_does_not_show_snackbar() -> None:
    home = _make_home(state="running")
    shown = []
    home._show_snackbar = lambda *args: shown.append(args)

    home._handle_player_command_result(
        {"status": "executed", "output": "Command sent to server stdin."}
    )

    assert shown == []
    assert home.log_viewer.start_count == 1


def test_file_updated_player_menu_command_shows_success_snackbar() -> None:
    home = _make_home(state="stopped")
    shown = []
    home._show_snackbar = lambda *args: shown.append(args)
    home._refresh_players = lambda: None

    home._handle_player_command_result(
        {
            "status": "file_updated",
            "normalized_command": "op Steve",
            "message": "服务器未运行，已更新 ops.json；下次启动后生效。",
        }
    )

    assert shown == [("服务器未运行，已更新 ops.json；下次启动后生效。", False)]
    assert home.log_viewer.start_count == 1


def test_player_menu_directory_command_refreshes_directory_cache_on_online_filter() -> None:
    home = _make_home(state="running")

    home._handle_player_command_result(
        {
            "status": "executed",
            "normalized_command": "op steve",
            "command": "op Steve",
            "message": "sent",
        }
    )

    assert home.interfaces.player.refresh_directory_calls == 1
    assert home.interfaces.player.calls == 1
    assert home.log_viewer.start_count == 1


def test_console_directory_command_refreshes_banned_ip_filter() -> None:
    home = _make_home(state="running")
    home._build_player_panel()
    home._player_list_filter = "banned_ips"

    home._on_console_command_result(
        {
            "status": "executed",
            "normalized_command": "ban-ip steve",
            "command": "ban-ip Steve",
            "message": "sent",
        }
    )

    assert home.interfaces.player.refresh_directory_calls == 1
    assert home.interfaces.player.calls == 1
    assert "封禁 IP" in home.player_updated.value


def test_cancelled_directory_command_does_not_refresh_player_directory() -> None:
    home = _make_home(state="running")

    home._handle_player_command_result(
        {
            "status": "cancelled",
            "normalized_command": "deop steve",
            "command": "deop Steve",
            "message": "cancelled",
        }
    )

    assert home.interfaces.player.refresh_directory_calls == 0
    assert home.interfaces.player.calls == 0


def test_metric_cards_are_reused_between_metric_refreshes() -> None:
    home = _make_home(state="running")
    players = home.interfaces.player.get_current_players()
    first_metrics = home.interfaces.system.capture_metrics(server_pid=4321)
    second_metrics = {
        **first_metrics,
        "cpu_percent": 51.0,
        "memory_percent": 63.0,
        "server_cpu_percent": 8.0,
    }

    home._render_metrics(players, first_metrics, [])
    first_controls = list(home.metrics_row.controls)
    first_cards = [control.content for control in first_controls]
    first_cpu_card = home._metric_cards["cpu"]
    first_cpu_value = first_cpu_card.value_text
    first_cpu_progress_bar = first_cpu_card.progress_bar
    first_cpu_highlight_bar = first_cpu_card.highlight_bar
    home._render_metrics(players, second_metrics, [])

    assert home.metrics_row.controls == first_controls
    assert [control.content for control in home.metrics_row.controls] == first_cards
    assert home._metric_cards["cpu"].value_text is first_cpu_value
    assert home._metric_cards["cpu"].progress_bar is first_cpu_progress_bar
    assert home._metric_cards["cpu"].highlight_bar is first_cpu_highlight_bar
    assert first_cpu_value.value == "51"
    assert first_cpu_progress_bar.value == 0.51
    assert first_cpu_highlight_bar.value == 0.08


def test_player_row_allows_missing_uuid() -> None:
    row = _player_row(
        {
            "name": "Steve",
            "uuid": None,
            "avatar_url": "https://minotar.net/helm/Steve/40.png",
        }
    )

    assert row.__class__.__name__ == "Container"


def test_player_row_wraps_context_menu_when_actions_are_available() -> None:
    selected = []

    row = _player_row(
        {
            "name": "Steve",
            "uuid": None,
            "avatar_url": "",
        },
        [ft.PopupMenuItem(content="inspect")],
        lambda event: selected.append(event),
    )

    assert row.__class__.__name__ == "ContextMenu"
    assert row.secondary_items[0].content == "inspect"
    assert row.on_select is not None


def test_player_render_reuses_context_menu_during_identical_poll_refresh() -> None:
    home = _make_home(state="running")
    home.interfaces.command = _CommandStub()
    data = {
        "online_count": 1,
        "max_players": 20,
        "players": [{"name": "Steve", "uuid": None, "avatar_url": "", "is_operator": True}],
        "captured_at": "first",
        "server_state": "running",
    }

    home._render_players(data)
    context_menu = home.player_list.controls[0]
    home._render_players({**data, "captured_at": "second"})

    assert home.player_list.controls[0] is context_menu


def test_player_panel_uses_filter_button_without_nested_directory_panel() -> None:
    home = _make_home(state="running")
    home._player_list_filter = "online"

    panel = home._build_player_panel()
    body = panel.content.controls[1].content

    assert body is home.player_list
    assert home.player_filter_button.icon == ft.Icons.FILTER_LIST
    assert home.player_filter_button.items[0].checked is True
    assert home.player_filter_button.items[0].content.value == "在线玩家"


def test_player_filter_renders_directory_items_in_main_player_list() -> None:
    home = _make_home(state="running")
    home.interfaces.command = _CommandStub()
    home._build_player_panel()

    home._set_player_filter("operators")

    assert home.interfaces.player.directory_calls >= 1
    assert home._player_list_filter == "operators"
    assert home.player_filter_button.items[2].checked is True
    assert home.player_list.controls[0].__class__.__name__ == "ContextMenu"
    assert "管理员" in home.player_updated.value


def test_player_render_rebuilds_context_menu_when_operator_state_changes() -> None:
    home = _make_home(state="running")
    home.interfaces.command = _CommandStub()
    data = {
        "online_count": 1,
        "max_players": 20,
        "players": [{"name": "Steve", "uuid": None, "avatar_url": "", "is_operator": True}],
        "server_state": "running",
    }

    home._render_players(data)
    context_menu = home.player_list.controls[0]
    updated = {
        **data,
        "players": [{"name": "Steve", "uuid": None, "avatar_url": "", "is_operator": False}],
    }
    home._render_players(updated)

    assert home.player_list.controls[0] is not context_menu


def test_player_context_menu_select_dispatches_stored_handler() -> None:
    home = _make_home(state="running")
    calls = []
    item = ft.PopupMenuItem(content="inspect", data=lambda _event: calls.append("selected"))

    home._on_player_context_menu_select(SimpleNamespace(item=item))

    assert calls == ["selected"]


def test_player_context_menu_select_falls_back_to_item_index() -> None:
    home = _make_home(state="running")
    calls = []
    items = [
        ft.PopupMenuItem(content="first", data=lambda _event: calls.append("first")),
        ft.PopupMenuItem(content="second", data=lambda _event: calls.append("second")),
    ]

    home._on_player_context_menu_select(SimpleNamespace(item=None, item_index=1), items)

    assert calls == ["second"]


def test_player_context_menu_deop_action_submits_immediately() -> None:
    home = _make_home(state="running")
    command = _CommandStub()
    home.interfaces.command = command
    items = home._player_context_menu_items(
        {"name": "Steve", "is_operator": True},
        _empty_server_capabilities(),
    )

    home._on_player_context_menu_select(
        SimpleNamespace(
            item=None,
            item_index=_menu_index(items, "\u53d6\u6d88\u7ba1\u7406\u5458"),
        ),
        items,
    )

    assert command.commands[0] == {
        "command": "deop Steve",
        "requested_by": "player_menu",
        "user_confirmed": True,
    }


def test_player_context_menu_deop_reaches_server_command_service(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    repository = CommandRepository(get_connection(db_path))
    server = _RecordingCommandServer()
    home = _make_home(state="running")
    home.interfaces.command = CommandInterface(CommandService(repository, server))
    items = home._player_context_menu_items(
        {"name": "Steve", "is_operator": True},
        _empty_server_capabilities(),
    )

    home._on_player_context_menu_select(
        SimpleNamespace(
            item=None,
            item_index=_menu_index(items, "\u53d6\u6d88\u7ba1\u7406\u5458"),
        ),
        items,
    )

    assert server.commands == ["deop Steve"]
    audit = repository.list_recent(limit=1)[0]
    assert audit["command"] == "deop Steve"
    assert audit["requested_by"] == "player_menu"
    assert audit["status"] == "executed"


def test_player_context_menu_hides_tempban_without_plugin_capability() -> None:
    home = _make_home(state="running")
    home.interfaces.command = _CommandStub()

    items = home._player_context_menu_items(
        {"name": "Steve"},
        _empty_server_capabilities(),
    )
    labels = [_menu_label(item) for item in items]

    assert "\u8bbe\u4e3a\u7ba1\u7406\u5458" in labels
    assert "\u53d6\u6d88\u7ba1\u7406\u5458" not in labels
    assert "\u6c38\u4e45\u5c01\u7981" in labels
    assert "\u6c38\u4e45\u5c01\u7981 IP" in labels
    assert not any("\u4e34\u65f6\u5c01\u7981" in label for label in labels)


def test_player_context_menu_shows_deop_for_existing_operator() -> None:
    home = _make_home(state="running")
    home.interfaces.command = _CommandStub()

    items = home._player_context_menu_items(
        {"name": "Steve", "is_operator": True},
        _empty_server_capabilities(),
    )
    labels = [_menu_label(item) for item in items]

    assert "\u53d6\u6d88\u7ba1\u7406\u5458" in labels
    assert "\u8bbe\u4e3a\u7ba1\u7406\u5458" not in labels


def test_player_context_menu_shows_pardon_for_banned_player() -> None:
    home = _make_home(state="running")
    home.interfaces.command = _CommandStub()

    items = home._player_context_menu_items(
        {"name": "Steve", "is_banned": True},
        _empty_server_capabilities(),
    )
    labels = [_menu_label(item) for item in items]

    assert "\u89e3\u9664\u5c01\u7981" in labels
    assert "\u6c38\u4e45\u5c01\u7981" not in labels


def test_player_context_menu_shows_tempban_when_plugin_capability_exists() -> None:
    home = _make_home(state="running")
    home.interfaces.command = _CommandStub()
    capabilities = {
        **_empty_server_capabilities(),
        "supports_temp_ban": True,
        "supports_temp_ip_ban": True,
        "temp_ban_command": "tempban",
        "temp_ip_ban_command": "tempipban",
    }

    items = home._player_context_menu_items({"name": "Steve"}, capabilities)
    labels = [_menu_label(item) for item in items]

    assert "\u4e34\u65f6\u5c01\u7981 \u00b7 1 \u5929" in labels
    assert "\u4e34\u65f6\u5c01\u7981 IP \u00b7 1 \u5929" in labels
    assert "\u4e34\u65f6\u5c01\u7981 \u00b7 \u81ea\u5b9a\u4e49\u65f6\u95f4..." in labels
    assert "\u6c38\u4e45\u5c01\u7981\u73a9\u5bb6\u548c IP" in labels
    assert "\u4e34\u65f6\u5c01\u7981\u73a9\u5bb6\u548c IP \u00b7 1 \u5929" in labels


def test_custom_ban_duration_validation() -> None:
    assert _normalize_ban_duration(" 7D ") == "7d"
    assert _normalize_ban_duration("30m") == "30m"
    assert _normalize_ban_duration("1 day") is None
    assert _temporary_ban_command("tempban", "Steve", "7d") == "tempban Steve 7d"
    assert _temporary_combined_ban_commands(
        "tempban",
        "tempipban",
        "Steve",
        "7d",
    ) == ["tempban Steve 7d", "tempipban Steve 7d"]


def test_player_menu_submits_combined_commands_in_order() -> None:
    home = _make_home(state="running")
    command = _CommandStub()
    home.interfaces.command = command

    results = home._submit_player_commands(["ban Steve", "ban-ip Steve"])

    assert [result["command"] for result in results] == ["ban Steve", "ban-ip Steve"]
    assert command.commands == [
        {
            "command": "ban Steve",
            "requested_by": "player_menu",
            "user_confirmed": True,
        },
        {
            "command": "ban-ip Steve",
            "requested_by": "player_menu",
            "user_confirmed": True,
        },
    ]


def test_player_context_menu_combined_ban_submits_two_commands_immediately() -> None:
    home = _make_home(state="running")
    command = _CommandStub()
    home.interfaces.command = command
    items = home._player_context_menu_items(
        {"name": "Steve"},
        _empty_server_capabilities(),
    )

    home._on_player_context_menu_select(
        SimpleNamespace(
            item=None,
            item_index=_menu_index(items, "\u6c38\u4e45\u5c01\u7981\u73a9\u5bb6\u548c IP"),
        ),
        items,
    )

    assert [call["command"] for call in command.commands[:2]] == [
        "ban Steve",
        "ban-ip Steve",
    ]


def test_dashboard_poll_loop_refreshes_before_sleep(monkeypatch) -> None:
    home = _make_home(state="running")
    calls: list[str] = []

    def refresh_once() -> bool:
        calls.append("refresh")
        return False

    def fail_if_called(_seconds: float) -> None:
        raise AssertionError("dashboard loop should refresh before sleeping")

    home._dashboard_polling = True
    home._refresh_dashboard_snapshot = refresh_once
    monkeypatch.setattr("src.ui.pages.home.time.sleep", fail_if_called)

    home._dashboard_poll_loop()

    assert calls == ["refresh"]
    assert home._dashboard_polling is False


def test_home_shutdown_stops_background_refreshers() -> None:
    home = _make_home(state="running")
    home._dashboard_polling = True

    home.shutdown()

    assert home._dashboard_polling is False
    assert home._dashboard_stop_event.is_set() is True
    assert home.log_viewer.stop_count == 1


def test_dashboard_refresh_interval_is_short_for_stopped_metrics() -> None:
    assert DASHBOARD_REFRESH_INTERVAL_SECONDS <= 1.0


def test_dashboard_snapshot_stops_when_server_is_stopped() -> None:
    home = _make_home(state="stopped")

    should_continue = home._refresh_dashboard_snapshot()

    assert should_continue is False
    assert home.interfaces.system.pids == [4321]
    assert home.interfaces.player.calls == 0
    assert home.log_viewer.stop_count == 1


def test_dashboard_snapshot_keeps_polling_when_stopped_if_loop_is_active() -> None:
    home = _make_home(state="stopped")
    home._dashboard_polling = True

    should_continue = home._refresh_dashboard_snapshot()

    assert should_continue is True
    assert home.interfaces.system.pids == [4321]
    assert home.interfaces.player.calls == 0


def test_percent_formatter_does_not_hide_small_nonzero_values() -> None:
    assert _format_percent_value(0.0) == "0"
    assert _format_percent_value(0.04) == "<0.1"
    assert _format_percent_value(0.6) == "0.6"
    assert _format_percent_value(12.4) == "12"
