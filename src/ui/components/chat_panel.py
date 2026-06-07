from __future__ import annotations

import asyncio
import base64
import re
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable

import flet as ft

from src.ai.stream_events import ChatStreamEvent, StreamEventType
from src.interface.chat_interface import ChatInterface
from src.interface.command_interface import CommandInterface
from src.interface.config_edit_interface import ConfigEditInterface
from src.ui import theme
from src.ui.components.common import panel, tag


DEFAULT_LOG_PROMPT = "请分析导入的日志，指出可能原因和下一步排查建议。"
CHAT_BUBBLE_WIDTH = 252
CHAT_CONTENT_WIDTH = CHAT_BUBBLE_WIDTH
CHAT_MIN_BUBBLE_WIDTH = 200
COMPOSER_INPUT_HEIGHT = 78
COMPOSER_RIGHT_FLUSH_EXTENSION = 11
COMPOSER_INNER_RIGHT_GAP = 8
COMPOSER_SEND_BUTTON_WIDTH = 46
COMPOSER_SEND_BUTTON_HEIGHT = 34
COMPOSER_ATTACH_BUTTON_SIZE = 26
COMPOSER_TOOLBAR_GAP = 5
COMPOSER_MODEL_SELECTOR_WIDTH = 158
COMPOSER_MODEL_SELECTOR_HEIGHT = 26
COMPOSER_MODEL_SELECTOR_FONT_SIZE = 10
MODEL_ICON_SIZE = 13
CHAT_FOOTER_LEFT_PADDING = 11
CHAT_FOOTER_RIGHT_PADDING = 11
CHAT_FOOTER_VERTICAL_PADDING = 9
COMPOSER_TEXT_SIZE = 16
COMPOSER_CHAR_WIDTH = 8
COMPOSER_LINE_HEIGHT = 22
COMPOSER_TEXT_LEFT = 10
COMPOSER_TEXT_TOP = 8
COMPOSER_TEXT_RIGHT = 58
COMPOSER_TEXT_BOTTOM = 34
COMPOSER_DRAFT_ATTACHMENT_AREA_HEIGHT = 58
COMPOSER_DRAFT_ATTACHMENT_CARD_HEIGHT = 24
ATTACHMENT_CHIP_MIN_LABEL_WIDTH = 52
ATTACHMENT_CHIP_MAX_LABEL_WIDTH = 96
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_ICON_PATHS = {
    "deepseek": PROJECT_ROOT / "icons/deepseek.svg",
    "qwen": PROJECT_ROOT / "icons/qwen.svg",
}
MODEL_ICON_NORMALIZATION = {
    "deepseek": {"scale": 1.0, "source_width": 1024, "source_height": 1024},
    "qwen": {"scale": 1.0, "source_width": 1024, "source_height": 1024},
}


class ChatPanel:
    def __init__(
        self,
        chat_interface: ChatInterface,
        page: ft.Page,
        session_id: str | None = None,
        config_interface: ConfigEditInterface | None = None,
        command_interface: CommandInterface | None = None,
        on_ask_ai_logs: Callable[[], None] | None = None,
        on_command_audit_change: Callable[[], None] | None = None,
        on_command_execution_start: Callable[[], None] | None = None,
        on_command_result: Callable[[dict], None] | None = None,
        on_server_start_requested: Callable[[], None] | None = None,
        on_config_proposal: Callable[[dict], None] | None = None,
        on_session_change: Callable[[str], None] | None = None,
    ):
        self._interface = chat_interface
        self._page = page
        self.session_id = session_id or chat_interface.create_session("server_ops")
        self._config_interface = config_interface
        self._command_interface = command_interface
        self._on_ask_ai_logs = on_ask_ai_logs
        self._on_command_audit_change = on_command_audit_change
        self._on_command_execution_start = on_command_execution_start
        self._on_command_result = on_command_result
        self._on_server_start_requested = on_server_start_requested
        self._on_config_proposal = on_config_proposal
        self._on_session_change = on_session_change
        self._streaming = False
        self._history_open = False
        self._bubble_width = CHAT_BUBBLE_WIDTH
        self._composer_width = CHAT_CONTENT_WIDTH
        self._content_width = CHAT_CONTENT_WIDTH
        self._draft_attachments: list[dict] = []
        self._next_draft_attachment_id = 1
        self._last_input_value = ""

        self.chat_feed = ft.ListView(expand=True, spacing=6, padding=0, auto_scroll=True)
        self._history_list = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO)
        self._history_body = self._build_history_body()
        self._chat_body = ft.Container(content=self.chat_feed, visible=True, expand=True)
        self._body_content = ft.Stack(
            controls=[self._chat_body, self._history_body],
            expand=True,
        )
        self._history_button = ft.IconButton(
            icon=ft.Icons.MENU,
            tooltip="展开对话历史",
            icon_size=16,
            icon_color=theme.TEXT,
            width=30,
            height=30,
            on_click=lambda _e: self._toggle_history_sidebar(),
        )
        self._selected_model = self._load_selected_model()
        self._model_selector_button = self._make_model_selector_button()
        self._send_button = ft.IconButton(
            icon=ft.Icons.SEND,
            icon_color="#ffffff",
            bgcolor=theme.BLUE,
            tooltip="发送",
            width=COMPOSER_SEND_BUTTON_WIDTH,
            height=COMPOSER_SEND_BUTTON_HEIGHT,
            icon_size=15,
            padding=0,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=7)),
            on_click=lambda _: self.send(),
        )
        self._input = self._make_composer_input()
        self._draft_attachment_list = ft.Row(
            width=self._content_width,
            wrap=True,
            spacing=6,
            run_spacing=5,
            scroll=ft.ScrollMode.AUTO,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._draft_attachment_area = ft.Container(
            content=self._draft_attachment_list,
            width=self._content_width,
            height=0,
            visible=False,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            padding=ft.Padding.only(left=2, right=2, top=0, bottom=5),
        )
        self._attach_button = ft.IconButton(
            icon=ft.Icons.ATTACH_FILE,
            icon_color=theme.MUTED,
            tooltip="文件上传暂未启用",
            width=COMPOSER_ATTACH_BUTTON_SIZE,
            height=COMPOSER_ATTACH_BUTTON_SIZE,
            icon_size=14,
            padding=0,
            disabled=True,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
        )
        self._attach_button_slot = ft.Container(
            content=self._attach_button,
            left=0,
            bottom=0,
            width=COMPOSER_ATTACH_BUTTON_SIZE,
            height=COMPOSER_ATTACH_BUTTON_SIZE,
            alignment=ft.Alignment(-1, 1),
        )
        self._send_button_slot = ft.Container(
            content=self._send_button,
            right=0,
            bottom=0,
            width=COMPOSER_SEND_BUTTON_WIDTH,
            height=COMPOSER_SEND_BUTTON_HEIGHT,
            alignment=ft.Alignment(1, 1),
        )
        self._model_selector_slot = ft.Container(
            content=self._model_selector_button,
            left=COMPOSER_ATTACH_BUTTON_SIZE + COMPOSER_TOOLBAR_GAP,
            bottom=0,
            width=COMPOSER_MODEL_SELECTOR_WIDTH,
            height=COMPOSER_MODEL_SELECTOR_HEIGHT,
            alignment=ft.Alignment(-1, 1),
        )
        self._input_stack = ft.Stack(
            controls=[
                self._input,
                self._attach_button_slot,
                self._model_selector_slot,
                self._send_button_slot,
            ],
            width=self._composer_width,
            height=COMPOSER_INPUT_HEIGHT,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        self._composer_row = ft.Column(
            controls=[
                self._draft_attachment_area,
                self._input_stack,
            ],
            spacing=0,
            width=self._composer_width,
        )
        self._reset_composer_state(update=False)
        self._seed_messages()
        self._sync_body_content()

    @property
    def has_ai(self) -> bool:
        return self._interface.has_ai

    def _load_selected_model(self) -> dict:
        getter = getattr(self._interface, "get_selected_ai_model", None)
        if callable(getter):
            try:
                model = getter()
                if isinstance(model, dict) and model.get("id"):
                    return model
            except Exception:
                pass
        return {
            "id": "unconfigured",
            "selection_id": "deepseek::unconfigured",
            "display_name": "AI 未配置",
            "short_name": "AI 未配置",
            "provider": "",
            "selected": True,
            "configured": False,
        }

    def _list_ai_models(self) -> list[dict]:
        lister = getattr(self._interface, "list_ai_models", None)
        if callable(lister):
            try:
                models = lister()
                if isinstance(models, list) and models:
                    return [model for model in models if isinstance(model, dict)]
            except Exception:
                pass
        return [self._selected_model]

    def _make_model_selector_button(self) -> ft.PopupMenuButton:
        return ft.PopupMenuButton(
            content=_model_selector_content(self._selected_model),
            items=self._model_menu_items(),
            tooltip="选择 AI 模型",
            padding=0,
            width=COMPOSER_MODEL_SELECTOR_WIDTH,
            height=COMPOSER_MODEL_SELECTOR_HEIGHT,
            disabled=self._streaming,
        )

    def _model_menu_items(self) -> list[ft.PopupMenuItem]:
        selected_id = str(
            self._selected_model.get("selection_id")
            or self._selected_model.get("id", "")
        )
        # TODO(settings-ui): move the complete provider model catalog into a
        # searchable settings page so large catalogs do not overload this menu.
        return [
            ft.PopupMenuItem(
                content=_model_menu_content(model),
                checked=(
                    model.get("selection_id") or model.get("id")
                ) == selected_id,
                height=38,
                on_click=lambda _event, selection_id=(
                    model.get("selection_id") or model.get("id", "")
                ): self._select_ai_model(
                    str(selection_id)
                ),
            )
            for model in self._list_ai_models()
        ]

    def _select_ai_model(self, model_id: str) -> None:
        if self._streaming or not model_id:
            return
        selector = getattr(self._interface, "select_ai_model", None)
        if callable(selector):
            try:
                selected = selector(model_id)
                if isinstance(selected, dict) and selected.get("id"):
                    self._selected_model = selected
                else:
                    self._selected_model = self._load_selected_model()
            except Exception:
                self._selected_model = self._load_selected_model()
        else:
            self._selected_model = self._load_selected_model()
        self._sync_model_selector()
        self._page.update()

    def _sync_model_selector(self) -> None:
        self._selected_model = self._load_selected_model()
        self._model_selector_button.content = _model_selector_content(self._selected_model)
        self._model_selector_button.items = self._model_menu_items()
        self._model_selector_button.disabled = self._streaming

    def _set_streaming(self, streaming: bool) -> None:
        self._streaming = streaming
        self._sync_model_selector()
        self._safe_update(self._model_selector_button)

    def switch_session(self, session_id: str) -> None:
        self.session_id = session_id
        self._history_open = False
        self._reset_composer_state(update=False)
        self._seed_messages()
        self._sync_body_content()
        if self._on_session_change is not None:
            self._on_session_change(session_id)
        self._page.update()

    def _create_and_switch_session(self) -> None:
        self.switch_session(self._interface.create_session("server_ops"))

    def set_available_width(
        self,
        width: int | float | None,
        composer_width: int | float | None = None,
        update: bool = False,
    ) -> None:
        bubble_width = _coerce_bubble_width(width)
        row_width = _coerce_composer_width(composer_width, bubble_width)
        content_width = _input_width_for_composer(row_width)
        if (
            bubble_width == self._bubble_width
            and row_width == self._composer_width
            and content_width == self._content_width
        ):
            return

        self._bubble_width = bubble_width
        self._composer_width = row_width
        self._content_width = content_width
        self._input.width = content_width
        self._draft_attachment_area.width = content_width
        self._draft_attachment_list.width = content_width
        self._input_stack.width = row_width
        self._composer_row.width = row_width
        self._sync_draft_attachment_area()
        for control in self.chat_feed.controls:
            _resize_bubble_control(control, bubble_width)
        if update:
            self._page.update()

    def _make_composer_input(self) -> ft.TextField:
        return ft.TextField(
            value="",
            selection=ft.TextSelection(0, 0),
            multiline=True,
            min_lines=3,
            max_lines=3,
            shift_enter=True,
            dense=True,
            border=ft.InputBorder.NONE,
            border_width=0,
            focused_border_width=0,
            border_radius=0,
            border_color="#00000000",
            focused_border_color="#00000000",
            filled=False,
            bgcolor="#00000000",
            focused_bgcolor="#00000000",
            fill_color="#00000000",
            focus_color="#00000000",
            hover_color="#00000000",
            color=theme.TEXT,
            cursor_color=theme.BLUE,
            hint_text="询问玩家、指标或最近日志",
            hint_style=ft.TextStyle(color="#667488"),
            text_size=COMPOSER_TEXT_SIZE,
            text_vertical_align=0,
            text_style=ft.TextStyle(
                size=COMPOSER_TEXT_SIZE,
                height=COMPOSER_LINE_HEIGHT / COMPOSER_TEXT_SIZE,
                font_family="Menlo",
                font_family_fallback=["Monaco", "Consolas", "monospace"],
            ),
            content_padding=ft.Padding.only(
                left=COMPOSER_TEXT_LEFT,
                right=COMPOSER_TEXT_RIGHT,
                top=COMPOSER_TEXT_TOP,
                bottom=COMPOSER_TEXT_BOTTOM,
            ),
            height=COMPOSER_INPUT_HEIGHT,
            width=self._content_width,
            on_change=lambda _e: self._on_composer_change(),
            on_submit=lambda _e: self.send(),
        )

    def _reset_composer_state(self, update: bool = True) -> None:
        self._draft_attachments = []
        self._input.value = ""
        self._input.selection = ft.TextSelection(0, 0)
        self._last_input_value = ""
        self._sync_draft_attachment_area()
        if update:
            self._page.update()

    def _focus_current_input(self) -> None:
        field = self._input
        if not _control_has_page(field):
            return
        run_task = getattr(self._page, "run_task", None)
        if callable(run_task):
            try:
                run_task(field.focus)
            except Exception:
                pass

    def _on_composer_change(self) -> None:
        self._last_input_value = self._input.value or ""

    def _safe_update(self, control: ft.Control) -> None:
        try:
            control.update()
        except Exception:
            pass

    def _toggle_history_sidebar(self) -> None:
        self._history_open = not self._history_open
        self._sync_body_content()
        self._page.update()

    def _open_session_switcher(self, _event: object | None = None) -> None:
        if self._history_open:
            self._history_open = False
        else:
            self._history_open = True
        self._sync_body_content()
        self._page.update()

    def _build_history_sidebar(self) -> ft.Control:
        self._refresh_history_rows()
        return self._history_body

    def _refresh_history_rows(self) -> None:
        sessions = []
        list_sessions = getattr(self._interface, "list_sessions", None)
        if callable(list_sessions):
            try:
                sessions = list_sessions(limit=20)
            except Exception:
                sessions = []
        sessions = _non_empty_sessions(sessions)
        self._history_list.controls = _session_switch_rows(
            sessions,
            self.session_id,
            self._switch_from_popover,
        )

    def _build_history_body(self) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.MENU, size=15, color=theme.BLUE),
                            ft.Text(
                                "对话历史",
                                size=13,
                                weight=ft.FontWeight.W_700,
                                color=theme.TEXT,
                                expand=True,
                            ),
                            ft.TextButton(
                                "新建",
                                icon=ft.Icons.ADD,
                                on_click=lambda _e: self._new_session_from_popover(),
                            ),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=self._history_list,
                        expand=True,
                    ),
                ],
                spacing=9,
            ),
            padding=ft.Padding.all(9),
            visible=False,
            expand=True,
        )

    def _switch_from_popover(self, session_id: str) -> None:
        self.switch_session(session_id)

    def _new_session_from_popover(self) -> None:
        self._create_and_switch_session()

    def _close_session_popover(self, update: bool = True) -> None:
        self._history_open = False
        self._sync_body_content()
        if update:
            self._page.update()

    def _switch_from_dialog(self, session_id: str) -> None:
        self._switch_from_popover(session_id)

    def _new_session_from_dialog(self) -> None:
        self._new_session_from_popover()

    def _sync_body_content(self) -> None:
        if self._history_open:
            self._refresh_history_rows()
            self._chat_body.visible = False
            self._history_body.visible = True
            self._history_button.icon = ft.Icons.CLOSE
            self._history_button.tooltip = "收起对话历史"
        else:
            self._chat_body.visible = True
            self._history_body.visible = False
            self._history_button.icon = ft.Icons.MENU
            self._history_button.tooltip = "展开对话历史"

    def add_attachment(self, attachment: dict) -> None:
        draft = dict(attachment)
        draft["draft_id"] = self._next_draft_attachment_id
        self._next_draft_attachment_id += 1
        self._draft_attachments.append(draft)
        self._sync_draft_attachment_area()
        self._page.update()
        self._focus_current_input()

    def send(self) -> None:
        parts = self._current_prompt_parts()
        if not parts:
            return
        if self._streaming:
            return

        session_id = self.session_id
        text = _parts_text(parts).strip()
        if not text:
            text = DEFAULT_LOG_PROMPT
            parts = [*parts, {"kind": "text", "text": text}]

        attachment_ids = [
            part["attachment"]["attachment_id"]
            for part in parts
            if part.get("kind") == "attachment"
        ]
        display_parts = [dict(part) for part in parts]

        self._reset_composer_state(update=False)

        self.chat_feed.controls.append(
            _message("user", "你", text, prompt_parts=display_parts, width=self._bubble_width)
        )
        bubble = _pending_message(width=self._bubble_width)
        self.chat_feed.controls.append(bubble)
        self._streaming = True
        self._sync_model_selector()
        self._page.update()

        use_non_stream = _looks_like_config_prompt(text) or _looks_like_command_prompt(text)
        if self.has_ai and not use_non_stream:
            if hasattr(self._page, "run_task"):
                self._page.run_task(
                    self._stream_task,
                    text,
                    attachment_ids,
                    bubble,
                    display_parts,
                    session_id,
                )
            else:
                threading.Thread(
                    target=self._stream_thread,
                    args=(text, attachment_ids, bubble, display_parts, session_id),
                    daemon=True,
                ).start()
            return

        if hasattr(self._page, "run_task"):
            self._page.run_task(
                self._send_non_stream_task,
                text,
                attachment_ids,
                bubble,
                display_parts,
                session_id,
            )
        else:
            threading.Thread(
                target=self._send_non_stream_thread,
                args=(text, attachment_ids, bubble, display_parts, session_id),
                daemon=True,
            ).start()

    async def _stream_task(
        self,
        text: str,
        attachment_ids: list[str],
        bubble: ft.Container,
        prompt_parts: list[dict] | None = None,
        session_id: str | None = None,
    ) -> None:
        session_id = session_id or self.session_id
        try:
            full_text = ""
            async for event in self._stream_events_async(
                session_id,
                text,
                attachment_ids,
                prompt_parts,
            ):
                if event.event_type == StreamEventType.DELTA:
                    full_text += event.text
                    _set_markdown_text(bubble, full_text)
                    self._safe_update(bubble)
                elif event.event_type == StreamEventType.TOOL_START:
                    _set_markdown_text(
                        bubble,
                        _tool_progress_text(full_text, event.tool_name, "正在调用本地工具"),
                    )
                    self._safe_update(bubble)
                elif event.event_type == StreamEventType.TOOL_RESULT:
                    _set_markdown_text(
                        bubble,
                        _tool_progress_text(full_text, event.tool_name, "本地工具已返回，正在整理结果"),
                    )
                    self._safe_update(bubble)
                elif event.event_type == StreamEventType.COMMAND_ACTION and event.command_action:
                    full_text = event.text
                    if session_id == self.session_id:
                        self._render_send_result(
                            bubble,
                            {"assistant": full_text, "command_action": event.command_action},
                        )
                    else:
                        _set_markdown_text(bubble, full_text)
                    self._page.update()
                elif event.event_type == StreamEventType.CONFIG_PROPOSAL and event.config_proposal:
                    full_text = event.text
                    if session_id == self.session_id:
                        self._render_send_result(
                            bubble,
                            {
                                "assistant": full_text,
                                "config_proposal": event.config_proposal,
                            },
                        )
                    else:
                        _set_markdown_text(bubble, full_text)
                    self._page.update()
                elif event.event_type == StreamEventType.SERVER_ACTION and event.server_action:
                    if session_id == self.session_id:
                        self._handle_server_action(event.server_action)
                elif event.event_type == StreamEventType.ERROR:
                    full_text = event.error_message or "AI 响应失败。"
                    _set_markdown_text(bubble, full_text)
                    self._safe_update(bubble)
                    break
        except Exception as exc:
            _set_markdown_text(bubble, f"发送失败：{exc}")
            self._safe_update(bubble)
        finally:
            self._set_streaming(False)

    async def _stream_events_async(
        self,
        session_id: str,
        text: str,
        attachment_ids: list[str],
        prompt_parts: list[dict] | None = None,
    ):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[ChatStreamEvent | Exception | None] = asyncio.Queue()
        stop_event = threading.Event()

        def enqueue(item: ChatStreamEvent | Exception | None) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass

        def produce() -> None:
            try:
                stream_turn = getattr(self._interface, "stream_turn", None)
                if callable(stream_turn):
                    stream = (
                        stream_turn(
                            session_id,
                            text,
                            attachment_ids,
                            prompt_parts=prompt_parts,
                        )
                        if prompt_parts is not None
                        else stream_turn(session_id, text, attachment_ids)
                    )
                elif prompt_parts is not None:
                    stream = self._interface.stream_message(
                        session_id,
                        text,
                        attachment_ids,
                        prompt_parts=prompt_parts,
                    )
                else:
                    stream = self._interface.stream_message(session_id, text, attachment_ids)
                for event in stream:
                    if stop_event.is_set():
                        break
                    enqueue(event)
            except Exception as exc:
                enqueue(exc)
            finally:
                enqueue(None)

        threading.Thread(target=produce, daemon=True).start()
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            stop_event.set()

    async def _send_non_stream_task(
        self,
        text: str,
        attachment_ids: list[str],
        bubble: ft.Container,
        prompt_parts: list[dict] | None = None,
        session_id: str | None = None,
    ) -> None:
        session_id = session_id or self.session_id
        try:
            send_turn = getattr(self._interface, "send_turn", None)
            if callable(send_turn):
                if prompt_parts is not None:
                    result = await asyncio.to_thread(
                        send_turn,
                        session_id,
                        text,
                        attachment_ids,
                        prompt_parts=prompt_parts,
                    )
                else:
                    result = await asyncio.to_thread(
                        send_turn,
                        session_id,
                        text,
                        attachment_ids,
                    )
            else:
                if prompt_parts is not None:
                    result = await asyncio.to_thread(
                        self._interface.send_message,
                        session_id,
                        text,
                        attachment_ids,
                        prompt_parts=prompt_parts,
                    )
                else:
                    result = await asyncio.to_thread(
                        self._interface.send_message,
                        session_id,
                        text,
                        attachment_ids,
                    )
            if session_id == self.session_id:
                self._render_send_result(bubble, result)
                self._page.update()
            else:
                _set_markdown_text(bubble, result.get("assistant", ""))
        except Exception as exc:
            _set_markdown_text(bubble, f"发送失败：{exc}")
            bubble.update()
        finally:
            self._set_streaming(False)

    def _send_non_stream_thread(
        self,
        text: str,
        attachment_ids: list[str],
        bubble: ft.Container,
        prompt_parts: list[dict] | None = None,
        session_id: str | None = None,
    ) -> None:
        session_id = session_id or self.session_id
        try:
            send_turn = getattr(self._interface, "send_turn", None)
            if callable(send_turn):
                result = (
                    send_turn(session_id, text, attachment_ids, prompt_parts=prompt_parts)
                    if prompt_parts is not None
                    else send_turn(session_id, text, attachment_ids)
                )
            else:
                result = (
                    self._interface.send_message(
                        session_id,
                        text,
                        attachment_ids,
                        prompt_parts=prompt_parts,
                    )
                    if prompt_parts is not None
                    else self._interface.send_message(session_id, text, attachment_ids)
                )
            if session_id == self.session_id:
                self._render_send_result(bubble, result)
            else:
                _set_markdown_text(bubble, result.get("assistant", ""))
        except Exception as exc:
            _set_markdown_text(bubble, f"发送失败：{exc}")
        finally:
            self._set_streaming(False)
            try:
                self._page.update()
            except Exception:
                pass

    def _stream_thread(
        self,
        text: str,
        attachment_ids: list[str],
        bubble: ft.Container,
        prompt_parts: list[dict] | None = None,
        session_id: str | None = None,
    ) -> None:
        session_id = session_id or self.session_id
        try:
            full_text = ""
            command_action: dict | None = None
            config_proposal: dict | None = None
            stream_turn = getattr(self._interface, "stream_turn", None)
            if callable(stream_turn):
                stream = (
                    stream_turn(session_id, text, attachment_ids, prompt_parts=prompt_parts)
                    if prompt_parts is not None
                    else stream_turn(session_id, text, attachment_ids)
                )
            elif prompt_parts is not None:
                stream = self._interface.stream_message(
                    session_id,
                    text,
                    attachment_ids,
                    prompt_parts=prompt_parts,
                )
            else:
                stream = self._interface.stream_message(session_id, text, attachment_ids)
            for event in stream:
                if event.event_type == StreamEventType.DELTA:
                    full_text += event.text
                elif event.event_type == StreamEventType.TOOL_START:
                    _set_markdown_text(
                        bubble,
                        _tool_progress_text(full_text, event.tool_name, "正在调用本地工具"),
                    )
                elif event.event_type == StreamEventType.TOOL_RESULT:
                    _set_markdown_text(
                        bubble,
                        _tool_progress_text(full_text, event.tool_name, "本地工具已返回，正在整理结果"),
                    )
                elif event.event_type == StreamEventType.COMMAND_ACTION and event.command_action:
                    full_text = event.text
                    command_action = event.command_action
                elif event.event_type == StreamEventType.CONFIG_PROPOSAL and event.config_proposal:
                    full_text = event.text
                    config_proposal = event.config_proposal
                elif event.event_type == StreamEventType.SERVER_ACTION and event.server_action:
                    if session_id == self.session_id:
                        self._handle_server_action(event.server_action)
                elif event.event_type == StreamEventType.ERROR:
                    full_text = event.error_message or "AI 响应失败。"
                    break
            result = {"assistant": full_text}
            if command_action is not None:
                result["command_action"] = command_action
            if config_proposal is not None:
                result["config_proposal"] = config_proposal
            if session_id == self.session_id:
                self._render_send_result(bubble, result)
            else:
                _set_markdown_text(bubble, full_text)
        except Exception as exc:
            _set_markdown_text(bubble, f"发送失败：{exc}")
        finally:
            self._set_streaming(False)
            try:
                self._page.update()
            except Exception:
                pass

    def _render_send_result(self, bubble: ft.Container, result: dict) -> None:
        _set_markdown_text(bubble, result.get("assistant", ""))
        proposal = result.get("config_proposal")
        if proposal and self._on_config_proposal is not None:
            self._on_config_proposal(proposal)
        self._handle_server_action(result.get("server_action"))
        command_actions = result.get("command_actions")
        if command_actions is None:
            command_action = result.get("command_action")
            command_actions = [command_action] if command_action else []
        if command_actions:
            self._notify_command_audit_change()
        if hasattr(self._interface, "confirm_command_action"):
            for command_action in command_actions:
                if command_action.get("status") != "confirmation_required":
                    continue
                self.chat_feed.controls.append(
                    _command_action_card(
                        command_action,
                        self._interface,
                        self._page,
                        self.session_id,
                        self._append_assistant_feedback,
                        self._notify_command_audit_change,
                        self._on_command_execution_start,
                        self._on_command_result,
                        self._on_server_start_requested,
                        width=self._bubble_width,
                    )
                )
        autonomous_task = result.get("autonomous_task")
        if _is_waiting_autonomous_task(autonomous_task):
            self.chat_feed.controls.append(
                _autonomous_task_card(
                    autonomous_task,
                    self._interface,
                    self._page,
                    self._append_assistant_feedback,
                    width=self._bubble_width,
                )
            )

    def _append_assistant_feedback(self, text: str) -> None:
        if not text:
            return
        self.chat_feed.controls.append(
            _message("assistant", "助手", text, markdown=True, width=self._bubble_width)
        )

    def _handle_server_action(self, action: dict | None) -> None:
        if not _is_server_start_action(action):
            return
        if self._on_server_start_requested is not None:
            self._on_server_start_requested()

    def complete_config_feedback(self, recorded_result: dict, action_kind: str) -> None:
        bubble = _message(
            "assistant",
            "助手",
            _config_action_progress_text(recorded_result, action_kind),
            markdown=True,
            width=self._bubble_width,
        )
        self.chat_feed.controls.append(bubble)
        try:
            self._page.update()
        except Exception:
            pass
        feedback_action = getattr(self._interface, "complete_config_action_feedback", None)
        if not callable(feedback_action):
            return
        run_task = getattr(self._page, "run_task", None)
        if callable(run_task):
            try:
                run_task(self._complete_config_feedback_task, recorded_result, action_kind, bubble)
                return
            except Exception:
                pass
        threading.Thread(
            target=self._complete_config_feedback_thread,
            args=(recorded_result, action_kind, bubble),
            daemon=True,
        ).start()

    async def _complete_config_feedback_task(
        self,
        recorded_result: dict,
        action_kind: str,
        bubble: ft.Container,
    ) -> None:
        result = await asyncio.to_thread(
            self._interface.complete_config_action_feedback,
            self.session_id,
            recorded_result,
            action_kind,
            **_turn_kwargs(recorded_result.get("turn_id")),
        )
        _set_markdown_text(bubble, result.get("assistant", ""))
        self._page.update()

    def _complete_config_feedback_thread(
        self,
        recorded_result: dict,
        action_kind: str,
        bubble: ft.Container,
    ) -> None:
        try:
            result = self._interface.complete_config_action_feedback(
                self.session_id,
                recorded_result,
                action_kind,
                **_turn_kwargs(recorded_result.get("turn_id")),
            )
            _set_markdown_text(bubble, result.get("assistant", ""))
            self._page.update()
        except Exception:
            _set_markdown_text(bubble, "配置操作已处理，但生成助手反馈失败。")
            try:
                self._page.update()
            except Exception:
                pass

    def _notify_command_audit_change(self) -> None:
        if self._on_command_audit_change is not None:
            self._on_command_audit_change()

    def _current_prompt_parts(self) -> list[dict]:
        parts: list[dict] = []
        for draft in self._draft_attachments:
            parts.append({
                "kind": "attachment",
                "attachment": _draft_attachment_payload(draft),
            })
        text = self._input.value or ""
        if text.strip():
            parts.append({"kind": "text", "text": text})
        return parts

    def _remove_draft_attachment(self, draft_id: int, update_page: bool = True) -> None:
        before_count = len(self._draft_attachments)
        self._draft_attachments = [
            draft for draft in self._draft_attachments if draft.get("draft_id") != draft_id
        ]
        if len(self._draft_attachments) == before_count:
            return
        self._sync_draft_attachment_area()
        if update_page:
            self._page.update()
            self._focus_current_input()

    def _sync_draft_attachment_area(self) -> None:
        self._draft_attachment_list.controls = [
            _draft_attachment_card(
                draft,
                on_remove=lambda _event, draft_id=int(draft["draft_id"]): (
                    self._remove_draft_attachment(draft_id)
                ),
            )
            for draft in self._draft_attachments
        ]
        has_attachments = bool(self._draft_attachments)
        self._draft_attachment_area.visible = has_attachments
        self._draft_attachment_area.height = (
            COMPOSER_DRAFT_ATTACHMENT_AREA_HEIGHT if has_attachments else 0
        )

    def _seed_messages(self) -> None:
        get_session_view = getattr(self._interface, "get_session_view", None)
        if callable(get_session_view):
            try:
                view = get_session_view(self.session_id, limit_turns=50)
                controls: list[ft.Control] = []
                for turn in view.get("turns", []):
                    turn_attachments = turn.get("attachments") or []
                    for message in turn.get("messages", []):
                        role = message.get("role")
                        if role == "user":
                            prompt_parts = _history_prompt_parts(message, turn_attachments)
                            controls.append(
                                _message(
                                    "user",
                                    "你",
                                    message.get("content", ""),
                                    prompt_parts=prompt_parts,
                                    width=self._bubble_width,
                                )
                            )
                        elif role == "assistant":
                            controls.append(
                                _message(
                                    "assistant",
                                    "助手",
                                    message.get("content", ""),
                                    markdown=True,
                                    width=self._bubble_width,
                                )
                            )
                        elif role == "tool":
                            controls.append(
                                _message(
                                    "tool",
                                    "本地工具",
                                    message.get("content", ""),
                                    width=self._bubble_width,
                                )
                            )
                if controls:
                    self.chat_feed.controls = controls
                    return
            except Exception:
                pass
        self.chat_feed.controls = [
            _message("assistant", "助手", "我可以查询玩家、系统指标和最近日志。", width=self._bubble_width),
            _message("tool", "本地工具", "server_ops workspace ready", width=self._bubble_width),
        ]

    def build(self) -> ft.Control:
        composer = ft.Container(
            content=self._composer_row,
            bgcolor=theme.INPUT_BG,
            border=ft.Border.all(1, theme.LINE),
            border_radius=8,
            padding=ft.Padding.all(6),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        chat_panel = panel(
            title="运维助手",
            subtitle="本地工具优先，AI 可选",
            body=self._body_content,
            action=ft.Row(
                controls=[
                    tag("AI 已连接" if self.has_ai else "本地工具"),
                    self._history_button,
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            footer=composer,
            footer_padding=ft.Padding.only(
                left=CHAT_FOOTER_LEFT_PADDING,
                right=CHAT_FOOTER_RIGHT_PADDING,
                top=CHAT_FOOTER_VERTICAL_PADDING,
                bottom=CHAT_FOOTER_VERTICAL_PADDING,
            ),
        )
        return chat_panel


def _model_selector_content(model: dict) -> ft.Container:
    return ft.Container(
        content=ft.Row(
            controls=[
                _model_icon(model),
                ft.Text(
                    _model_selector_label(model),
                    size=COMPOSER_MODEL_SELECTOR_FONT_SIZE,
                    weight=ft.FontWeight.W_600,
                    color=theme.TEXT,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
            ],
            spacing=3,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        width=COMPOSER_MODEL_SELECTOR_WIDTH,
        height=COMPOSER_MODEL_SELECTOR_HEIGHT,
        bgcolor=theme.PANEL,
        border=ft.Border.all(1, theme.LINE),
        border_radius=6,
        padding=ft.Padding.symmetric(horizontal=6, vertical=0),
        alignment=ft.Alignment(-1, 0),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )


def _model_menu_content(model: dict) -> ft.Row:
    return ft.Row(
        controls=[
            _model_icon(model),
            ft.Text(
                _model_menu_label(model),
                size=13,
                color=theme.TEXT,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
            ),
        ],
        spacing=7,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _model_icon(model: dict) -> ft.Control:
    src = _model_icon_src(str(model.get("provider", "")))
    if not src:
        return ft.Icon(ft.Icons.AUTO_AWESOME, size=MODEL_ICON_SIZE, color=theme.BLUE)
    return ft.Image(
        src=src,
        width=MODEL_ICON_SIZE,
        height=MODEL_ICON_SIZE,
        fit=ft.BoxFit.CONTAIN,
    )


@lru_cache(maxsize=8)
def _model_icon_src(provider: str) -> str:
    path = MODEL_ICON_PATHS.get(provider)
    if path is None or not path.exists():
        return ""
    encoded = base64.b64encode(
        _normalized_model_icon_svg(provider, path).encode("utf-8")
    ).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _normalized_model_icon_svg(provider: str, path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    inner = _svg_inner(raw)
    spec = MODEL_ICON_NORMALIZATION.get(
        provider,
        {"scale": 1.0, "source_width": 1024, "source_height": 1024},
    )
    scale = float(spec["scale"])
    dx = (1024 - (float(spec["source_width"]) * scale)) / 2
    dy = (1024 - (float(spec["source_height"]) * scale)) / 2
    return (
        f'<svg viewBox="0 0 1024 1024" width="{MODEL_ICON_SIZE}" '
        f'height="{MODEL_ICON_SIZE}" xmlns="http://www.w3.org/2000/svg">'
        f'<g transform="translate({dx:.3f} {dy:.3f}) scale({scale:.6f})">'
        f"{inner}</g></svg>"
    )


def _svg_inner(raw_svg: str) -> str:
    match = re.search(r"<svg[^>]*>(.*)</svg>\s*$", raw_svg, flags=re.DOTALL)
    if not match:
        return raw_svg
    return match.group(1)


def _model_selector_label(model: dict) -> str:
    return str(
        model.get("display_name")
        or model.get("id")
        or "Model"
    )


def _model_menu_label(model: dict) -> str:
    label = str(model.get("display_name") or model.get("id") or "Model")
    if not model.get("configured", False):
        return f"{label} · 未配置"
    return label


def _parts_text(parts: list[dict]) -> str:
    return "".join(part.get("text", "") for part in parts if part.get("kind") == "text")


def _prompt_display_text(parts: list[dict]) -> str:
    text_parts = [
        str(part.get("text") or "")
        for part in parts
        if part.get("kind") == "text" and str(part.get("text") or "").strip()
    ]
    if len(text_parts) <= 1:
        return (text_parts[0] if text_parts else "").strip()
    return " ".join(part.strip() for part in text_parts)


def _history_prompt_parts(message: dict, turn_attachments: list[dict]) -> list[dict] | None:
    metadata = message.get("metadata") or {}
    raw_parts = metadata.get("prompt_parts")
    if isinstance(raw_parts, list) and raw_parts:
        parts: list[dict] = []
        for part in raw_parts:
            if part.get("kind") == "text":
                text = part.get("text") or ""
                if text:
                    parts.append({"kind": "text", "text": text})
            elif part.get("kind") == "attachment":
                attachment = part.get("attachment") or {}
                parts.append({"kind": "attachment", "attachment": attachment})
        return parts or None
    if not turn_attachments:
        return None
    return _history_fallback_prompt_parts(message.get("content") or "", turn_attachments)


def _history_fallback_prompt_parts(text: str, attachments: list[dict]) -> list[dict] | None:
    if not attachments:
        return [{"kind": "text", "text": text}] if text.strip() else None

    chunks = re.split(r" {2,}", text, maxsplit=len(attachments))
    if len(chunks) <= 1:
        parts: list[dict] = []
        if text.strip():
            parts.append({"kind": "text", "text": text})
        parts.extend({"kind": "attachment", "attachment": attachment} for attachment in attachments)
        return parts or None

    parts = []
    for index, attachment in enumerate(attachments):
        if index < len(chunks) and chunks[index].strip():
            parts.append({"kind": "text", "text": chunks[index]})
        parts.append({"kind": "attachment", "attachment": attachment})
    tail = chunks[len(attachments)] if len(chunks) > len(attachments) else ""
    if tail.strip():
        parts.append({"kind": "text", "text": tail})
    return parts or None


def _display_units(text: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in text)


def _draft_attachment_payload(draft: dict) -> dict:
    return {
        key: value
        for key, value in draft.items()
        if key != "draft_id"
    }


def _attachment_meta_text(item: dict) -> str:
    pieces: list[str] = []
    line_count = item.get("line_count")
    if line_count:
        pieces.append(f"{line_count} 行")
    time_range = str(item.get("time_range") or "").strip()
    if time_range:
        pieces.append(time_range)
    return " · ".join(pieces)


def _draft_attachment_card(item: dict, on_remove: Callable) -> ft.Container:
    label = _short_attachment_token_label(_attachment_chip_label(item), max_units=18)
    meta_text = _attachment_meta_text(item)
    title_width = 112 if meta_text else 144
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.DESCRIPTION, size=13, color=theme.BLUE),
                ft.Text(
                    label,
                    size=11,
                    weight=ft.FontWeight.W_700,
                    color=theme.TEXT,
                    width=title_width,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    meta_text,
                    size=10,
                    color=theme.MUTED,
                    width=92,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    visible=bool(meta_text),
                ),
                ft.Container(
                    content=ft.Icon(ft.Icons.CLOSE, size=12, color=theme.MUTED),
                    width=16,
                    height=18,
                    alignment=ft.Alignment(0, 0),
                    tooltip="移除日志",
                    on_click=on_remove,
                ),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        height=COMPOSER_DRAFT_ATTACHMENT_CARD_HEIGHT,
        bgcolor=theme.PANEL_SOFT,
        border=ft.Border.all(1, theme.LINE_STRONG),
        border_radius=6,
        padding=ft.Padding.only(left=6, right=4, top=0, bottom=0),
        tooltip=_attachment_chip_tooltip(item, label),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )


def _short_attachment_token_label(label: str, max_units: int = 16) -> str:
    if _display_units(label) <= max_units:
        return label
    units = 0
    chars: list[str] = []
    for char in label:
        char_units = 2 if ord(char) > 127 else 1
        if units + char_units > max_units - 3:
            break
        chars.append(char)
        units += char_units
    return f"{''.join(chars).rstrip()}..."


def _control_has_page(control: ft.Control) -> bool:
    try:
        control.page
        return True
    except RuntimeError:
        return False
    except Exception:
        return False


def _is_server_start_action(action: dict | None) -> bool:
    if not isinstance(action, dict):
        return False
    if action.get("action_type") != "server_start":
        return False
    server = action.get("server") or {}
    if not isinstance(server, dict):
        return False
    return (server.get("state") or server.get("status")) in {"starting", "running"}


def _session_switch_row(
    session: dict,
    current_session_id: str,
    on_select: Callable[[str], None],
) -> ft.Container:
    session_id = str(session.get("id") or "")
    is_current = session_id == current_session_id
    title = session.get("title") or "server_ops"
    updated = session.get("last_turn_at") or session.get("updated_at") or ""
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(
                    content=ft.Text(
                        "•" if is_current else "",
                        size=15,
                        color=theme.BLUE,
                    ),
                    width=10,
                ),
                ft.Column(
                    controls=[
                        ft.Text(
                            title,
                            size=12,
                            weight=ft.FontWeight.W_700,
                            color=theme.BLUE if is_current else theme.TEXT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            _compact_time_only_text(updated),
                            size=10,
                            color=theme.MUTED,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=2, vertical=5),
        on_click=lambda _e, sid=session_id: on_select(sid) if sid else None,
    )


def _session_date_header(label: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(
            label,
            size=10,
            weight=ft.FontWeight.W_700,
            color=theme.SOFT_TEXT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
        padding=ft.Padding.only(left=2, right=2, top=8, bottom=1),
    )


def _session_switch_rows(
    sessions: list[dict],
    current_session_id: str,
    on_select: Callable[[str], None],
) -> list[ft.Control]:
    if not sessions:
        return [
            ft.Container(
                content=ft.Text("暂无历史对话", size=12, color=theme.MUTED),
                padding=ft.Padding.symmetric(horizontal=2, vertical=12),
            )
        ]
    controls: list[ft.Control] = []
    current_group = ""
    for session in sessions:
        group = _session_date_label(
            session.get("last_turn_at") or session.get("updated_at") or ""
        )
        if group != current_group:
            current_group = group
            controls.append(_session_date_header(group))
        controls.append(_session_switch_row(session, current_session_id, on_select))
    return controls


def _non_empty_sessions(sessions: list[dict]) -> list[dict]:
    return [
        session
        for session in sessions
        if int(session.get("visible_message_count") or 0) > 0
    ]


def _compact_time_text(value: str) -> str:
    if not value:
        return "尚无消息"
    try:
        parsed = _local_datetime(value)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value.replace("T", " ")[:19]


def _compact_time_only_text(value: str) -> str:
    if not value:
        return "尚无消息"
    try:
        return _local_datetime(value).strftime("%H:%M:%S")
    except ValueError:
        return _compact_time_text(value)


def _session_date_label(value: str) -> str:
    if not value:
        return "未记录时间"
    try:
        return _local_datetime(value).strftime("%Y-%m-%d")
    except ValueError:
        return value.replace("T", " ")[:10] or "未记录时间"


def _local_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def _message(
    kind: str,
    label: str,
    text: str | None,
    prompt_parts: list[dict] | None = None,
    markdown: bool = False,
    width: int | float | None = CHAT_BUBBLE_WIDTH,
) -> ft.Container:
    if kind == "user":
        bgcolor = theme.BLUE
        border_color = theme.BLUE
        color = "#ffffff"
        align = ft.Alignment(1, 0)
    elif kind == "tool":
        bgcolor = theme.GREEN_SOFT
        border_color = "#245a43"
        color = "#b9efd2"
        align = ft.Alignment(-1, 0)
    else:
        bgcolor = theme.PANEL_SOFT
        border_color = theme.LINE
        color = theme.TEXT
        align = ft.Alignment(-1, 0)

    body_controls: list[ft.Control] = [
        ft.Text(label, size=10, weight=ft.FontWeight.W_700, color=color, opacity=0.72),
    ]
    if prompt_parts:
        body_controls.append(
            _prompt_parts_view(
                prompt_parts,
                color=color,
                width=_content_width_for_bubble(width),
            )
        )
    else:
        content_width = _content_width_for_bubble(width)
        body_controls.append(
            _markdown(text or "", color=color, width=content_width)
            if markdown
            else ft.Text(
                text or "",
                size=12,
                color=color,
                selectable=True,
                width=content_width,
            )
        )

    return ft.Container(
        content=ft.Container(
            content=ft.Column(controls=body_controls, spacing=5),
            bgcolor=bgcolor,
            border=ft.Border.all(1, border_color),
            border_radius=7,
            padding=ft.Padding.symmetric(horizontal=9, vertical=7),
            width=_coerce_bubble_width(width),
        ),
        alignment=align,
    )


def _prompt_parts_view(
    parts: list[dict],
    color: str,
    width: int | float | None = CHAT_CONTENT_WIDTH,
) -> ft.Container:
    attachments = [
        part.get("attachment") or {}
        for part in parts
        if part.get("kind") == "attachment"
    ]
    text = _prompt_display_text(parts)
    controls: list[ft.Control] = []
    if attachments:
        controls.append(
            ft.Row(
                controls=[
                    _attachment_chip(attachment, removable=False)
                    for attachment in attachments
                ],
                spacing=5,
                run_spacing=4,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
    if text:
        controls.append(
            ft.Text(
                text,
                size=12,
                color=color,
                selectable=True,
                width=width,
            )
        )
    if not controls:
        controls.append(ft.Text("", size=12, color=color, width=width))
    return ft.Container(
        content=ft.Column(
            controls=controls,
            spacing=5,
            tight=True,
        ),
        width=width,
    )


def _inline_text_width(text: str, max_width: int | float | None) -> int:
    max_line_width = int(max_width or CHAT_CONTENT_WIDTH)
    estimated = (_display_units(text) * 6) + 4
    if estimated < max_line_width:
        return estimated
    return max(24, min(max_line_width, estimated))


def _attachment_chip(
    item: dict,
    removable: bool,
    on_remove: Callable | None = None,
) -> ft.Container:
    label = _short_attachment_token_label(_attachment_chip_label(item))
    label_width = _attachment_chip_label_width(label)
    chip_width = _attachment_chip_width(label, removable=removable)
    controls: list[ft.Control] = []
    if removable:
        controls.append(
            ft.Container(
                content=ft.Icon(ft.Icons.CLOSE, size=13, color=theme.MUTED),
                width=14,
                height=18,
                alignment=ft.Alignment(0, 0),
                tooltip="移除日志",
                on_click=on_remove,
            )
        )
    controls.append(
        ft.Text(
            label,
            size=13,
            weight=ft.FontWeight.W_600,
            color=theme.TEXT,
            width=label_width,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
    )
    return ft.Container(
        content=ft.Row(
            controls=controls,
            spacing=4 if removable else 0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        width=chip_width,
        height=25,
        bgcolor=theme.INPUT_BG,
        border=ft.Border.all(1, theme.LINE_STRONG),
        border_radius=5,
        padding=ft.Padding.only(left=5, right=7, top=2, bottom=2),
        tooltip=_attachment_chip_tooltip(item, label),
    )


def _attachment_chip_label(item: dict) -> str:
    label = str(item.get("source") or item.get("label") or "日志片段").strip()
    if label.startswith("日志片段 "):
        label = label.removeprefix("日志片段 ").strip()
    if "·" in label:
        label = label.split("·", 1)[0].strip()
    return label


def _attachment_chip_tooltip(item: dict, label: str) -> str:
    line_count = item.get("line_count")
    if line_count:
        return f"{label} · {line_count} 行日志已导入"
    return f"{label} 日志已导入"


def _attachment_chip_label_width(label: str) -> int:
    estimated = (_display_units(label) * 5) + 8
    return max(
        ATTACHMENT_CHIP_MIN_LABEL_WIDTH,
        min(ATTACHMENT_CHIP_MAX_LABEL_WIDTH, estimated),
    )


def _attachment_chip_width(label: str, removable: bool = True) -> int:
    if removable:
        return _attachment_chip_label_width(label) + 34
    return _attachment_chip_label_width(label) + 14


def _is_waiting_autonomous_task(value: dict | None) -> bool:
    if not isinstance(value, dict):
        return False
    return value.get("status") == "awaiting_user_confirmation" and bool(
        value.get("current_proposal")
    )


def _autonomous_task_card(
    task_result: dict,
    chat_interface: ChatInterface,
    page: ft.Page,
    on_assistant_feedback: Callable[[str], None] | None = None,
    width: int | float | None = CHAT_BUBBLE_WIDTH,
) -> ft.Container:
    task = task_result.get("task") or {}
    proposal = task_result.get("current_proposal") or {}
    task_id = task_result.get("task_id") or task.get("id") or ""
    proposal_id = proposal.get("proposal_id") or ""
    status_text = ft.Text("等待确认", size=11, color=theme.MUTED)
    processing = False

    def set_processing(message: str) -> bool:
        nonlocal processing
        if processing:
            return False
        processing = True
        status_text.value = message
        status_text.color = theme.BLUE
        confirm_button.disabled = True
        reject_button.disabled = True
        cancel_button.disabled = True
        try:
            page.update()
        except Exception:
            pass
        return True

    def show_result(result: dict) -> None:
        status = result.get("status")
        status_text.value = result.get("message") or result.get("assistant") or status or "已处理"
        status_text.color = theme.GREEN if status == "completed" else theme.MUTED if status == "cancelled" else theme.RED
        if on_assistant_feedback and result.get("assistant"):
            on_assistant_feedback(result["assistant"])
        try:
            page.update()
        except Exception:
            pass

    def show_failure(exc: Exception) -> None:
        show_result({"status": "failed", "message": f"提交任务操作失败：{exc}"})

    async def continue_task(approved: bool) -> None:
        try:
            result = await asyncio.to_thread(
                chat_interface.continue_autonomous_task,
                task_id,
                proposal_id,
                approved,
            )
        except Exception as exc:
            show_failure(exc)
            return
        show_result(result)

    async def cancel_task() -> None:
        try:
            result = await asyncio.to_thread(
                chat_interface.cancel_autonomous_task,
                task_id,
            )
        except Exception as exc:
            show_failure(exc)
            return
        show_result(result)

    def run_async(coro: Callable[[], object]) -> None:
        run_task = getattr(page, "run_task", None)
        if callable(run_task):
            try:
                run_task(coro)
                return
            except Exception:
                pass
        threading.Thread(target=lambda: asyncio.run(coro()), daemon=True).start()

    def confirm(_event: object) -> None:
        if set_processing("正在应用并验证配置草案..."):
            run_async(lambda: continue_task(True))

    def reject(_event: object) -> None:
        if set_processing("正在拒绝配置草案..."):
            run_async(lambda: continue_task(False))

    def cancel(_event: object) -> None:
        if set_processing("正在取消自主任务..."):
            run_async(cancel_task)

    changes = proposal.get("changes") or []
    change_lines = [
        ft.Text(
            f"{change.get('key')}: {change.get('old_value')} -> {change.get('new_value')}",
            size=11,
            color=theme.TEXT,
            selectable=True,
        )
        for change in changes
    ] or [ft.Text("无配置变化", size=11, color=theme.MUTED)]

    confirm_button = ft.TextButton(
        "确认",
        icon=ft.Icons.CHECK,
        on_click=confirm,
        style=ft.ButtonStyle(color=theme.GREEN),
    )
    reject_button = ft.TextButton(
        "拒绝",
        icon=ft.Icons.BLOCK,
        on_click=reject,
        style=ft.ButtonStyle(color=theme.MUTED),
    )
    cancel_button = ft.IconButton(
        icon=ft.Icons.CLOSE,
        icon_size=16,
        icon_color=theme.RED,
        tooltip="取消任务",
        on_click=cancel,
    )

    return ft.Container(
        content=ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.AUTO_FIX_HIGH, size=15, color=theme.BLUE),
                            ft.Text("自主配置任务", size=12, weight=ft.FontWeight.W_700, color=theme.TEXT),
                            ft.Container(expand=True),
                            tag(proposal.get("risk_level", "UNKNOWN")),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        task.get("user_goal", ""),
                        size=11,
                        color=theme.MUTED,
                        selectable=True,
                    ),
                    ft.Column(controls=change_lines, spacing=2),
                    ft.Text(
                        f"轮次：{task.get('current_round', 0)}/{task.get('max_rounds', 0)} · "
                        f"需要重启：{'是' if proposal.get('restart_required') else '否'}",
                        size=11,
                        color=theme.MUTED,
                    ),
                    status_text,
                    ft.Row(
                        controls=[confirm_button, reject_button, cancel_button],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=6,
            ),
            bgcolor=theme.PANEL_SOFT,
            border=ft.Border.all(1, theme.LINE_STRONG),
            border_radius=7,
            padding=ft.Padding.symmetric(horizontal=9, vertical=8),
            width=_coerce_bubble_width(width),
        ),
        alignment=ft.Alignment(-1, 0),
    )


def _command_action_card(
    action: dict,
    chat_interface: ChatInterface,
    page: ft.Page,
    session_id: str,
    on_assistant_feedback: Callable[[str], None] | None = None,
    on_command_audit_change: Callable[[], None] | None = None,
    on_command_execution_start: Callable[[], None] | None = None,
    on_command_result: Callable[[dict], None] | None = None,
    on_server_start_requested: Callable[[], None] | None = None,
    width: int | float | None = CHAT_BUBBLE_WIDTH,
) -> ft.Container:
    command = action.get("command") or action.get("normalized_command") or ""
    is_restart = action.get("action_type") == "server_restart" or command == "restart_server"
    display_command = action.get("display_name") or ("重启服务器" if is_restart else command)
    audit_id = action.get("audit_id")
    turn_id = action.get("turn_id")
    status = action.get("status", "")
    risk = action.get("risk_level", "UNKNOWN")
    needs_confirmation = status == "confirmation_required"
    status_text = ft.Text(
        "等待确认" if needs_confirmation else action.get("message", status),
        size=11,
        color=theme.MUTED,
    )
    processing = False

    def start_processing(message: str, notify_execution: bool = False) -> bool:
        nonlocal processing
        if processing:
            return False
        processing = True
        status_text.value = message
        status_text.color = theme.BLUE
        apply_button.disabled = True
        cancel_button.disabled = True
        apply_button.icon = ft.Icons.HOURGLASS_EMPTY
        if notify_execution:
            if is_restart and on_server_start_requested:
                on_server_start_requested()
            elif on_command_execution_start:
                on_command_execution_start()
        try:
            page.update()
        except Exception:
            pass
        return True

    def show_result(
        result: dict,
        awaiting_feedback: bool = False,
        append_feedback: bool = True,
        notify_command_result: bool = True,
    ) -> None:
        status_text.value = _interactive_status_text(result, "命令已处理。")
        if awaiting_feedback:
            status_text.value += "\n正在生成 AI 反馈..."
        status_text.color = (
            theme.GREEN
            if result.get("status") == "executed"
            else theme.MUTED
            if result.get("status") == "cancelled"
            else theme.RED
        )
        if result.get("status") == "executed":
            apply_button.icon = ft.Icons.CHECK
        elif result.get("status") == "cancelled":
            apply_button.icon = ft.Icons.BLOCK
        else:
            apply_button.icon = ft.Icons.ERROR_OUTLINE
        if append_feedback and on_assistant_feedback and result.get("assistant"):
            on_assistant_feedback(result["assistant"])
        if on_command_audit_change:
            on_command_audit_change()
        if notify_command_result and on_command_result:
            on_command_result(result)
        try:
            page.update()
        except Exception:
            pass

    def fail_processing(exc: Exception) -> None:
        show_result({
            "status": "failed",
            "error_message": f"提交操作失败：{exc}",
        })

    async def apply_command_task() -> None:
        execute_action = getattr(chat_interface, "execute_confirmed_command_action", None)
        feedback_action = getattr(chat_interface, "complete_command_action_feedback", None)
        try:
            if callable(execute_action) and callable(feedback_action):
                recorded = await asyncio.to_thread(
                    execute_action,
                    session_id,
                    command,
                    audit_id=audit_id,
                    **_turn_kwargs(turn_id),
                )
                show_result(recorded, awaiting_feedback=True, append_feedback=False)
                result = await asyncio.to_thread(
                    feedback_action,
                    session_id,
                    recorded,
                    action_kind="command_confirmation",
                    **_turn_kwargs(turn_id),
                )
                show_result(result, notify_command_result=False)
                return
            else:
                result = await asyncio.to_thread(
                    chat_interface.confirm_command_action,
                    session_id,
                    command,
                    audit_id=audit_id,
                    **_turn_kwargs(turn_id),
                )
        except Exception as exc:
            fail_processing(exc)
            return
        show_result(result)

    def apply_command(_event: object) -> None:
        progress_text = (
            "正在重启服务器，等待停止完成后会提交启动请求..."
            if is_restart
            else "命令执行中，正在等待服务器反馈..."
        )
        if not start_processing(progress_text, notify_execution=True):
            return
        run_task = getattr(page, "run_task", None)
        if callable(run_task):
            try:
                run_task(apply_command_task)
                return
            except Exception:
                pass
        try:
            execute_action = getattr(chat_interface, "execute_confirmed_command_action", None)
            feedback_action = getattr(chat_interface, "complete_command_action_feedback", None)
            if callable(execute_action) and callable(feedback_action):
                recorded = execute_action(
                    session_id,
                    command,
                    audit_id=audit_id,
                    **_turn_kwargs(turn_id),
                )
                show_result(recorded, awaiting_feedback=True, append_feedback=False)
                result = feedback_action(
                    session_id,
                    recorded,
                    action_kind="command_confirmation",
                    **_turn_kwargs(turn_id),
                )
                show_result(result, notify_command_result=False)
                return
            else:
                result = chat_interface.confirm_command_action(
                    session_id,
                    command,
                    audit_id=audit_id,
                    **_turn_kwargs(turn_id),
                )
        except Exception as exc:
            fail_processing(exc)
            return
        show_result(result)

    async def cancel_command_task() -> None:
        cancel_action = getattr(chat_interface, "cancel_command_action", None)
        record_cancel_action = getattr(chat_interface, "record_cancelled_command_action", None)
        feedback_action = getattr(chat_interface, "complete_command_action_feedback", None)
        try:
            if callable(record_cancel_action) and callable(feedback_action):
                recorded = await asyncio.to_thread(
                    record_cancel_action,
                    session_id,
                    command,
                    audit_id=audit_id,
                    **_turn_kwargs(turn_id),
                )
                show_result(recorded, awaiting_feedback=True, append_feedback=False)
                result = await asyncio.to_thread(
                    feedback_action,
                    session_id,
                    recorded,
                    action_kind="command_cancelled",
                    **_turn_kwargs(turn_id),
                )
            elif callable(cancel_action):
                result = await asyncio.to_thread(
                    cancel_action,
                    session_id,
                    command,
                    audit_id=audit_id,
                    **_turn_kwargs(turn_id),
                )
            else:
                result = {
                    "status": "cancelled",
                    "message": "已取消执行该命令。",
                }
        except Exception as exc:
            fail_processing(exc)
            return
        show_result(result)

    def cancel_command(_event: object) -> None:
        if not start_processing("正在取消执行..."):
            return
        run_task = getattr(page, "run_task", None)
        if callable(run_task):
            try:
                run_task(cancel_command_task)
                return
            except Exception:
                pass
        cancel_action = getattr(chat_interface, "cancel_command_action", None)
        record_cancel_action = getattr(chat_interface, "record_cancelled_command_action", None)
        feedback_action = getattr(chat_interface, "complete_command_action_feedback", None)
        try:
            if callable(record_cancel_action) and callable(feedback_action):
                recorded = record_cancel_action(
                    session_id,
                    command,
                    audit_id=audit_id,
                    **_turn_kwargs(turn_id),
                )
                show_result(recorded, awaiting_feedback=True, append_feedback=False)
                result = feedback_action(
                    session_id,
                    recorded,
                    action_kind="command_cancelled",
                    **_turn_kwargs(turn_id),
                )
            elif callable(cancel_action):
                result = cancel_action(
                    session_id,
                    command,
                    audit_id=audit_id,
                    **_turn_kwargs(turn_id),
                )
            else:
                result = {
                    "status": "cancelled",
                    "message": "已取消执行该命令。",
                }
        except Exception as exc:
            fail_processing(exc)
            return
        show_result(result)

    apply_button = ft.FilledButton(
        content="我理解风险，重启服务器" if is_restart else "我理解风险，执行命令",
        icon=ft.Icons.PLAY_ARROW,
        style=_button_style(theme.RED if risk == "HIGH" else theme.BLUE, "#ffffff", theme.RED if risk == "HIGH" else theme.BLUE),
        on_click=apply_command,
        disabled=not needs_confirmation,
    )
    cancel_button = ft.TextButton(
        content="取消",
        on_click=cancel_command,
        disabled=not needs_confirmation,
    )

    return ft.Container(
        content=ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.WARNING_AMBER if risk == "HIGH" else ft.Icons.TERMINAL,
                                size=14,
                                color="#f8d27a" if risk == "HIGH" else theme.BLUE,
                            ),
                            ft.Text(
                                (
                                    "高风险重启确认"
                                    if is_restart and needs_confirmation
                                    else "重启执行结果"
                                    if is_restart
                                    else "高风险命令确认"
                                    if needs_confirmation
                                    else "命令执行结果"
                                ),
                                size=12,
                                weight=ft.FontWeight.W_700,
                                color=theme.TEXT,
                                expand=True,
                            ),
                            tag(risk),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=ft.Text(
                            display_command,
                            size=11,
                            color="#c9d1d9",
                            font_family="Consolas",
                            selectable=True,
                        ),
                        bgcolor=theme.INPUT_BG,
                        border=ft.Border.all(1, theme.LINE),
                        border_radius=7,
                        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                    ),
                    ft.Text(
                        action.get("message", ""),
                        size=11,
                        color=theme.MUTED,
                        selectable=True,
                        visible=bool(action.get("message")),
                    ),
                    ft.Row(
                        controls=[cancel_button, apply_button, status_text],
                        spacing=6,
                        wrap=True,
                        run_spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=7,
            ),
            bgcolor=theme.PANEL_SOFT,
            border=ft.Border.all(1, theme.LINE_STRONG),
            border_radius=7,
            padding=ft.Padding.all(9),
            width=_coerce_bubble_width(width),
        ),
        alignment=ft.Alignment(-1, 0),
    )


def _interactive_status_text(result: dict, default: str) -> str:
    message = (
        result.get("output")
        or result.get("message")
        or result.get("error_message")
        or default
    )
    if result.get("tool_message_id"):
        return f"{message} 已写入对话上下文。"
    return message


def _turn_kwargs(turn_id: str | None) -> dict:
    return {"turn_id": turn_id} if turn_id else {}


def _config_action_progress_text(result: dict, action_kind: str) -> str:
    if action_kind == "config_reject" and result.get("status") == "rejected":
        return "已拒绝此次配置修改，文件未发生变化。\n\n正在生成助手反馈..."
    if action_kind == "config_apply" and result.get("status") == "saved":
        return "已采纳并保存配置修改。\n\n正在生成助手反馈..."
    message = result.get("message") or result.get("error_message") or "配置操作已处理。"
    return f"{message}\n\n正在生成助手反馈..."


def _coerce_bubble_width(width: int | float | None) -> int:
    if width is None:
        return CHAT_BUBBLE_WIDTH
    return max(CHAT_MIN_BUBBLE_WIDTH, int(width))


def _content_width_for_bubble(width: int | float | None) -> int:
    return max(120, _coerce_bubble_width(width) - 20)


def _composer_width_for_bubble(width: int | float | None) -> int:
    return max(120, _coerce_bubble_width(width) + COMPOSER_RIGHT_FLUSH_EXTENSION)


def _coerce_composer_width(
    composer_width: int | float | None,
    bubble_width: int | float | None,
) -> int:
    if composer_width is None:
        return _composer_width_for_bubble(bubble_width)
    return max(120, int(composer_width))


def _input_width_for_composer(composer_width: int | float | None) -> int:
    return max(120, int(composer_width or CHAT_CONTENT_WIDTH) - COMPOSER_INNER_RIGHT_GAP)


def _resize_bubble_control(control: ft.Control, width: int | float | None) -> None:
    inner = getattr(control, "content", None)
    if not isinstance(inner, ft.Container):
        return

    bubble_width = _coerce_bubble_width(width)
    content_width = _content_width_for_bubble(bubble_width)
    inner.width = bubble_width
    content = inner.content
    if not isinstance(content, ft.Column):
        return
    for child in content.controls:
        if isinstance(child, ft.Markdown):
            child.width = content_width
        elif isinstance(child, ft.Container):
            child.width = content_width
            if isinstance(child.content, ft.Row):
                for row_child in child.content.controls:
                    if isinstance(row_child, ft.Text) and row_child.selectable:
                        row_child.width = _inline_text_width(
                            row_child.value or "",
                            content_width,
                        )
            elif isinstance(child.content, ft.Column):
                for column_child in child.content.controls:
                    if isinstance(column_child, ft.Text) and column_child.selectable:
                        column_child.width = content_width
        elif isinstance(child, ft.Text) and child.selectable:
            child.width = content_width


def _pending_message(width: int | float | None = CHAT_BUBBLE_WIDTH) -> ft.Container:
    return _message("assistant", "助手", "处理中...", markdown=True, width=width)


def _markdown(
    text: str,
    color: str = theme.TEXT,
    width: int | float | None = CHAT_CONTENT_WIDTH,
) -> ft.Markdown:
    return ft.Markdown(
        value=text,
        selectable=True,
        extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
        code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
        auto_follow_links=False,
        soft_line_break=True,
        shrink_wrap=False,
        fit_content=False,
        width=width,
        md_style_sheet=ft.MarkdownStyleSheet(
            a_text_style=ft.TextStyle(size=12, color=theme.BLUE, weight=ft.FontWeight.W_600),
            p_text_style=ft.TextStyle(size=12, color=color),
            p_padding=ft.Padding.only(top=0, bottom=2),
            h1_text_style=ft.TextStyle(size=15, color=color, weight=ft.FontWeight.W_700),
            h2_text_style=ft.TextStyle(size=14, color=color, weight=ft.FontWeight.W_700),
            h3_text_style=ft.TextStyle(size=13, color=color, weight=ft.FontWeight.W_700),
            h4_text_style=ft.TextStyle(size=12, color=color, weight=ft.FontWeight.W_700),
            h5_text_style=ft.TextStyle(size=12, color=color, weight=ft.FontWeight.W_700),
            h6_text_style=ft.TextStyle(size=12, color=color, weight=ft.FontWeight.W_700),
            strong_text_style=ft.TextStyle(size=12, color=color, weight=ft.FontWeight.W_700),
            em_text_style=ft.TextStyle(size=12, color=color, italic=True),
            code_text_style=ft.TextStyle(size=11, color=theme.TEXT, font_family="Consolas"),
            blockquote_text_style=ft.TextStyle(size=12, color=theme.MUTED),
            block_spacing=5,
            list_indent=16,
            list_bullet_text_style=ft.TextStyle(size=12, color=theme.MUTED),
            table_head_text_style=ft.TextStyle(size=11, color=theme.TEXT, weight=ft.FontWeight.W_700),
            table_body_text_style=ft.TextStyle(size=11, color=theme.TEXT),
            table_cells_padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            blockquote_padding=ft.Padding.only(left=8, top=3, bottom=3),
            blockquote_decoration=ft.BoxDecoration(
                bgcolor=theme.PANEL_SOFT,
                border=ft.Border.all(1, theme.LINE),
                border_radius=6,
            ),
            codeblock_padding=ft.Padding.all(8),
        ),
    )


def _set_markdown_text(control: ft.Container, text: str) -> None:
    inner = control.content
    if isinstance(inner, ft.Container) and isinstance(inner.content, ft.Column):
        column = inner.content
        if len(column.controls) >= 2 and isinstance(column.controls[-1], ft.Markdown):
            column.controls[-1].value = text


def _tool_progress_text(text: str, tool_name: str | None, status: str) -> str:
    label = _tool_display_name(tool_name)
    prefix = (text or "").rstrip()
    progress = f"> {status}：`{label}`..."
    return f"{prefix}\n\n{progress}" if prefix else progress


def _tool_display_name(tool_name: str | None) -> str:
    return {
        "scan_server_addons": "组件诊断扫描",
        "get_addon_diagnostics": "读取组件诊断",
        "query_recent_logs": "查询最近日志",
        "get_server_status": "查询服务器状态",
        "query_system_metrics": "查询系统指标",
        "get_online_players": "查询在线玩家",
    }.get(str(tool_name or ""), str(tool_name or "本地工具"))


def _looks_like_config_prompt(text: str) -> bool:
    lowered = text.lower()
    compact = "".join(lowered.split())
    return any(
        token in compact
        for token in (
            "server.properties",
            "max-players",
            "pvp",
            "online-mode",
            "gamemode",
            "motd",
            "offlinemode",
        )
    ) or any(
        token in text
        for token in (
            "最大人数",
            "玩家上限",
            "配置",
            "正版验证",
            "离线模式",
            "盗版",
            "命令方块",
            "视距",
            "难度",
            "白名单",
            "端口",
            "出生点保护",
            "允许飞行",
        )
    )


def _looks_like_command_prompt(text: str) -> bool:
    lowered = text.lower()
    compact = "".join(lowered.split())
    if any(
        token in compact
        for token in (
            "op",
            "deop",
            "ban",
            "pardon",
            "whitelist",
            "gamemode",
            "difficulty",
            "weather",
            "tp",
            "give",
            "kick",
            "stop",
            "restart",
            "reboot",
        )
    ):
        return True
    return any(
        token in text
        for token in (
            "管理员",
            "设为管理",
            "操作员",
            "封禁",
            "踢出",
            "白名单",
            "传送",
            "给他",
            "给玩家",
            "执行命令",
            "发送命令",
            "控制台命令",
            "关闭服务器",
            "停止服务器",
            "关掉服务器",
            "停掉服务器",
            "重启服务器",
            "重启mc",
            "重启一下",
            "关服",
            "停服",
            "重开服",
        )
    )


def _button_style(bgcolor: str, color: str, border_color: str) -> ft.ButtonStyle:
    return ft.ButtonStyle(
        bgcolor=bgcolor,
        color=color,
        shape=ft.RoundedRectangleBorder(radius=7),
        side=ft.BorderSide(1, border_color),
        padding=ft.Padding.symmetric(horizontal=10, vertical=7),
    )
