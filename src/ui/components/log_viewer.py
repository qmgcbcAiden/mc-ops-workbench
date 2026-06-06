from __future__ import annotations

import asyncio
import inspect
import re
import threading
import time
from concurrent.futures import Future
from datetime import datetime

import flet as ft

from src.interface.log_interface import LogInterface
from src.service.log_service import make_overflow_event
from src.ui import theme


MAX_LOG_ENTRIES = 500
MAX_RENDERED_LOG_ROWS = 180
MAX_EVENTS_PER_REFRESH = MAX_LOG_ENTRIES
LOG_ROW_HEIGHT = 38
LOG_ROW_SELECTED_BG = "#101d2c"
LOG_POLL_INTERVAL_SECONDS = 0.08
DRAG_TAP_SUPPRESS_SECONDS = 0.25
DRAG_SCROLL_DELTA = LOG_ROW_HEIGHT
DRAG_SCROLL_DURATION_MS = 140
DRAG_SCROLL_INTERVAL_SECONDS = 0.16
DRAG_SCROLL_SELECT_ROWS_PER_TICK = 1
DRAG_RELEASE_STALE_SECONDS = 0.55
_RCON_CLIENT_NOISE_RE = re.compile(
    r"Thread RCON Client\s+/[^ ]+\s+(?:started|shutting down)\s*$",
    re.IGNORECASE,
)
_LEVEL_BUTTON_WIDTHS = {
    "ANY": 52,
    "INFO": 58,
    "WARN": 64,
    "ERROR": 72,
}


class LogViewer:
    def __init__(self, log_interface: LogInterface, page: ft.Page, server_interface=None) -> None:
        self._interface = log_interface
        self._page = page
        self._server_interface = server_interface
        self._log_list = ft.ListView(
            expand=True,
            spacing=0,
            padding=0,
            auto_scroll=True,
            item_extent=LOG_ROW_HEIGHT,
            cache_extent=LOG_ROW_HEIGHT * 6,
            semantic_child_count=MAX_RENDERED_LOG_ROWS,
        )
        self._running = False
        self._thread: threading.Thread | None = None
        self._task: Future | None = None
        self._refresh_generation = 0
        self._lock = threading.Lock()
        self._update_lock = threading.Lock()
        self._showing_empty_state = False
        self._events: list[dict] = []
        self._seen_event_keys: set[str] = set()
        self._rendered_event_keys: list[str] = []
        self._last_log_context: dict[str, str | None] | None = None

        self.level_buttons: dict[str, ft.TextButton] = {}
        self.selected_level = "ANY"
        self.keyword = ""

        self._selected_keys: set[str] = set()
        self._selected_events: dict[str, dict] = {}
        self._on_ask_ai: object = None
        self._drag_selecting = False
        self._drag_anchor_key: str | None = None
        self._drag_anchor_index: int | None = None
        self._drag_last_key: str | None = None
        self._drag_last_index: int | None = None
        self._drag_changed = False
        self._drag_last_activity_at = 0.0
        self._pending_drag_events: list[dict] = []
        self._suppress_tap_key: str | None = None
        self._suppress_tap_deadline = 0.0
        self._scroll_zone: str | None = None
        self._scroll_thread: threading.Thread | None = None

        self._select_all_btn = ft.IconButton(
            icon=ft.Icons.SELECT_ALL,
            icon_color=theme.TEXT,
            icon_size=17,
            width=30,
            height=30,
            tooltip="全选",
            on_click=lambda _: self.select_visible(),
        )
        self._invert_sel_btn = ft.IconButton(
            icon=ft.Icons.FLIP_TO_BACK,
            icon_color=theme.TEXT,
            icon_size=17,
            width=30,
            height=30,
            tooltip="反选",
            on_click=lambda _: self.invert_visible(),
        )
        self._ask_ai_btn = ft.IconButton(
            icon=ft.Icons.QUESTION_ANSWER,
            icon_color=theme.TEXT,
            icon_size=17,
            width=30,
            height=30,
            tooltip="问助手",
            disabled=True,
            on_click=lambda _: self._trigger_ask_ai(),
        )
        self._clear_sel_btn = ft.IconButton(
            icon=ft.Icons.CLEAR_ALL,
            icon_color=theme.TEXT,
            icon_size=17,
            width=30,
            height=30,
            tooltip="清除选择",
            disabled=True,
            on_click=lambda _: self.clear_selection(),
        )

    @property
    def control(self) -> ft.ListView:
        return self._log_list

    @property
    def selection_count(self) -> int:
        return len(self._selected_keys)

    @property
    def filter_row_width(self) -> int:
        return sum(_level_button_width(level) for level in ("ANY", "INFO", "WARN", "ERROR"))

    def set_on_ask_ai(self, callback: object) -> None:
        self._on_ask_ai = callback

    def get_selected_events(self) -> list[dict]:
        selected: list[dict] = []
        seen: set[str] = set()
        with self._lock:
            for event in self._events:
                key = _event_key(event)
                if key in self._selected_keys and key in self._selected_events:
                    selected.append(self._selected_events[key])
                    seen.add(key)
            for key in self._selected_keys:
                if key not in seen and key in self._selected_events:
                    selected.append(self._selected_events[key])
        return selected

    def get_selected_raw_logs(self) -> dict:
        selected = self.get_selected_events()
        if not selected:
            return {"raw_text": "", "label": "", "line_count": 0, "source": "", "time_range": "", "event_ids": []}
        raw_lines = [item.get("raw_line", item.get("message", "")) for item in selected]
        times = [item.get("event_time", "") for item in selected]
        event_ids = [item.get("event_id") for item in selected if item.get("event_id")]
        source = selected[0].get("source", "latest.log")
        first_time = times[0] if times else ""
        last_time = times[-1] if times else ""
        return {
            "raw_text": "\n".join(raw_lines),
            "label": f"日志片段 {source} · {len(selected)} 行",
            "line_count": len(selected),
            "source": source,
            "time_range": f"{first_time}-{last_time}" if first_time and last_time else "",
            "event_ids": [event_id for event_id in event_ids if event_id],
        }

    def clear_selection(self) -> None:
        self._selected_keys.clear()
        self._selected_events.clear()
        self._update_selection_ui()
        self.refresh()
        self._update_live_log_control()

    def select_visible(self) -> None:
        with self._lock:
            for event in self._rendered_events_locked():
                self._select_event_locked(event)
        self._update_selection_ui()
        self.refresh()
        self._update_live_log_control()

    def invert_visible(self) -> None:
        with self._lock:
            for event in self._rendered_events_locked():
                key = _event_key(event)
                if key in self._selected_keys:
                    self._selected_keys.discard(key)
                    self._selected_events.pop(key, None)
                else:
                    self._select_event_locked(event)
        self._update_selection_ui()
        self.refresh()
        self._update_live_log_control()

    def build_filter_row(self) -> ft.Row:
        return ft.Row(
            controls=[
                self._make_level_button("ANY", "全部"),
                self._make_level_button("INFO", "INFO"),
                self._make_level_button("WARN", "WARN"),
                self._make_level_button("ERROR", "ERROR"),
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def build_selection_row(self) -> ft.Row:
        return ft.Row(
            controls=[
                self._select_all_btn,
                self._invert_sel_btn,
                self._ask_ai_btn,
                self._clear_sel_btn,
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def load_initial(self) -> None:
        self._render_events(self._interface.load_recent_events(limit=300))

    def clear(self, prime_tail: bool = False) -> None:
        if prime_tail:
            self._interface.prime_tail_to_end()
        with self._lock:
            self._selected_keys.clear()
            self._selected_events.clear()
            self._events = []
            self._seen_event_keys = set()
            self._rendered_event_keys = []
            self._last_log_context = None
            self._log_list.controls = []
            self._showing_empty_state = False
        self._update_selection_ui()

    def refresh(self) -> None:
        with self._lock:
            self._render_events_locked(self._events)

    def refresh_live_once(self) -> bool:
        try:
            return self._append_and_flush_events(self._collect_new_events())
        except Exception:
            return False

    def visible_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return self._visible_events_locked()[-max(0, limit):]

    def start_auto_refresh(self, force: bool = False) -> None:
        if self._running and not force:
            return
        if force:
            self._refresh_generation += 1
        self._running = True
        self._refresh_generation += 1
        generation = self._refresh_generation
        if hasattr(self._page, "run_task"):
            self._task = self._page.run_task(self._refresh_loop_async, generation)
            return
        if hasattr(self._page, "run_thread"):
            self._thread = self._page.run_thread(self._refresh_loop, generation)
            return
        self._thread = threading.Thread(target=self._refresh_loop, args=(generation,), daemon=True)
        self._thread.start()

    def restart_auto_refresh(self) -> None:
        self.start_auto_refresh(force=True)

    def stop_auto_refresh(self) -> None:
        self._running = False
        self._refresh_generation += 1

    @property
    def is_drag_selecting(self) -> bool:
        return self._drag_selecting

    def end_drag_select(self) -> None:
        self._end_drag_select()

    def append_sample(self) -> dict:
        event = self._interface.append_sample_log()
        self._append_events([event])
        return event

    def _visible_events_locked(self) -> list[dict]:
        return [event for event in self._events if _matches_filter(event, self.selected_level, self.keyword)]

    def _rendered_events_locked(self) -> list[dict]:
        return self._visible_events_locked()[-MAX_RENDERED_LOG_ROWS:]

    def _select_event_locked(self, event: dict) -> None:
        key = _event_key(event)
        self._selected_keys.add(key)
        self._selected_events[key] = dict(event)

    def _set_event_selected_locked(self, event: dict, selected: bool) -> bool:
        key = _event_key(event)
        if selected:
            was_selected = key in self._selected_keys
            self._select_event_locked(event)
            return not was_selected
        if key not in self._selected_keys:
            return False
        self._selected_keys.discard(key)
        self._selected_events.pop(key, None)
        return True

    def _trigger_ask_ai(self) -> None:
        if self._on_ask_ai and callable(self._on_ask_ai):
            self._on_ask_ai()

    def _toggle_selection(self, event: dict) -> None:
        key = _event_key(event)
        if self._should_suppress_tap(key):
            return
        with self._lock:
            checked = key not in self._selected_keys
            changed = self._set_event_selected_locked(event, checked)
        if not changed:
            return
        self._update_selection_ui()
        if not self._sync_rendered_row_selection(key, checked):
            self.refresh()
            self._update_live_log_control()

    def _start_drag_select(self, event: dict) -> None:
        key = _event_key(event)
        if self._drag_selecting and self._drag_anchor_key == key:
            return
        self._drag_selecting = True
        self._drag_anchor_key = key
        self._drag_anchor_index = self._rendered_event_index(key)
        self._drag_last_key = None
        self._drag_last_index = None
        self._drag_changed = False
        self._touch_drag_activity()
        self._log_list.auto_scroll = False
        changed_keys = self._drag_select_to_key(key, fallback_event=event)
        if changed_keys:
            self._drag_changed = True
            self._update_selection_ui()
            self._sync_rendered_rows_selection(changed_keys, True)
        else:
            self._sync_rendered_row_selection(key, True, update=False)

    def _drag_over_event(self, event: dict) -> None:
        if not self._drag_selecting:
            return
        if not self._drag_session_is_fresh():
            self._end_drag_select()
            return
        changed_keys = self._drag_select_to_key(_event_key(event), fallback_event=event)
        if changed_keys:
            self._drag_changed = True
            self._update_selection_ui()
            self._sync_rendered_rows_selection(changed_keys, True)

    def _drag_update_position(self, anchor_event: dict, drag_event: object = None) -> None:
        if not self._drag_selecting:
            self._start_drag_select(anchor_event)
        if not self._drag_selecting:
            return
        self._touch_drag_activity()
        target_index = self._drag_target_index_from_pointer(drag_event)
        if target_index is None:
            self._drag_over_event(anchor_event)
            return
        changed_keys = self._drag_select_to_index(target_index)
        if changed_keys:
            self._drag_changed = True
            self._update_selection_ui()
            self._sync_rendered_rows_selection(changed_keys, True)

    def _end_drag_select(self) -> None:
        if not self._drag_selecting:
            return
        anchor_key = self._drag_anchor_key
        self._drag_selecting = False
        self._drag_anchor_key = None
        self._drag_anchor_index = None
        self._drag_last_key = None
        self._drag_last_index = None
        self._drag_last_activity_at = 0.0
        self.stop_drag_scroll()
        self._log_list.auto_scroll = True
        if anchor_key:
            self._suppress_tap_key = anchor_key
            self._suppress_tap_deadline = time.monotonic() + DRAG_TAP_SUPPRESS_SECONDS
        changed = self._drag_changed
        pending_events = self._take_pending_drag_events()
        did_append_pending = self._append_events(pending_events) if pending_events else False
        if changed:
            self._drag_changed = False
            if not did_append_pending:
                self.refresh()
        if changed or did_append_pending:
            self._update_live_log_control()

    def _should_suppress_tap(self, key: str) -> bool:
        now = time.monotonic()
        if self._suppress_tap_key and now > self._suppress_tap_deadline:
            self._suppress_tap_key = None
            self._suppress_tap_deadline = 0.0
            return False
        if key != self._suppress_tap_key:
            return False
        self._suppress_tap_key = None
        self._suppress_tap_deadline = 0.0
        return True

    def _drag_select_to_key(self, key: str, fallback_event: dict | None = None) -> list[str]:
        target_index = self._rendered_event_index(key)
        if target_index is None:
            return self._select_fallback_drag_event(key, fallback_event)
        return self._drag_select_to_index(target_index)

    def _select_fallback_drag_event(self, key: str, fallback_event: dict | None) -> list[str]:
        if fallback_event is None:
            return []
        changed_keys: list[str] = []
        with self._lock:
            if self._set_event_selected_locked(fallback_event, True):
                changed_keys.append(key)
        self._drag_last_key = key
        return changed_keys

    def _drag_select_to_index(self, target_index: int) -> list[str]:
        changed_keys: list[str] = []
        with self._lock:
            visible_events = self._rendered_events_locked()
            if not visible_events:
                return []
            target_index = max(0, min(target_index, len(visible_events) - 1))
            start_index = self._drag_last_index
            if start_index is None:
                start_index = self._drag_anchor_index
            if start_index is None:
                start_index = target_index

            start = min(start_index, target_index)
            end = max(start_index, target_index)
            target_events = visible_events[start:end + 1]
            self._drag_last_index = target_index
            self._drag_last_key = _event_key(visible_events[target_index])
            for target in target_events:
                if self._set_event_selected_locked(target, True):
                    changed_keys.append(_event_key(target))
        return changed_keys

    def _rendered_event_index(self, key: str) -> int | None:
        try:
            return self._rendered_event_keys.index(key)
        except ValueError:
            return None

    def _drag_target_index_from_pointer(self, event: object = None) -> int | None:
        anchor_index = self._drag_anchor_index
        if anchor_index is None:
            return None
        y = _event_local_y(event)
        if y is None:
            return None
        row_delta = int(y // LOG_ROW_HEIGHT)
        return anchor_index + row_delta

    def _extend_drag_selection_for_scroll(self, direction: str) -> list[str]:
        if direction not in {"up", "down"}:
            return []
        changed_keys: list[str] = []
        with self._lock:
            visible_events = self._rendered_events_locked()
            if not visible_events:
                return []
            last_index = self._drag_last_index
            if last_index is None:
                last_index = self._drag_anchor_index
            if last_index is None:
                last_index = len(visible_events) - 1 if direction == "up" else 0
            last_index = max(0, min(last_index, len(visible_events) - 1))

            step = max(1, DRAG_SCROLL_SELECT_ROWS_PER_TICK)
            if direction == "up":
                next_index = max(0, last_index - step)
                if next_index == last_index:
                    return []
                target_events = visible_events[next_index:last_index]
            else:
                next_index = min(len(visible_events) - 1, last_index + step)
                if next_index == last_index:
                    return []
                target_events = visible_events[last_index + 1:next_index + 1]

            self._drag_last_key = _event_key(visible_events[next_index])
            self._drag_last_index = next_index
            for target in target_events:
                if self._set_event_selected_locked(target, True):
                    changed_keys.append(_event_key(target))

        if changed_keys:
            self._drag_changed = True
            self._update_selection_ui()
            self._sync_rendered_rows_selection(changed_keys, True)
        return changed_keys

    def _sync_rendered_row_selection(self, key: str, checked: bool, update: bool = True) -> bool:
        control = self._rendered_row_control(key)
        if control is None:
            return False
        _set_log_row_selection_visual(control, checked)
        if update:
            self._flush_controls(control)
        return True

    def _sync_rendered_rows_selection(self, keys: list[str], checked: bool) -> bool:
        controls: list[ft.Control] = []
        for key in keys:
            control = self._rendered_row_control(key)
            if control is None:
                continue
            _set_log_row_selection_visual(control, checked)
            controls.append(control)
        if not controls:
            return False
        self._flush_controls(*controls)
        return True

    def _rendered_row_control(self, key: str) -> ft.Control | None:
        try:
            index = self._rendered_event_keys.index(key)
        except ValueError:
            return None
        if index >= len(self._log_list.controls):
            return None
        return self._log_list.controls[index]

    def _flush_controls(self, *controls: ft.Control) -> None:
        did_update = False
        for control in controls:
            try:
                if getattr(control, "page", None):
                    control.update()
                    did_update = True
            except RuntimeError:
                pass
            except Exception:
                pass
        if did_update:
            return
        try:
            if controls:
                self._page.update(*controls)
                return
        except TypeError:
            pass
        except Exception:
            pass
        self._update_live_log_control()

    def _auto_scroll_from_drag(self, event: object) -> None:
        y = _event_local_y(event)
        if y is None:
            return
        if y < 6:
            self._scroll_drag_once("up")
        elif y > LOG_ROW_HEIGHT - 6:
            self._scroll_drag_once("down")

    def start_drag_scroll(self, direction: str) -> None:
        if not self._drag_selecting:
            return
        if not self._drag_session_is_fresh():
            self._end_drag_select()
            return
        if direction not in {"up", "down"}:
            return
        self._scroll_zone = direction
        if self._scroll_thread and self._scroll_thread.is_alive():
            return
        self._scroll_thread = threading.Thread(target=self._drag_scroll_loop, daemon=True)
        self._scroll_thread.start()

    def stop_drag_scroll(self) -> None:
        self._scroll_zone = None

    def _drag_scroll_loop(self) -> None:
        while self._drag_selecting and self._scroll_zone:
            if not self._drag_session_is_fresh():
                self._end_drag_select()
                break
            self._scroll_drag_once(self._scroll_zone)
            time.sleep(DRAG_SCROLL_INTERVAL_SECONDS)

    def _scroll_drag_once(self, direction: str) -> None:
        if direction not in {"up", "down"}:
            return
        if not self._drag_session_is_fresh():
            self._end_drag_select()
            return
        delta = -DRAG_SCROLL_DELTA if direction == "up" else DRAG_SCROLL_DELTA
        self._scroll_by(delta, duration=DRAG_SCROLL_DURATION_MS)
        self._extend_drag_selection_for_scroll(direction)

    def _touch_drag_activity(self) -> None:
        self._drag_last_activity_at = time.monotonic()

    def _drag_session_is_fresh(self) -> bool:
        if not self._drag_selecting:
            return False
        if self._drag_last_activity_at <= 0:
            return False
        return (time.monotonic() - self._drag_last_activity_at) <= DRAG_RELEASE_STALE_SECONDS

    def _scroll_by(self, delta: int, duration: int = 90) -> None:
        try:
            if hasattr(self._page, "run_task"):
                self._page.run_task(self._scroll_by_async, delta, duration)
                return
            result = self._log_list.scroll_to(delta=delta, duration=duration)
            if inspect.isawaitable(result):
                asyncio.run(result)
        except Exception:
            pass

    async def _scroll_by_async(self, delta: int, duration: int) -> None:
        try:
            result = self._log_list.scroll_to(delta=delta, duration=duration)
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass

    def _update_selection_ui(self) -> None:
        has_selection = bool(self._selected_keys)
        self._ask_ai_btn.disabled = not has_selection
        self._clear_sel_btn.disabled = not has_selection
        for control in (self._ask_ai_btn, self._clear_sel_btn):
            if hasattr(control, "update"):
                try:
                    control.update()
                except RuntimeError:
                    pass

    def _make_level_button(self, level: str, label: str) -> ft.TextButton:
        button = ft.TextButton(
            content=label,
            style=self._level_button_style(level == self.selected_level),
            width=_level_button_width(level),
            height=30,
            on_click=lambda _: self._on_level_filter(level),
        )
        self.level_buttons[level] = button
        return button

    def _level_button_style(self, active: bool) -> ft.ButtonStyle:
        return ft.ButtonStyle(
            bgcolor=theme.BLUE_SOFT if active else theme.INPUT_BG,
            color=theme.BLUE if active else theme.MUTED,
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=ft.Padding.symmetric(horizontal=4, vertical=5),
        )

    def _on_level_filter(self, level: str) -> None:
        self.selected_level = level
        for key, button in self.level_buttons.items():
            button.style = self._level_button_style(key == level)
        self.refresh()
        self._update_filter_buttons()
        self._update_live_log_control()

    def _refresh_loop(self, generation: int | None = None) -> None:
        while self._running and self._is_current_refresh_generation(generation):
            try:
                self._append_and_flush_events(self._collect_new_events())
            except Exception:
                pass
            time.sleep(LOG_POLL_INTERVAL_SECONDS)

    async def _refresh_loop_async(self, generation: int | None = None) -> None:
        while self._running and self._is_current_refresh_generation(generation):
            try:
                self._append_and_flush_events(self._collect_new_events())
            except Exception:
                pass
            await asyncio.sleep(LOG_POLL_INTERVAL_SECONDS)

    def _is_current_refresh_generation(self, generation: int | None) -> bool:
        return generation is None or generation == self._refresh_generation

    def _append_and_flush_events(self, events: list[dict]) -> bool:
        if not self._append_events(events):
            return False
        self._update_live_log_control()
        return True

    def _update_live_log_control(self) -> bool:
        if not self._update_lock.acquire(blocking=False):
            return False
        try:
            self._page.update()
            return True
        finally:
            self._update_lock.release()

    def _update_filter_buttons(self) -> None:
        for button in self.level_buttons.values():
            try:
                if getattr(button, "page", None):
                    button.update()
            except RuntimeError:
                pass

    def _collect_new_events(self) -> list[dict]:
        stdout_events: list[dict] = []
        if self._server_interface is not None:
            try:
                stdout_events = self._server_interface.drain_stdout_events(persist=False)
            except Exception:
                pass
        try:
            file_events = self._tail_new_logs_limited()
        except Exception:
            file_events = []
        return [*file_events, *stdout_events]

    def _tail_new_logs_limited(self) -> list[dict]:
        try:
            return self._interface.tail_new_logs(
                persist=False,
                limit=None,
            )
        except TypeError:
            return self._interface.tail_new_logs(persist=False)

    def _append_events(self, events: list[dict]) -> bool:
        if not events:
            return False
        with self._lock:
            if self._drag_selecting:
                self._queue_pending_drag_events_locked(events)
                return False
            filtered_events = _filter_ui_noise_events(events)
            normalized_events = self._apply_log_context_locked(filtered_events)
            accepted_events = self._dedupe_events_locked(normalized_events)
            if not accepted_events:
                return False
            accepted_events = _coalesce_event_burst(accepted_events)
            self._events.extend(accepted_events)
            self._trim_events_locked()
            return self._sync_visible_rows_locked()

    def _queue_pending_drag_events_locked(self, events: list[dict]) -> None:
        self._pending_drag_events.extend(dict(event) for event in events)
        if len(self._pending_drag_events) > MAX_LOG_ENTRIES:
            self._pending_drag_events = self._pending_drag_events[-MAX_LOG_ENTRIES:]

    def _take_pending_drag_events(self) -> list[dict]:
        with self._lock:
            pending_events = self._pending_drag_events
            self._pending_drag_events = []
        return pending_events

    def _render_events(self, events: list[dict]) -> None:
        with self._lock:
            self._render_events_locked(events)

    def _render_events_locked(self, events: list[dict]) -> None:
        self._last_log_context = None
        filtered_events = _filter_ui_noise_events(events)
        self._events = self._apply_log_context_locked(list(filtered_events[-MAX_LOG_ENTRIES:]))
        self._seen_event_keys = {_event_key(event) for event in self._events}
        visible_events = self._rendered_events_locked()
        if self._events and not visible_events:
            self._log_list.controls = [_empty_state("没有匹配的日志")]
            self._rendered_event_keys = []
            self._showing_empty_state = True
            return
        if not visible_events:
            self._log_list.controls = [_empty_state("等待服务器生成 latest.log")]
            self._rendered_event_keys = []
            self._showing_empty_state = True
            return
        self._log_list.controls = [self._build_log_row(event) for event in visible_events]
        self._rendered_event_keys = [_event_key(event) for event in visible_events]
        self._showing_empty_state = False

    def _sync_visible_rows_locked(self) -> bool:
        visible_events = self._rendered_events_locked()
        target_keys = [_event_key(event) for event in visible_events]
        if target_keys == self._rendered_event_keys and not self._showing_empty_state:
            return False
        if not visible_events:
            text = "没有匹配的日志" if self._events else "等待服务器生成 latest.log"
            self._log_list.controls = [_empty_state(text)]
            self._rendered_event_keys = []
            self._showing_empty_state = True
            return True

        existing_controls = (
            dict(zip(self._rendered_event_keys, self._log_list.controls))
            if not self._showing_empty_state
            else {}
        )
        self._log_list.controls = [
            existing_controls.get(key) or self._build_log_row(event)
            for key, event in zip(target_keys, visible_events)
        ]
        self._rendered_event_keys = target_keys
        self._showing_empty_state = False
        return True

    def _build_log_row(self, event: dict) -> ft.Control:
        key = _event_key(event)
        return _log_row(
            event,
            checked=key in self._selected_keys,
            on_check=self._toggle_selection,
            on_drag_start=self._start_drag_select,
            on_drag_update=self._drag_update_position,
            on_drag_over=self._drag_over_event,
            on_drag_end=self._end_drag_select,
            on_drag_scroll=self._auto_scroll_from_drag,
        )

    def _dedupe_events_locked(self, events: list[dict]) -> list[dict]:
        accepted: list[dict] = []
        for event in events:
            key = _event_key(event)
            if key in self._seen_event_keys:
                continue
            self._seen_event_keys.add(key)
            accepted.append(event)
        return accepted

    def _apply_log_context_locked(self, events: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for event in events:
            item = dict(event)
            if _is_continuation_event(item) and self._last_log_context is not None:
                item["level"] = self._last_log_context.get("level") or item.get("level")
                item["event_time"] = self._last_log_context.get("event_time") or item.get("event_time")
                item["category"] = self._last_log_context.get("category") or item.get("category")
            elif _is_context_source(item):
                self._last_log_context = {
                    "event_time": item.get("event_time"),
                    "level": item.get("level"),
                    "category": item.get("category"),
                }
            normalized.append(item)
        return normalized

    def _trim_events_locked(self) -> None:
        if len(self._events) > MAX_LOG_ENTRIES:
            self._events = self._events[-MAX_LOG_ENTRIES:]
            self._seen_event_keys = {_event_key(event) for event in self._events}


def _event_key(event: dict) -> str:
    if event.get("event_time") and event.get("message"):
        return "|".join(str(event.get(key, "")) for key in ("event_time", "level", "message"))
    if event.get("raw_hash"):
        return str(event["raw_hash"])
    if event.get("raw_line"):
        return str(event["raw_line"])
    return "|".join(str(event.get(key, "")) for key in ("event_time", "level", "message", "created_at"))


def _filter_ui_noise_events(events: list[dict]) -> list[dict]:
    return [event for event in events if not _is_rcon_client_noise_event(event)]


def _coalesce_event_burst(events: list[dict]) -> list[dict]:
    if len(events) <= MAX_EVENTS_PER_REFRESH:
        return events
    retained_count = max(0, MAX_EVENTS_PER_REFRESH - 1)
    skipped_count = len(events) - retained_count
    if retained_count == 0:
        return [make_overflow_event(skipped_count)]
    return [make_overflow_event(skipped_count), *events[-retained_count:]]


def _is_rcon_client_noise_event(event: dict) -> bool:
    message = str(event.get("message") or "")
    raw_line = str(event.get("raw_line") or "")
    return bool(_RCON_CLIENT_NOISE_RE.search(message) or (raw_line and _RCON_CLIENT_NOISE_RE.search(raw_line)))


def _level_button_width(level: str) -> int:
    return _LEVEL_BUTTON_WIDTHS.get(level, 60)


def _is_context_source(event: dict) -> bool:
    return bool(event.get("is_structured") or event.get("event_time"))


def _is_continuation_event(event: dict) -> bool:
    if event.get("is_structured") or event.get("event_time"):
        return False
    text = str(event.get("raw_line") or event.get("message") or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return lowered.startswith(("at ", "caused by:", "suppressed:", "... ")) or any(
        token in text for token in ("Exception:", "Exception in thread", "Error:", "Throwable:")
    )


def _matches_filter(event: dict, selected_level: str, keyword: str) -> bool:
    if selected_level != "ANY" and event.get("level") != selected_level:
        return False
    keyword = (keyword or "").strip().lower()
    if not keyword:
        return True
    return keyword in (event.get("message") or "").lower() or keyword in (event.get("raw_line") or "").lower()


def _log_row(
    item: dict,
    checked: bool = False,
    on_check: object = None,
    on_drag_start: object = None,
    on_drag_update: object = None,
    on_drag_over: object = None,
    on_drag_end: object = None,
    on_drag_scroll: object = None,
) -> ft.Control:
    from src.ui.theme import log_color as _log_color

    level = item.get("level") or "INFO"
    color, _ = _log_color(level)

    def _start(_event: object = None) -> None:
        if on_drag_start and callable(on_drag_start):
            on_drag_start(item)

    def _over(_event: object = None) -> None:
        if on_drag_over and callable(on_drag_over):
            on_drag_over(item)

    def _end(_event: object = None) -> None:
        if on_drag_end and callable(on_drag_end):
            on_drag_end()

    def _update(event: object = None) -> None:
        _start(event)
        if on_drag_update and callable(on_drag_update):
            on_drag_update(item, event)
        else:
            _over(event)
        if on_drag_scroll and callable(on_drag_scroll):
            on_drag_scroll(event)

    def _tap(_event: object = None) -> None:
        if on_check and callable(on_check):
            on_check(item)

    line = _format_log_line(item)
    text_color = color if level in {"WARN", "ERROR"} else theme.TEXT

    selector = ft.GestureDetector(
        content=ft.Container(
            content=ft.Icon(
                _checkbox_icon(checked),
                size=18,
                color=theme.BLUE if checked else theme.MUTED,
            ),
            width=34,
            height=LOG_ROW_HEIGHT,
            bgcolor=theme.BLUE_SOFT if checked else "#00000000",
            alignment=ft.Alignment(0, 0),
        ),
        drag_interval=0,
        hover_interval=0,
        mouse_cursor=ft.MouseCursor.CLICK,
        on_tap=_tap,
        on_tap_up=_end,
        on_pan_start=_start,
        on_pan_update=_update,
        on_pan_end=_end,
        on_pan_cancel=_end,
        on_long_press_cancel=_end,
        on_long_press_up=_end,
        on_long_press_end=_end,
        on_enter=_over,
        tooltip="选择日志",
    )

    return ft.Container(
        content=ft.Row(
            controls=[
                selector,
                ft.Text(
                    line,
                    size=11,
                    color=text_color,
                    font_family="Consolas",
                    expand=True,
                    max_lines=1,
                    no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    selectable=False,
                    enable_interactive_selection=False,
                ),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        height=LOG_ROW_HEIGHT,
        bgcolor=LOG_ROW_SELECTED_BG if checked else "#00000000",
        padding=ft.Padding.only(right=8),
        border=ft.Border.only(bottom=ft.BorderSide(1, theme.LINE)),
        alignment=ft.Alignment(-1, 0),
        data={"event_key": _event_key(item), "selected": checked},
    )


def _checkbox_icon(checked: bool) -> ft.Icons:
    return ft.Icons.CHECK_BOX if checked else ft.Icons.CHECK_BOX_OUTLINE_BLANK


def _event_local_y(event: object = None) -> float | None:
    if event is None:
        return None
    y = getattr(event, "local_y", None)
    if y is not None:
        return float(y)
    local_position = getattr(event, "local_position", None)
    y = getattr(local_position, "y", None)
    if y is not None:
        return float(y)
    y = getattr(event, "y", None)
    if y is not None:
        return float(y)
    return None


def _set_log_row_selection_visual(control: ft.Control, checked: bool) -> None:
    control.bgcolor = LOG_ROW_SELECTED_BG if checked else "#00000000"
    data = dict(getattr(control, "data", {}) or {})
    data["selected"] = checked
    control.data = data

    row = getattr(control, "content", None)
    children = getattr(row, "controls", None) or []
    if not children:
        return
    selector = children[0]
    selector_shell = getattr(selector, "content", None)
    if selector_shell is None:
        return
    selector_shell.bgcolor = theme.BLUE_SOFT if checked else "#00000000"
    icon = getattr(selector_shell, "content", None)
    if icon is None:
        return
    icon.icon = _checkbox_icon(checked)
    icon.color = theme.BLUE if checked else theme.MUTED


def _empty_state(text: str) -> ft.Control:
    return ft.Container(
        content=ft.Text(text, size=12, color=theme.MUTED, text_align=ft.TextAlign.CENTER),
        bgcolor=theme.PANEL_SOFT,
        border=ft.Border.all(1, theme.LINE),
        border_radius=7,
        padding=ft.Padding.symmetric(vertical=14, horizontal=10),
        alignment=ft.Alignment(0, 0),
    )


def _short_time(value: str | None) -> str:
    if not value:
        return "--:--:--"
    if "T" in value:
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%H:%M:%S")
        except ValueError:
            return value.split("T", 1)[1][:8]
    return value[:8]


def _format_log_line(item: dict) -> str:
    time_text = _short_time(item.get("event_time") or item.get("created_at"))
    level = str(item.get("level") or "INFO")[:5]
    message = str(item.get("message") or "")
    return f"{time_text:<8} {level:<5} {message}"
