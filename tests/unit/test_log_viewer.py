from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from src.service.log_service import parse_log_line
from src.ui.components.log_viewer import (
    MAX_EVENTS_PER_REFRESH,
    MAX_LOG_ENTRIES,
    MAX_RENDERED_LOG_ROWS,
    DRAG_RELEASE_STALE_SECONDS,
    LOG_ROW_HEIGHT,
    LogViewer,
    _is_rcon_client_noise_event,
    _short_time,
)


class _PageStub:
    def __init__(self) -> None:
        self.update_count = 0

    def update(self) -> None:
        self.update_count += 1


class _PageTaskStub(_PageStub):
    def __init__(self) -> None:
        super().__init__()
        self.task_handler = None
        self.task_args = None

    def run_task(self, handler, *args, **kwargs):
        del kwargs
        self.task_handler = handler
        self.task_args = args
        return None


class _PageThreadStub(_PageTaskStub):
    def __init__(self) -> None:
        super().__init__()
        self.thread_handler = None
        self.thread_args = None

    def run_thread(self, handler, *args, **kwargs):
        del kwargs
        self.thread_handler = handler
        self.thread_args = args
        return None


class _TargetedPageStub:
    def __init__(self) -> None:
        self.updated_controls: list[tuple[object, ...]] = []

    def update(self, *controls: object) -> None:
        self.updated_controls.append(controls)


class _LogInterfaceStub:
    def __init__(self, events: list[dict]) -> None:
        self._events = events
        self.persist_calls: list[bool] = []
        self.prime_count = 0

    def tail_new_logs(self, persist: bool = True) -> list[dict]:
        self.persist_calls.append(persist)
        return self._events

    def prime_tail_to_end(self) -> None:
        self.prime_count += 1

    def load_recent_events(self, limit: int = 300) -> list[dict]:
        del limit
        return []


class _ServerInterfaceStub:
    def __init__(self, events: list[dict], state: str = "stopped") -> None:
        self._events = events
        self._state = state
        self.persist_calls: list[bool] = []

    def drain_stdout_events(self, persist: bool = True) -> list[dict]:
        self.persist_calls.append(persist)
        return self._events

    def get_server_status(self) -> dict:
        return {"state": self._state}


def test_log_viewer_collects_live_events_without_persisting() -> None:
    event = parse_log_line("[12:00:00 INFO]: Booting server")
    log_interface = _LogInterfaceStub([event])
    server_interface = _ServerInterfaceStub([])
    viewer = LogViewer(log_interface, _PageStub(), server_interface)

    events = viewer._collect_new_events()

    assert events == [event]
    assert log_interface.persist_calls == [False]
    assert server_interface.persist_calls == [False]


def test_log_viewer_prefers_flet_run_task_for_refresh_loop_when_available() -> None:
    page = _PageThreadStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)

    viewer.start_auto_refresh()

    assert page.task_handler == viewer._refresh_loop_async
    assert page.task_args == (viewer._refresh_generation,)
    assert page.thread_handler is None
    viewer.stop_auto_refresh()


def test_log_viewer_falls_back_to_flet_run_task_for_refresh_loop() -> None:
    page = _PageTaskStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)

    viewer.start_auto_refresh()

    assert page.task_handler == viewer._refresh_loop_async
    assert page.task_args == (viewer._refresh_generation,)
    viewer.stop_auto_refresh()


def test_log_viewer_restart_auto_refresh_replaces_stale_loop_generation() -> None:
    page = _PageThreadStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)

    viewer.start_auto_refresh()
    first_generation = page.task_args[0]
    viewer.restart_auto_refresh()

    assert page.task_handler == viewer._refresh_loop_async
    assert page.task_args[0] > first_generation


def test_log_viewer_tails_latest_log_and_stdout_while_server_is_active() -> None:
    stdout_event = parse_log_line("[12:00:00 INFO]: Booting server")
    file_event = parse_log_line("[12:00:01 INFO]: File log line")
    log_interface = _LogInterfaceStub([file_event])
    server_interface = _ServerInterfaceStub([stdout_event], state="starting")
    viewer = LogViewer(log_interface, _PageStub(), server_interface)

    events = viewer._collect_new_events()

    assert events == [file_event, stdout_event]
    assert log_interface.persist_calls == [False]
    assert log_interface.prime_count == 0
    assert server_interface.persist_calls == [False]


def test_log_viewer_tails_latest_log_when_active_stdout_is_quiet() -> None:
    file_event = parse_log_line("[12:00:01 INFO]: File log line")
    log_interface = _LogInterfaceStub([file_event])
    server_interface = _ServerInterfaceStub([], state="starting")
    viewer = LogViewer(log_interface, _PageStub(), server_interface)

    events = viewer._collect_new_events()

    assert events == [file_event]
    assert log_interface.persist_calls == [False]
    assert log_interface.prime_count == 0
    assert server_interface.persist_calls == [False]


def test_log_viewer_deduplicates_stdout_and_latest_log_events() -> None:
    raw_line = "[12:00:00 INFO]: Booting server"
    stdout_event = parse_log_line(raw_line)
    file_event = parse_log_line(raw_line)
    viewer = LogViewer(_LogInterfaceStub([file_event]), _PageStub(), _ServerInterfaceStub([stdout_event]))

    did_render = viewer._append_events(viewer._collect_new_events())

    assert did_render is True
    assert len(viewer.control.controls) == 1
    assert len(viewer.visible_events(limit=10)) == 1


def test_log_viewer_deduplicates_ansi_stdout_against_clean_log_file() -> None:
    stdout_event = parse_log_line("\x1b[32m[12:00:00 INFO]: Booting server\x1b[0m")
    file_event = parse_log_line("[12:00:00 INFO]: Booting server")
    viewer = LogViewer(_LogInterfaceStub([file_event]), _PageStub(), _ServerInterfaceStub([stdout_event]))

    did_render = viewer._append_events(viewer._collect_new_events())

    assert did_render is True
    assert len(viewer.control.controls) == 1
    assert len(viewer.visible_events(limit=10)) == 1


def test_log_viewer_keeps_filtered_out_live_events_for_later_refresh() -> None:
    event = parse_log_line("[12:00:00 INFO]: Booting server")
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer.selected_level = "ERROR"

    did_render = viewer._append_events([event])
    assert did_render is False

    viewer.selected_level = "ANY"
    viewer.refresh()
    assert len(viewer.control.controls) == 1


def test_live_stream_update_flushes_the_page_immediately() -> None:
    page = _TargetedPageStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)

    viewer._update_live_log_control()

    assert page.updated_controls == [()]


def test_live_stream_update_falls_back_for_simple_page_stubs() -> None:
    page = _PageStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)

    viewer._update_live_log_control()

    assert page.update_count == 1


def test_live_stream_flushes_once_per_accepted_log_batch() -> None:
    page = _PageStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)
    events = [
        parse_log_line("[12:00:00 INFO]: First line"),
        parse_log_line("[12:00:01 INFO]: Second line"),
    ]

    assert viewer._append_and_flush_events(events) is True

    assert page.update_count == 1
    assert [event["message"] for event in viewer.visible_events(limit=10)] == [
        "First line",
        "Second line",
    ]


def test_log_viewer_builds_only_retained_rows_for_a_large_burst() -> None:
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    built: list[dict] = []
    build_row = viewer._build_log_row

    def tracking_build_row(event: dict):
        built.append(event)
        return build_row(event)

    viewer._build_log_row = tracking_build_row
    total = MAX_RENDERED_LOG_ROWS + 75
    events = [
        parse_log_line(f"[12:00:00 INFO]: Burst line {index}")
        for index in range(total)
    ]

    assert viewer._append_events(events) is True
    assert len(built) == MAX_RENDERED_LOG_ROWS
    assert len(viewer.control.controls) == MAX_RENDERED_LOG_ROWS
    assert built[0]["message"] == f"Burst line {total - MAX_RENDERED_LOG_ROWS}"
    assert viewer.visible_events(limit=MAX_LOG_ENTRIES)[0]["message"] == "Burst line 0"


def test_log_viewer_coalesces_extreme_realtime_bursts() -> None:
    events = [
        parse_log_line(f"[12:00:00 INFO]: Burst line {index}")
        for index in range(MAX_EVENTS_PER_REFRESH + 20)
    ]
    viewer = LogViewer(
        _LogInterfaceStub([]),
        _PageStub(),
        _ServerInterfaceStub(events, state="running"),
    )

    viewer._append_events(viewer._collect_new_events())
    collected = viewer.visible_events(limit=MAX_EVENTS_PER_REFRESH + 1)

    assert len(collected) == MAX_EVENTS_PER_REFRESH
    assert collected[0]["level"] == "WARN"
    assert "已折叠较早的 21 条实时日志" in collected[0]["message"]
    assert collected[-1]["message"] == f"Burst line {MAX_EVENTS_PER_REFRESH + 19}"


def test_log_rows_use_lightweight_control_trees() -> None:
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())

    viewer._append_events([parse_log_line("[12:00:00 INFO]: Booting server")])

    assert _count_control_tree(viewer.control.controls[0]) <= 6


def test_log_row_scroll_area_does_not_start_selection() -> None:
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._append_events([parse_log_line("[12:00:00 INFO]: Booting server")])

    row = viewer.control.controls[0]
    selector, log_text = row.content.controls

    assert row.__class__.__name__ == "Container"
    assert selector.__class__.__name__ == "GestureDetector"
    assert log_text.__class__.__name__ == "Text"
    assert row.on_hover is None
    assert selector.on_tap is not None
    assert selector.on_tap_down is None
    assert selector.on_pan_down is None
    assert selector.on_pan_start is not None

    selector.on_enter(None)

    assert viewer.selection_count == 0

    selector.on_tap(None)

    assert viewer.selection_count == 1
    assert viewer.control.auto_scroll is True


def test_log_row_drag_selection_starts_after_pan_start() -> None:
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._append_events([parse_log_line("[12:00:00 INFO]: Booting server")])
    selector = viewer.control.controls[0].content.controls[0]

    selector.on_pan_start(None)

    assert viewer.is_drag_selecting is True
    assert viewer.selection_count == 1
    assert viewer.control.auto_scroll is False

    selector.on_pan_end(None)

    assert viewer.is_drag_selecting is False
    assert viewer.control.auto_scroll is True


def test_log_row_drag_selection_still_starts_on_pan_update() -> None:
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._append_events([parse_log_line("[12:00:00 INFO]: Booting server")])
    selector = viewer.control.controls[0].content.controls[0]

    selector.on_pan_update(None)

    assert viewer.is_drag_selecting is True
    assert viewer.selection_count == 1
    assert viewer.control.auto_scroll is False

    selector.on_pan_end(None)

    assert viewer.is_drag_selecting is False
    assert viewer.control.auto_scroll is True


def test_log_row_release_fallback_handlers_end_drag_selection() -> None:
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._append_events([parse_log_line("[12:00:00 INFO]: Booting server")])
    selector = viewer.control.controls[0].content.controls[0]

    selector.on_pan_start(None)
    selector.on_long_press_end(None)

    assert viewer.is_drag_selecting is False
    assert viewer.control.auto_scroll is True

    selector.on_pan_start(None)
    selector.on_tap_up(None)

    assert viewer.is_drag_selecting is False
    assert viewer.control.auto_scroll is True


def test_select_visible_selects_only_the_rendered_log_window() -> None:
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._append_events(
        [
            parse_log_line(f"[12:00:00 INFO]: Burst line {index}")
            for index in range(MAX_RENDERED_LOG_ROWS + 20)
        ]
    )

    viewer.select_visible()

    assert viewer.selection_count == MAX_RENDERED_LOG_ROWS


def test_selected_events_preserve_log_order() -> None:
    events = [
        parse_log_line("[12:00:00 INFO]: First line"),
        parse_log_line("[12:00:01 INFO]: Second line"),
        parse_log_line("[12:00:02 INFO]: Third line"),
    ]
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._render_events(events)

    viewer._toggle_selection(events[2])
    viewer._toggle_selection(events[0])

    assert [event["message"] for event in viewer.get_selected_events()] == [
        "First line",
        "Third line",
    ]


def test_log_viewer_reuses_rows_which_remain_in_the_live_window() -> None:
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._append_events(
        [parse_log_line(f"[12:00:00 INFO]: Existing line {index}") for index in range(3)]
    )
    retained_control = viewer.control.controls[1]

    viewer._append_events([parse_log_line("[12:00:01 INFO]: New line")])

    assert viewer.control.controls[1] is retained_control


def test_filtered_row_disappears_when_it_falls_out_of_the_live_window() -> None:
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer.selected_level = "ERROR"
    viewer._append_events([parse_log_line("[12:00:00 ERROR]: Initial error")])

    did_render = viewer._append_events(
        [parse_log_line(f"[12:00:01 INFO]: Noise {index}") for index in range(MAX_LOG_ENTRIES)]
    )

    assert did_render is True
    assert viewer.visible_events(limit=10) == []
    assert len(viewer.control.controls) == 1


def _count_control_tree(control: object) -> int:
    total = 1
    content = getattr(control, "content", None)
    if content is not None:
        total += _count_control_tree(content)
    for child in getattr(control, "controls", []) or []:
        total += _count_control_tree(child)
    return total


def _row_checkbox_icon(row: object) -> ft.Icons:
    selector = row.content.controls[0]
    return selector.content.content.icon


def test_log_viewer_filters_rcon_client_noise_from_ui() -> None:
    events = [
        parse_log_line("[12:27:13 INFO]: Thread RCON Client /127.0.0.1 started"),
        parse_log_line("[12:27:13 INFO]: Thread RCON Client /127.0.0.1 shutting down"),
        parse_log_line("[12:27:14 INFO]: Booting server"),
    ]
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())

    did_render = viewer._append_events(events)

    assert did_render is True
    assert [event["message"] for event in viewer.visible_events(limit=10)] == ["Booting server"]
    assert len(viewer.control.controls) == 1


def test_log_viewer_filters_rcon_client_noise_from_initial_render() -> None:
    events = [
        parse_log_line("[12:27:13 INFO]: Thread RCON Client /127.0.0.1 started"),
        parse_log_line("[12:27:14 INFO]: Booting server"),
    ]
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())

    viewer._render_events(events)

    assert [event["message"] for event in viewer.visible_events(limit=10)] == ["Booting server"]
    assert len(viewer.control.controls) == 1


def test_rcon_client_noise_matcher_is_specific() -> None:
    assert _is_rcon_client_noise_event(
        parse_log_line("[12:27:13 INFO]: Thread RCON Client /127.0.0.1 started")
    )
    assert _is_rcon_client_noise_event(
        parse_log_line("[12:27:13 INFO]: Thread RCON Client /127.0.0.1 shutting down")
    )
    assert not _is_rcon_client_noise_event(
        parse_log_line("[12:27:13 INFO]: RCON command executed by operator")
    )


def test_log_viewer_marks_stack_trace_lines_as_error_continuations() -> None:
    events = [
        parse_log_line("2026-05-20 00:03:46,778 ServerMain ERROR Failed to start the minecraft server"),
        parse_log_line("java.io.IOException: 另一个程序已锁定文件的一部分，进程无法访问。"),
        parse_log_line("\tat sun.nio.ch.FileDispatcherImpl.write0(Native Method) ~[?:?]"),
    ]
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())

    did_render = viewer._append_events(events)

    visible = viewer.visible_events(limit=10)
    assert did_render is True
    assert [event["level"] for event in visible] == ["ERROR", "ERROR", "ERROR"]
    assert [event["event_time"] for event in visible] == ["00:03:46", "00:03:46", "00:03:46"]


def test_log_viewer_updates_dragged_rows_immediately() -> None:
    events = [
        parse_log_line("[12:00:00 INFO]: Booting server"),
        parse_log_line("[12:00:01 WARN]: Can't keep up"),
    ]
    page = _PageStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)
    viewer._render_events(events)
    controls_before = list(viewer.control.controls)

    viewer._start_drag_select(events[0])
    viewer._drag_over_event(events[1])

    assert viewer.is_drag_selecting is True
    assert list(viewer.control.controls) == controls_before
    assert page.update_count == 2
    assert _row_checkbox_icon(viewer.control.controls[0]) == ft.Icons.CHECK_BOX
    assert _row_checkbox_icon(viewer.control.controls[1]) == ft.Icons.CHECK_BOX
    assert viewer.control.controls[0].data["selected"] is True
    assert viewer.control.controls[1].data["selected"] is True

    viewer._end_drag_select()

    assert viewer.is_drag_selecting is False
    assert viewer.selection_count == 2


def test_log_viewer_drag_update_position_selects_target_row() -> None:
    events = [
        parse_log_line("[12:00:00 INFO]: Booting server"),
        parse_log_line("[12:00:01 WARN]: Can't keep up"),
    ]
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._render_events(events)

    viewer.control.controls[0].content.controls[0].on_pan_start(None)
    viewer.control.controls[0].content.controls[0].on_pan_update(SimpleNamespace(local_y=39))

    assert viewer.selection_count == 2
    assert _row_checkbox_icon(viewer.control.controls[1]) == ft.Icons.CHECK_BOX


def test_log_viewer_drag_select_fills_rows_skipped_by_pointer_updates() -> None:
    events = [
        parse_log_line(f"[12:00:0{index} INFO]: Line {index}")
        for index in range(5)
    ]
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._render_events(events)

    viewer._start_drag_select(events[0])
    viewer._drag_update_position(events[0], SimpleNamespace(local_y=LOG_ROW_HEIGHT * 4))

    assert viewer.selection_count == 5
    assert [_row_checkbox_icon(row) for row in viewer.control.controls] == [
        ft.Icons.CHECK_BOX,
        ft.Icons.CHECK_BOX,
        ft.Icons.CHECK_BOX,
        ft.Icons.CHECK_BOX,
        ft.Icons.CHECK_BOX,
    ]


def test_log_viewer_drag_scroll_extends_selection_one_row_at_a_time() -> None:
    events = [
        parse_log_line(f"[12:00:{index:02d} INFO]: Line {index}")
        for index in range(4)
    ]
    page = _PageTaskStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)
    viewer._render_events(events)

    viewer._start_drag_select(events[1])
    viewer._scroll_drag_once("down")
    viewer._scroll_drag_once("down")

    assert [event["message"] for event in viewer.get_selected_events()] == [
        "Line 1",
        "Line 2",
        "Line 3",
    ]
    assert page.task_handler == viewer._scroll_by_async


def test_log_viewer_stale_drag_session_does_not_start_edge_scroll() -> None:
    events = [
        parse_log_line("[12:00:00 INFO]: Line 0"),
        parse_log_line("[12:00:01 INFO]: Line 1"),
    ]
    page = _PageTaskStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)
    viewer._render_events(events)

    viewer._start_drag_select(events[0])
    viewer._drag_last_activity_at -= DRAG_RELEASE_STALE_SECONDS + 0.1
    viewer.start_drag_scroll("down")

    assert viewer.is_drag_selecting is False
    assert viewer.control.auto_scroll is True
    assert viewer._scroll_zone is None
    assert page.task_handler is None


def test_log_viewer_hover_does_not_keep_stale_drag_session_alive() -> None:
    events = [
        parse_log_line("[12:00:00 INFO]: Line 0"),
        parse_log_line("[12:00:01 INFO]: Line 1"),
    ]
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._render_events(events)

    viewer._start_drag_select(events[0])
    viewer._drag_last_activity_at -= DRAG_RELEASE_STALE_SECONDS + 0.1
    viewer._drag_over_event(events[1])

    assert viewer.is_drag_selecting is False
    assert viewer.selection_count == 1
    assert _row_checkbox_icon(viewer.control.controls[1]) == ft.Icons.CHECK_BOX_OUTLINE_BLANK


def test_log_viewer_freezes_live_rows_during_drag_and_flushes_after_release() -> None:
    initial_events = [
        parse_log_line("[12:00:00 INFO]: Line 0"),
        parse_log_line("[12:00:01 INFO]: Line 1"),
    ]
    pending_events = [
        parse_log_line("[12:00:02 INFO]: Line 2"),
        parse_log_line("[12:00:03 INFO]: Line 3"),
    ]
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._render_events(initial_events)
    controls_before = list(viewer.control.controls)

    viewer._start_drag_select(initial_events[0])

    assert viewer._append_events(pending_events) is False
    assert viewer.visible_events(limit=10) == initial_events
    assert list(viewer.control.controls) == controls_before

    viewer._end_drag_select()

    assert viewer.is_drag_selecting is False
    assert [event["message"] for event in viewer.visible_events(limit=10)] == [
        "Line 0",
        "Line 1",
        "Line 2",
        "Line 3",
    ]


def test_log_viewer_drag_does_not_toggle_anchor_back_off_on_tap_cleanup() -> None:
    event = parse_log_line("[12:00:00 INFO]: Booting server")
    viewer = LogViewer(_LogInterfaceStub([]), _PageStub())
    viewer._render_events([event])
    selector = viewer.control.controls[0].content.controls[0]

    selector.on_pan_start(None)
    selector.on_pan_end(None)
    selector.on_tap(None)

    assert viewer.selection_count == 1
    assert _row_checkbox_icon(viewer.control.controls[0]) == ft.Icons.CHECK_BOX


def test_log_viewer_scroll_uses_page_task() -> None:
    page = _PageTaskStub()
    viewer = LogViewer(_LogInterfaceStub([]), page)

    viewer._scroll_by(72)

    assert page.task_handler == viewer._scroll_by_async


def test_short_time_converts_iso_timestamp_to_local_time() -> None:
    assert len(_short_time("2026-05-19T16:03:46+00:00")) == 8
