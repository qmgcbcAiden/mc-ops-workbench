from __future__ import annotations

import asyncio
import threading
import time

import flet as ft

from src.ai.stream_events import ChatStreamEvent, StreamEventType
from src.ui import theme
from src.ui.components.chat_panel import (
    COMPOSER_ATTACH_BUTTON_SIZE,
    COMPOSER_DRAFT_ATTACHMENT_AREA_HEIGHT,
    COMPOSER_INNER_RIGHT_GAP,
    COMPOSER_SEND_BUTTON_HEIGHT,
    COMPOSER_SEND_BUTTON_WIDTH,
    COMPOSER_TOOLBAR_GAP,
    CHAT_FOOTER_RIGHT_PADDING,
    DEFAULT_LOG_PROMPT,
    ChatPanel,
    _attachment_chip_width,
    _attachment_chip_label,
    _autonomous_task_card,
    _compact_time_text,
    _command_action_card,
    _looks_like_command_prompt,
    _non_empty_sessions,
    _pending_message,
    _session_switch_rows,
)


class PageStub:
    def __init__(self) -> None:
        self.update_count = 0

    def update(self) -> None:
        self.update_count += 1
        return None


class DeferredTaskPageStub(PageStub):
    def __init__(self) -> None:
        super().__init__()
        self.tasks: list[tuple[object, tuple]] = []

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))
        return None

    def run_next_task(self) -> None:
        handler, args = self.tasks.pop(0)
        asyncio.run(handler(*args))


class ChatActionInterfaceStub:
    has_ai = False

    def __init__(self) -> None:
        self.config_applications: list[dict] = []
        self.config_feedback_calls: list[dict] = []
        self.command_confirmations: list[dict] = []
        self.command_cancellations: list[dict] = []
        self.autonomous_continuations: list[dict] = []

    def create_session(self, title: str | None = "server_ops") -> str:
        return "session_1"

    def apply_config_proposal(
        self,
        session_id: str,
        proposal_id: str,
        high_risk_confirmed: bool = False,
    ) -> dict:
        self.config_applications.append({
            "session_id": session_id,
            "proposal_id": proposal_id,
            "high_risk_confirmed": high_risk_confirmed,
        })
        return {
            "status": "saved",
            "proposal_id": proposal_id,
            "message": "配置已保存。",
            "tool_message_id": "tool_cfg_1",
            "assistant": "LLM 已确认配置保存成功。",
        }

    def complete_config_action_feedback(
        self,
        session_id: str,
        recorded_result: dict,
        action_kind: str = "config_apply",
    ) -> dict:
        self.config_feedback_calls.append({
            "session_id": session_id,
            "recorded_result": recorded_result,
            "action_kind": action_kind,
        })
        return {
            **recorded_result,
            "assistant": "LLM 已确认编辑器中的配置操作完成。",
        }

    def confirm_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
    ) -> dict:
        self.command_confirmations.append({
            "session_id": session_id,
            "command": command,
            "audit_id": audit_id,
        })
        return {
            "status": "executed",
            "command": command,
            "message": "命令已发送到 Minecraft 控制台。",
            "tool_message_id": "tool_cmd_1",
            "assistant": "LLM 已确认命令发送成功。",
        }

    def cancel_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
    ) -> dict:
        self.command_cancellations.append({
            "session_id": session_id,
            "command": command,
            "audit_id": audit_id,
        })
        return {
            "status": "cancelled",
            "command": command,
            "message": "已取消执行该命令。",
            "tool_message_id": "tool_cmd_cancel_1",
            "assistant": "LLM 已确认命令未执行。",
        }

    def continue_autonomous_task(
        self,
        task_id: str,
        proposal_id: str,
        approved: bool,
    ) -> dict:
        self.autonomous_continuations.append({
            "task_id": task_id,
            "proposal_id": proposal_id,
            "approved": approved,
        })
        return {
            "status": "completed",
            "message": "文件级目标已达成。",
            "assistant": "自主配置任务已完成。",
        }

    def cancel_autonomous_task(self, task_id: str) -> dict:
        return {
            "status": "cancelled",
            "task_id": task_id,
            "assistant": "自主配置任务已取消。",
        }


class SwitchingChatInterfaceStub(ChatActionInterfaceStub):
    def __init__(self) -> None:
        super().__init__()
        self.created_sessions: list[str] = []
        self.list_sessions_calls = 0
        self.sessions = [
            {
                "id": "session_1",
                "title": "主会话",
                "updated_at": "2026-06-02T01:00:00+00:00",
                "last_turn_at": "2026-06-02T01:00:00+00:00",
                "visible_message_count": 1,
            },
            {
                "id": "session_2",
                "title": "排障会话",
                "updated_at": "2026-06-02T02:00:00+00:00",
                "last_turn_at": "2026-06-02T02:00:00+00:00",
                "visible_message_count": 1,
            },
        ]

    def list_sessions(self, limit: int = 20) -> list[dict]:
        self.list_sessions_calls += 1
        return self.sessions[:limit]

    def get_session_view(self, session_id: str, limit_turns: int = 50) -> dict:
        del limit_turns
        text = "主会话消息" if session_id == "session_1" else "第二个会话消息"
        return {
            "session": {"id": session_id, "title": session_id},
            "turns": [
                {
                    "id": f"turn_{session_id}",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": text,
                        }
                    ],
                }
            ],
        }

    def create_session(self, title: str | None = "server_ops") -> str:
        session_id = f"session_new_{len(self.created_sessions) + 1}"
        self.created_sessions.append(session_id)
        self.sessions.insert(
            0,
            {
                "id": session_id,
                "title": title,
                "updated_at": "",
                "last_turn_at": "",
                "visible_message_count": 0,
            },
        )
        return session_id


class StreamingCommandActionInterfaceStub(ChatActionInterfaceStub):
    has_ai = True

    def stream_message(
        self,
        _session_id: str,
        _text: str,
        _attachment_ids: list[str],
        prompt_parts: list[dict] | None = None,
    ):
        del prompt_parts
        yield ChatStreamEvent(
            event_type=StreamEventType.COMMAND_ACTION,
            text="`stop` 是高风险 Minecraft 命令，请在确认卡片中点击执行。",
            command_action={
                "status": "confirmation_required",
                "audit_id": 20,
                "command": "stop",
                "risk_level": "HIGH",
                "confirmation_required": True,
            },
        )


class StreamingConfigProposalInterfaceStub(ChatActionInterfaceStub):
    has_ai = True

    proposal = {
        "proposal_id": "cfgp_stream",
        "relative_path": "server.properties",
        "risk_level": "LOW",
        "restart_required": False,
        "changes": [
            {
                "key": "max-players",
                "old_value": "10",
                "new_value": "20",
            }
        ],
        "diff": "-max-players=10\n+max-players=20",
    }

    def stream_message(
        self,
        _session_id: str,
        _text: str,
        _attachment_ids: list[str],
        prompt_parts: list[dict] | None = None,
    ):
        del prompt_parts
        yield ChatStreamEvent(
            event_type=StreamEventType.CONFIG_PROPOSAL,
            text="已生成配置修改草案，等待你在配置编辑器中采纳或拒绝。",
            config_proposal=self.proposal,
        )
        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_END)


class StreamingServerStartInterfaceStub(ChatActionInterfaceStub):
    has_ai = True

    def stream_message(
        self,
        _session_id: str,
        _text: str,
        _attachment_ids: list[str],
        prompt_parts: list[dict] | None = None,
    ):
        del prompt_parts
        yield ChatStreamEvent(
            event_type=StreamEventType.SERVER_ACTION,
            server_action={
                "action_type": "server_start",
                "source_tool": "start_server",
                "server": {"state": "starting", "pid": 4321},
            },
        )
        yield ChatStreamEvent(event_type=StreamEventType.DELTA, text="服务器启动请求已提交。")


class StreamingToolProgressInterfaceStub(ChatActionInterfaceStub):
    has_ai = True

    def stream_message(
        self,
        _session_id: str,
        _text: str,
        _attachment_ids: list[str],
        prompt_parts: list[dict] | None = None,
    ):
        del prompt_parts
        yield ChatStreamEvent(event_type=StreamEventType.DELTA, text="初步结论")
        yield ChatStreamEvent(
            event_type=StreamEventType.TOOL_START,
            tool_name="scan_server_addons",
        )
        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_END)


class SlowStreamingInterfaceStub(ChatActionInterfaceStub):
    has_ai = True

    def __init__(self) -> None:
        super().__init__()
        self.finished = threading.Event()

    def stream_message(
        self,
        _session_id: str,
        _text: str,
        _attachment_ids: list[str],
        prompt_parts: list[dict] | None = None,
    ):
        del prompt_parts
        time.sleep(0.25)
        self.finished.set()
        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_END)


class PhasedCommandActionInterfaceStub(ChatActionInterfaceStub):
    def __init__(self) -> None:
        super().__init__()
        self.feedback_calls: list[dict] = []
        self.on_feedback_start = lambda: None

    def execute_confirmed_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
    ) -> dict:
        self.command_confirmations.append({
            "session_id": session_id,
            "command": command,
            "audit_id": audit_id,
        })
        return {
            "status": "executed",
            "command": command,
            "message": "命令已发送到 Minecraft 控制台。",
        }

    def complete_command_action_feedback(
        self,
        session_id: str,
        recorded_result: dict,
        action_kind: str = "command_confirmation",
    ) -> dict:
        self.on_feedback_start()
        self.feedback_calls.append({
            "session_id": session_id,
            "action_kind": action_kind,
        })
        return {
            **recorded_result,
            "assistant": "LLM 已生成执行结果说明。",
        }


def test_config_proposal_is_handed_to_workbench_without_chat_card() -> None:
    proposal = {
        "proposal_id": "cfgp_test",
        "relative_path": "server.properties",
        "risk_level": "HIGH",
        "restart_required": True,
        "changes": [
            {
                "key": "online-mode",
                "old_value": "true",
                "new_value": "false",
            }
        ],
        "warnings": ["关闭正版验证会显著降低账号安全性。"],
        "diff": "-online-mode=true\n+online-mode=false",
    }
    routed: list[dict] = []
    panel = ChatPanel(
        ChatActionInterfaceStub(),
        PageStub(),
        on_config_proposal=routed.append,
    )
    before = len(panel.chat_feed.controls)

    panel._render_send_result(
        _pending_message(),
        {"assistant": "请在配置编辑器中采纳或拒绝。", "config_proposal": proposal},
    )

    assert routed == [proposal]
    assert len(panel.chat_feed.controls) == before


def test_chat_panel_switches_session_and_notifies_owner() -> None:
    page = PageStub()
    interface = SwitchingChatInterfaceStub()
    changed: list[str] = []
    panel = ChatPanel(
        interface,
        page,
        session_id="session_1",
        on_session_change=changed.append,
    )

    panel._open_session_switcher()

    assert panel._history_open is True
    assert panel._history_body.visible is True
    assert panel._chat_body.visible is False
    assert interface.list_sessions_calls == 1
    assert len(panel._history_list.controls) == 3

    panel._switch_from_popover("session_2")

    assert panel.session_id == "session_2"
    assert changed == ["session_2"]
    assert panel._history_open is False
    assert panel._history_body.visible is False
    assert panel._chat_body.visible is True
    assert "第二个会话消息" in _message_markdown_text(panel.chat_feed.controls[-1])


def test_chat_panel_can_switch_history_while_streaming() -> None:
    interface = SwitchingChatInterfaceStub()
    changed: list[str] = []
    panel = ChatPanel(
        interface,
        PageStub(),
        session_id="session_1",
        on_session_change=changed.append,
    )
    panel._streaming = True

    panel._switch_from_popover("session_2")

    assert panel.session_id == "session_2"
    assert changed == ["session_2"]
    assert "第二个会话消息" in _message_markdown_text(panel.chat_feed.controls[-1])


def test_chat_panel_history_restores_user_prompt_parts_from_metadata() -> None:
    class HistoryInterface(ChatActionInterfaceStub):
        def get_session_view(self, session_id: str, limit_turns: int = 50) -> dict:
            del limit_turns
            return {
                "session": {"id": session_id, "title": session_id},
                "turns": [
                    {
                        "id": "turn_1",
                        "messages": [
                            {
                                "role": "user",
                                "content": "看看这段日志 好像不太影响我游玩",
                                "metadata": {
                                    "prompt_parts": [
                                        {"kind": "text", "text": "看看这段日志 "},
                                        {"kind": "attachment", "attachment": _log_attachment()},
                                        {"kind": "text", "text": " 好像不太影响我游玩"},
                                    ]
                                },
                            }
                        ],
                    }
                ],
            }

    panel = ChatPanel(HistoryInterface(), PageStub())
    user_message = panel.chat_feed.controls[-1]
    prompt_column = _prompt_parts_column(user_message)
    attachment_row = prompt_column.controls[0]

    assert attachment_row.controls[0].content.controls[0].value == "latest.log"
    assert prompt_column.controls[1].value == "看看这段日志 好像不太影响我游玩"


def test_chat_panel_history_falls_back_to_turn_attachments() -> None:
    class HistoryInterface(ChatActionInterfaceStub):
        def get_session_view(self, session_id: str, limit_turns: int = 50) -> dict:
            del limit_turns
            return {
                "session": {"id": session_id, "title": session_id},
                "turns": [
                    {
                        "id": "turn_1",
                        "attachments": [_log_attachment()],
                        "messages": [
                            {
                                "role": "user",
                                "content": "看看日志",
                                "metadata": {},
                            }
                        ],
                    }
                ],
            }

    panel = ChatPanel(HistoryInterface(), PageStub())
    user_message = panel.chat_feed.controls[-1]
    prompt_column = _prompt_parts_column(user_message)
    attachment_row = prompt_column.controls[0]

    assert attachment_row.controls[0].content.controls[0].value == "latest.log"
    assert prompt_column.controls[1].value == "看看日志"


def test_chat_panel_history_infers_attachment_position_from_legacy_gap() -> None:
    class HistoryInterface(ChatActionInterfaceStub):
        def get_session_view(self, session_id: str, limit_turns: int = 50) -> dict:
            del limit_turns
            return {
                "session": {"id": session_id, "title": session_id},
                "turns": [
                    {
                        "id": "turn_1",
                        "attachments": [_log_attachment()],
                        "messages": [
                            {
                                "role": "user",
                                "content": "看看日志  好像不太影响我游玩",
                                "metadata": {},
                            }
                        ],
                    }
                ],
            }

    panel = ChatPanel(HistoryInterface(), PageStub())
    user_message = panel.chat_feed.controls[-1]
    prompt_column = _prompt_parts_column(user_message)
    attachment_row = prompt_column.controls[0]

    assert attachment_row.controls[0].content.controls[0].value == "latest.log"
    assert attachment_row.controls[0].width == _attachment_chip_width(
        "latest.log",
        removable=False,
    )
    assert prompt_column.controls[1].value == "看看日志 好像不太影响我游玩"


def test_chat_panel_new_session_button_creates_and_switches() -> None:
    page = PageStub()
    interface = SwitchingChatInterfaceStub()
    changed: list[str] = []
    panel = ChatPanel(
        interface,
        page,
        session_id="session_1",
        on_session_change=changed.append,
    )

    panel._open_session_switcher()
    panel._new_session_from_popover()

    assert interface.created_sessions == ["session_new_1"]
    assert panel.session_id == "session_new_1"
    assert changed == ["session_new_1"]


def test_chat_panel_history_button_toggles_sidebar() -> None:
    panel = ChatPanel(
        SwitchingChatInterfaceStub(),
        PageStub(),
        session_id="session_1",
    )

    panel._toggle_history_sidebar()

    assert panel._history_open is True
    assert panel._history_button.tooltip == "收起对话历史"
    assert panel._history_body.visible is True
    assert panel._chat_body.visible is False
    assert len(panel._history_list.controls) == 3

    panel._toggle_history_sidebar()

    assert panel._history_open is False
    assert panel._history_button.tooltip == "展开对话历史"
    assert panel._history_body.visible is False
    assert panel._chat_body.visible is True


def test_session_history_filters_empty_sessions() -> None:
    sessions = [
        {"id": "empty", "visible_message_count": 0},
        {"id": "active", "visible_message_count": 2},
    ]

    assert [session["id"] for session in _non_empty_sessions(sessions)] == ["active"]


def test_session_history_time_uses_local_timezone() -> None:
    text = _compact_time_text("2026-06-02T08:35:38+00:00")

    assert "UTC" not in text
    assert text.endswith(":35:38")


def test_session_history_groups_rows_by_date() -> None:
    rows = _session_switch_rows(
        [
            {
                "id": "session_1",
                "title": "同一天较晚",
                "updated_at": "2026-06-02T11:00:00",
            },
            {
                "id": "session_2",
                "title": "同一天较早",
                "updated_at": "2026-06-02T10:00:00",
            },
            {
                "id": "session_3",
                "title": "前一天",
                "updated_at": "2026-06-01T09:00:00",
            },
        ],
        "session_1",
        lambda _session_id: None,
    )

    assert len(rows) == 5
    assert rows[0].content.value == "2026-06-02"
    assert rows[3].content.value == "2026-06-01"


def test_config_feedback_appends_assistant_result_after_workbench_action() -> None:
    page = DeferredTaskPageStub()
    interface = ChatActionInterfaceStub()
    panel = ChatPanel(interface, page)
    before = len(panel.chat_feed.controls)
    recorded_result = {"status": "saved", "proposal_id": "cfgp_test"}

    panel.complete_config_feedback(recorded_result, "config_apply")

    assert len(panel.chat_feed.controls) == before + 1
    assert "已采纳并保存" in _message_markdown_text(panel.chat_feed.controls[-1])
    assert "正在生成助手反馈" in _message_markdown_text(panel.chat_feed.controls[-1])
    page.run_next_task()

    assert interface.config_feedback_calls == [{
        "session_id": "session_1",
        "recorded_result": recorded_result,
        "action_kind": "config_apply",
    }]
    assert len(panel.chat_feed.controls) == before + 1
    assert _message_markdown_text(panel.chat_feed.controls[-1]) == "LLM 已确认编辑器中的配置操作完成。"


def test_rejected_config_feedback_immediately_reports_file_unchanged() -> None:
    page = DeferredTaskPageStub()
    panel = ChatPanel(ChatActionInterfaceStub(), page)

    panel.complete_config_feedback(
        {"status": "rejected", "proposal_id": "cfgp_test"},
        "config_reject",
    )

    status = _message_markdown_text(panel.chat_feed.controls[-1])
    assert "已拒绝此次配置修改" in status
    assert "文件未发生变化" in status


def test_command_action_card_builds_with_current_flet_button_api() -> None:
    action = {
        "status": "confirmation_required",
        "audit_id": 17,
        "command": "op Aiden233",
        "risk_level": "HIGH",
        "confirmation_required": True,
        "message": "高风险命令；需要确认。",
    }

    card = _command_action_card(action, ChatActionInterfaceStub(), PageStub(), "session_1")

    assert card.__class__.__name__ == "Container"


def test_chinese_shutdown_prompt_uses_card_capable_message_path() -> None:
    assert _looks_like_command_prompt("帮我关闭服务器") is True
    assert _looks_like_command_prompt("帮我重启服务器") is True
    assert _looks_like_command_prompt("restart server") is True


def test_restart_confirmation_card_uses_restart_action_command() -> None:
    action = {
        "status": "confirmation_required",
        "audit_id": None,
        "command": "restart_server",
        "display_name": "重启服务器",
        "action_type": "server_restart",
        "risk_level": "HIGH",
        "confirmation_required": True,
        "message": "重启会先停止服务器，再调用 start_server。",
    }
    page = PageStub()
    chat_interface = ChatActionInterfaceStub()
    command_starts: list[str] = []
    server_starts: list[str] = []

    card = _command_action_card(
        action,
        chat_interface,
        page,
        "session_1",
        on_command_execution_start=lambda: command_starts.append("command"),
        on_server_start_requested=lambda: server_starts.append("server"),
    )
    execute_button = _card_action_button(card)

    assert execute_button.content == "我理解风险，重启服务器"

    execute_button.on_click(None)

    assert chat_interface.command_confirmations == [
        {
            "session_id": "session_1",
            "command": "restart_server",
            "audit_id": None,
        }
    ]
    assert command_starts == []
    assert server_starts == ["server"]


def test_command_confirmation_card_executes_and_records_to_chat_context() -> None:
    action = {
        "status": "confirmation_required",
        "audit_id": 17,
        "command": "op Aiden233",
        "risk_level": "HIGH",
        "confirmation_required": True,
        "message": "高风险命令；需要确认。",
    }
    page = PageStub()
    chat_interface = ChatActionInterfaceStub()
    feedback: list[str] = []
    audit_refreshes: list[str] = []
    command_results: list[dict] = []

    card = _command_action_card(
        action,
        chat_interface,
        page,
        "session_1",
        feedback.append,
        lambda: audit_refreshes.append("changed"),
        on_command_result=command_results.append,
    )
    execute_button = _card_action_button(card)

    execute_button.on_click(None)

    assert chat_interface.command_confirmations == [
        {
            "session_id": "session_1",
            "command": "op Aiden233",
            "audit_id": 17,
        }
    ]
    assert execute_button.disabled is True
    assert feedback == ["LLM 已确认命令发送成功。"]
    assert audit_refreshes == ["changed"]
    assert command_results[0]["status"] == "executed"
    assert command_results[0]["command"] == "op Aiden233"


def test_command_confirmation_card_cancels_and_records_to_chat_context() -> None:
    action = {
        "status": "confirmation_required",
        "audit_id": 18,
        "command": "op Aiden233",
        "risk_level": "HIGH",
        "confirmation_required": True,
        "message": "高风险命令；需要确认。",
    }
    page = PageStub()
    chat_interface = ChatActionInterfaceStub()
    feedback: list[str] = []
    audit_refreshes: list[str] = []

    card = _command_action_card(
        action,
        chat_interface,
        page,
        "session_1",
        feedback.append,
        lambda: audit_refreshes.append("changed"),
    )
    cancel_button = _card_cancel_button(card)
    execute_button = _card_action_button(card)

    cancel_button.on_click(None)

    assert chat_interface.command_cancellations == [
        {
            "session_id": "session_1",
            "command": "op Aiden233",
            "audit_id": 18,
        }
    ]
    assert chat_interface.command_confirmations == []
    assert cancel_button.disabled is True
    assert execute_button.disabled is True
    assert feedback == ["LLM 已确认命令未执行。"]
    assert audit_refreshes == ["changed"]


def test_command_confirmation_paints_busy_state_and_ignores_double_click_before_work_finishes() -> None:
    action = {
        "status": "confirmation_required",
        "audit_id": 19,
        "command": "op Aiden233",
        "risk_level": "HIGH",
        "confirmation_required": True,
        "message": "高风险命令；需要确认。",
    }
    page = DeferredTaskPageStub()
    chat_interface = ChatActionInterfaceStub()
    started: list[str] = []

    card = _command_action_card(
        action,
        chat_interface,
        page,
        "session_1",
        on_command_execution_start=lambda: started.append("started"),
    )
    execute_button = _card_action_button(card)

    execute_button.on_click(None)
    execute_button.on_click(None)

    assert execute_button.disabled is True
    assert started == ["started"]
    assert page.update_count == 1
    assert len(page.tasks) == 1
    assert chat_interface.command_confirmations == []

    page.run_next_task()

    assert chat_interface.command_confirmations == [
        {
            "session_id": "session_1",
            "command": "op Aiden233",
            "audit_id": 19,
        }
    ]


def test_command_confirmation_shows_execution_finished_while_llm_feedback_is_pending() -> None:
    action = {
        "status": "confirmation_required",
        "audit_id": 21,
        "command": "op Aiden233",
        "risk_level": "HIGH",
        "confirmation_required": True,
    }
    page = DeferredTaskPageStub()
    chat_interface = PhasedCommandActionInterfaceStub()
    waiting_values: list[str] = []
    card = _command_action_card(action, chat_interface, page, "session_1")
    chat_interface.on_feedback_start = lambda: waiting_values.append(_card_status_text(card).value)

    _card_action_button(card).on_click(None)
    page.run_next_task()

    assert waiting_values == [
        "命令已发送到 Minecraft 控制台。\n正在生成 AI 反馈..."
    ]
    assert chat_interface.feedback_calls == [{
        "session_id": "session_1",
        "action_kind": "command_confirmation",
    }]


def test_autonomous_task_card_confirms_with_task_and_proposal_ids() -> None:
    page = DeferredTaskPageStub()
    chat_interface = ChatActionInterfaceStub()
    feedback: list[str] = []
    card = _autonomous_task_card(
        {
            "status": "awaiting_user_confirmation",
            "task_id": "auto_cfg_1",
            "task": {
                "id": "auto_cfg_1",
                "user_goal": "自主把最大人数改到 30",
                "current_round": 1,
                "max_rounds": 3,
            },
            "current_proposal": {
                "proposal_id": "cfgp_1",
                "risk_level": "MEDIUM",
                "restart_required": True,
                "changes": [
                    {"key": "max-players", "old_value": "20", "new_value": "30"}
                ],
            },
        },
        chat_interface,
        page,
        feedback.append,
    )

    _autonomous_confirm_button(card).on_click(None)
    page.run_next_task()

    assert chat_interface.autonomous_continuations == [{
        "task_id": "auto_cfg_1",
        "proposal_id": "cfgp_1",
        "approved": True,
    }]
    assert feedback == ["自主配置任务已完成。"]


def _card_action_button(card):
    content = card.content
    column = content.content
    action_row = column.controls[-1]
    for control in action_row.controls:
        if control.__class__.__name__ == "FilledButton":
            return control
    raise AssertionError("FilledButton not found")


def _card_cancel_button(card):
    content = card.content
    column = content.content
    action_row = column.controls[-1]
    for control in action_row.controls:
        if control.__class__.__name__ == "TextButton":
            return control
    raise AssertionError("TextButton not found")


def _card_status_text(card):
    content = card.content
    column = content.content
    action_row = column.controls[-1]
    return action_row.controls[-1]


def _message_markdown_text(message):
    return message.content.content.controls[-1].value


def _prompt_parts_column(message):
    prompt_container = message.content.content.controls[1]
    assert isinstance(prompt_container, ft.Container)
    assert isinstance(prompt_container.content, ft.Column)
    return prompt_container.content


def _autonomous_confirm_button(card):
    content = card.content
    column = content.content
    action_row = column.controls[-1]
    return action_row.controls[0]


def _log_attachment() -> dict:
    return {
        "attachment_id": "att_log_1",
        "label": "latest.log",
        "line_count": 74,
    }


def _type_at_cursor(panel: ChatPanel, text: str) -> None:
    cursor = panel._input.selection.start
    value = panel._input.value or ""
    panel._input.value = f"{value[:cursor]}{text}{value[cursor:]}"
    panel._input.selection = ft.TextSelection(cursor + len(text), cursor + len(text))
    panel._on_composer_change()


def _draft_card_label(card: ft.Container) -> str:
    return card.content.controls[1].value


def _draft_card_remove_button(card: ft.Container) -> ft.Container:
    return card.content.controls[-1]


class SlowChatInterfaceStub:
    has_ai = False

    def __init__(self) -> None:
        self.finished = threading.Event()

    def create_session(self, title: str | None = "server_ops") -> str:
        return "session_1"

    def send_message(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> dict:
        del prompt_parts
        time.sleep(0.25)
        self.finished.set()
        return {
            "assistant": "已生成配置修改草案。",
            "source": "local",
        }


class RecordingChatInterfaceStub:
    has_ai = False

    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    def create_session(self, title: str | None = "server_ops") -> str:
        return "session_1"

    def send_message(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> dict:
        self.sent_messages.append({
            "session_id": session_id,
            "user_message": user_message,
            "attachment_ids": list(attachment_ids or []),
            "prompt_parts": prompt_parts,
        })
        return {"assistant": "ok", "source": "local"}


def test_chat_panel_log_attachment_uses_draft_attachment_area() -> None:
    panel = ChatPanel(ChatActionInterfaceStub(), PageStub())
    panel._input.value = "test"

    panel.add_attachment(_log_attachment())

    assert isinstance(panel._composer_row, ft.Column)
    assert panel._composer_row.controls[0] is panel._draft_attachment_area
    assert panel._composer_row.controls[1] is panel._input_stack
    assert panel._input_stack.controls[0] is panel._input
    assert panel._send_button_slot.content is panel._send_button
    assert panel._attach_button_slot.content is panel._attach_button
    assert panel._attach_button.disabled is True
    assert panel._attach_button.tooltip == "文件上传暂未启用"
    assert panel._input.multiline is True
    assert panel._input.value == "test"
    assert "latest.log" not in panel._input.value
    assert "×" not in panel._input.value
    assert panel._draft_attachment_area.visible is True
    assert panel._draft_attachment_area.height == COMPOSER_DRAFT_ATTACHMENT_AREA_HEIGHT
    assert len(panel._draft_attachment_list.controls) == 1
    assert _draft_card_label(panel._draft_attachment_list.controls[0]) == "latest.log"
    assert panel._model_selector_slot.left == (
        COMPOSER_ATTACH_BUTTON_SIZE + COMPOSER_TOOLBAR_GAP
    )


def test_chat_panel_composer_uses_single_outer_border_and_rect_send_button() -> None:
    panel = ChatPanel(ChatActionInterfaceStub(), PageStub())
    control = panel.build()

    footer = control.content.controls[-1]
    composer = footer.content

    assert footer.padding.right == CHAT_FOOTER_RIGHT_PADDING
    assert composer.bgcolor == theme.INPUT_BG
    assert composer.border is not None
    assert isinstance(panel._input, ft.TextField)
    assert panel._input.border == ft.InputBorder.NONE
    assert panel._input.border_width == 0
    assert panel._input.focused_border_width == 0
    assert panel._input.bgcolor == "#00000000"
    assert panel._input.focused_bgcolor == "#00000000"
    assert panel._send_button.width == COMPOSER_SEND_BUTTON_WIDTH
    assert panel._send_button.height == COMPOSER_SEND_BUTTON_HEIGHT
    assert panel._send_button.width > panel._send_button.height
    assert panel._send_button_slot.width == COMPOSER_SEND_BUTTON_WIDTH
    assert panel._send_button_slot.height == COMPOSER_SEND_BUTTON_HEIGHT

    panel.set_available_width(252, composer_width=300)
    assert panel._composer_row.width == 300
    assert panel._input_stack.width == 300
    assert panel._input.width == 300 - COMPOSER_INNER_RIGHT_GAP
    assert panel._draft_attachment_area.width == 300 - COMPOSER_INNER_RIGHT_GAP
    assert panel._draft_attachment_list.width == 300 - COMPOSER_INNER_RIGHT_GAP


def test_chat_panel_plain_input_keeps_cursor_newline_and_delete_normal() -> None:
    panel = ChatPanel(ChatActionInterfaceStub(), PageStub())
    panel._input.value = "看看日志"
    panel._input.selection = ft.TextSelection(2, 2)
    panel.add_attachment(_log_attachment())
    _type_at_cursor(panel, "\n这是第二行")

    assert panel._input.value == "看看\n这是第二行日志"
    assert panel._input.selection.start == len("看看\n这是第二行")
    assert panel._current_prompt_parts() == [
        {"kind": "attachment", "attachment": _log_attachment()},
        {"kind": "text", "text": "看看\n这是第二行日志"},
    ]


def test_chat_panel_multiple_draft_attachments_preserve_order() -> None:
    panel = ChatPanel(ChatActionInterfaceStub(), PageStub())

    panel.add_attachment(_log_attachment())
    panel.add_attachment({
        "attachment_id": "att_log_2",
        "label": "debug.log",
        "line_count": 6,
        "time_range": "12:00:01-12:00:10",
    })

    assert [_draft_card_label(card) for card in panel._draft_attachment_list.controls] == [
        "latest.log",
        "debug.log",
    ]
    assert [draft["attachment_id"] for draft in panel._draft_attachments] == [
        "att_log_1",
        "att_log_2",
    ]


def test_chat_panel_log_attachment_label_does_not_repeat_metadata() -> None:
    assert (
        _attachment_chip_label({
            "label": "日志片段 latest.log · 180 行",
            "line_count": 180,
        })
        == "latest.log"
    )


def test_chat_panel_removing_draft_attachment_does_not_touch_input() -> None:
    panel = ChatPanel(ChatActionInterfaceStub(), PageStub())
    panel._input.value = "看看日志\n这段文字保留"
    panel.add_attachment(_log_attachment())

    _draft_card_remove_button(panel._draft_attachment_list.controls[0]).on_click(None)

    assert panel._draft_attachments == []
    assert panel._draft_attachment_list.controls == []
    assert panel._draft_attachment_area.visible is False
    assert panel._draft_attachment_area.height == 0
    assert panel._input.value == "看看日志\n这段文字保留"


def test_chat_panel_sent_log_attachment_renders_above_prompt_text() -> None:
    page = DeferredTaskPageStub()
    panel = ChatPanel(RecordingChatInterfaceStub(), page)
    panel._input.value = "帮我看看日志: 不影响游戏是不是不用管"
    panel.add_attachment({
        "attachment_id": "att_log_1",
        "label": "日志片段 latest.log · 180 行",
        "line_count": 180,
    })

    panel.send()

    user_message = panel.chat_feed.controls[-2]
    prompt_column = _prompt_parts_column(user_message)
    attachment_row = prompt_column.controls[0]
    prompt_text = prompt_column.controls[1]

    assert isinstance(attachment_row, ft.Row)
    assert attachment_row.wrap is True
    assert attachment_row.controls[0].content.controls[0].value == "latest.log"
    assert attachment_row.controls[0].width == _attachment_chip_width(
        "latest.log",
        removable=False,
    )
    assert prompt_text.value == "帮我看看日志: 不影响游戏是不是不用管"


def test_chat_panel_send_with_text_and_attachments_uses_plain_prompt_parts() -> None:
    page = DeferredTaskPageStub()
    interface = RecordingChatInterfaceStub()
    panel = ChatPanel(interface, page)
    panel._input.value = "后段\n第二行"
    panel.add_attachment(_log_attachment())
    panel.add_attachment({
        "attachment_id": "att_log_2",
        "label": "debug.log",
        "line_count": 6,
    })

    panel.send()
    page.run_next_task()

    assert interface.sent_messages == [
        {
            "session_id": "session_1",
            "user_message": "后段\n第二行",
            "attachment_ids": ["att_log_1", "att_log_2"],
            "prompt_parts": [
                {
                    "kind": "attachment",
                    "attachment": {
                        "attachment_id": "att_log_1",
                        "label": "latest.log",
                        "line_count": 74,
                    },
                },
                {
                    "kind": "attachment",
                    "attachment": {
                        "attachment_id": "att_log_2",
                        "label": "debug.log",
                        "line_count": 6,
                    },
                },
                {"kind": "text", "text": "后段\n第二行"},
            ],
        }
    ]
    assert panel._draft_attachments == []
    assert panel._draft_attachment_list.controls == []
    assert panel._draft_attachment_area.visible is False
    assert panel._input.value == ""
    assert panel._input.hint_text == "询问玩家、指标或最近日志"


def test_chat_panel_send_attachment_only_uses_default_log_prompt() -> None:
    page = DeferredTaskPageStub()
    interface = RecordingChatInterfaceStub()
    panel = ChatPanel(interface, page)
    panel.add_attachment(_log_attachment())

    panel.send()
    page.run_next_task()

    assert interface.sent_messages == [
        {
            "session_id": "session_1",
            "user_message": DEFAULT_LOG_PROMPT,
            "attachment_ids": ["att_log_1"],
            "prompt_parts": [
                {"kind": "attachment", "attachment": _log_attachment()},
                {"kind": "text", "text": DEFAULT_LOG_PROMPT},
            ],
        }
    ]
    assert panel._draft_attachments == []
    assert panel._input.value == ""


def test_chat_panel_switch_session_clears_draft_attachments() -> None:
    interface = SwitchingChatInterfaceStub()
    panel = ChatPanel(interface, PageStub(), session_id="session_1")
    panel._input.value = "待发送"
    panel.add_attachment(_log_attachment())

    panel.switch_session("session_2")

    assert panel.session_id == "session_2"
    assert panel._draft_attachments == []
    assert panel._draft_attachment_list.controls == []
    assert panel._draft_attachment_area.visible is False
    assert panel._input.value == ""


def test_chat_panel_non_stream_send_returns_after_painting_pending_message() -> None:
    page = PageStub()
    interface = SlowChatInterfaceStub()
    panel = ChatPanel(interface, page)
    panel._input.value = "关掉pvp"

    started = time.monotonic()
    panel.send()
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    assert page.update_count >= 1
    assert len(panel.chat_feed.controls) == 4
    assert interface.finished.wait(1)


def test_chat_panel_notifies_audit_change_when_command_action_arrives() -> None:
    notifications: list[str] = []
    panel = ChatPanel(
        ChatActionInterfaceStub(),
        PageStub(),
        on_command_audit_change=lambda: notifications.append("changed"),
    )

    panel._render_send_result(
        _pending_message(),
        {
            "assistant": "需要确认命令。",
            "command_action": {
                "status": "confirmation_required",
                "command": "op Aiden233",
                "risk_level": "HIGH",
                "confirmation_required": True,
                "message": "高风险命令；需要确认。",
            },
        },
    )

    assert notifications == ["changed"]
    assert len(panel.chat_feed.controls) == 3


def test_chat_panel_non_stream_start_server_result_starts_startup_log_follow() -> None:
    server_starts: list[str] = []
    panel = ChatPanel(
        ChatActionInterfaceStub(),
        PageStub(),
        on_server_start_requested=lambda: server_starts.append("server"),
    )

    panel._render_send_result(
        _pending_message(),
        {
            "assistant": "服务器启动请求已提交。",
            "server_action": {
                "action_type": "server_start",
                "source_tool": "start_server",
                "server": {"state": "starting", "pid": 4321},
            },
        },
    )

    assert server_starts == ["server"]


def test_chat_panel_renders_each_confirmation_in_compound_command_result() -> None:
    panel = ChatPanel(ChatActionInterfaceStub(), PageStub())
    before = len(panel.chat_feed.controls)

    panel._render_send_result(
        _pending_message(),
        {
            "assistant": "需要分别确认两条命令。",
            "command_actions": [
                {
                    "status": "confirmation_required",
                    "audit_id": 22,
                    "command": "deop Aiden233",
                    "risk_level": "HIGH",
                },
                {
                    "status": "confirmation_required",
                    "audit_id": 23,
                    "command": "stop",
                    "risk_level": "HIGH",
                },
            ],
        },
    )

    assert len(panel.chat_feed.controls) == before + 2


def test_streamed_command_action_is_rendered_as_confirmation_card() -> None:
    notifications: list[str] = []
    panel = ChatPanel(
        StreamingCommandActionInterfaceStub(),
        PageStub(),
        on_command_audit_change=lambda: notifications.append("changed"),
    )
    before = len(panel.chat_feed.controls)

    asyncio.run(panel._stream_task("未知措辞", [], _pending_message()))

    assert notifications == ["changed"]
    assert len(panel.chat_feed.controls) == before + 1


def test_streamed_config_proposal_is_handed_to_workbench() -> None:
    routed: list[dict] = []
    interface = StreamingConfigProposalInterfaceStub()
    panel = ChatPanel(
        interface,
        PageStub(),
        on_config_proposal=routed.append,
    )
    before = len(panel.chat_feed.controls)

    asyncio.run(panel._stream_task("模糊的配置诉求", [], _pending_message()))

    assert routed == [interface.proposal]
    assert len(panel.chat_feed.controls) == before


def test_streamed_start_server_action_starts_startup_log_follow() -> None:
    server_starts: list[str] = []
    panel = ChatPanel(
        StreamingServerStartInterfaceStub(),
        PageStub(),
        on_server_start_requested=lambda: server_starts.append("server"),
    )

    asyncio.run(panel._stream_task("帮我启动服务器", [], _pending_message()))

    assert server_starts == ["server"]


def test_stream_task_shows_tool_progress() -> None:
    bubble = _pending_message()
    panel = ChatPanel(StreamingToolProgressInterfaceStub(), PageStub())

    asyncio.run(panel._stream_task("分析日志", [], bubble))

    text = _message_markdown_text(bubble)
    assert "初步结论" in text
    assert "正在调用本地工具" in text
    assert "组件诊断扫描" in text


def test_stream_task_does_not_block_async_event_loop_while_waiting_for_llm() -> None:
    async def run_case() -> None:
        interface = SlowStreamingInterfaceStub()
        panel = ChatPanel(interface, PageStub())

        started = time.monotonic()
        task = asyncio.create_task(panel._stream_task("分析日志", [], _pending_message()))
        await asyncio.sleep(0.05)
        elapsed = time.monotonic() - started

        assert elapsed < 0.15
        await task
        assert interface.finished.is_set()

    asyncio.run(run_case())
