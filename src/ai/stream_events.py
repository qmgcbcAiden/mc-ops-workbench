from __future__ import annotations

from dataclasses import dataclass, field


class StreamEventType:
    MESSAGE_START = "message_start"
    DELTA = "delta"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    COMMAND_ACTION = "command_action"
    CONFIG_PROPOSAL = "config_proposal"
    SERVER_ACTION = "server_action"
    MESSAGE_END = "message_end"
    ERROR = "error"


@dataclass
class ChatStreamEvent:
    event_type: str
    text: str = ""
    tool_name: str | None = None
    tool_arguments: dict | None = None
    tool_result: str | None = None
    command_action: dict | None = None
    config_proposal: dict | None = None
    server_action: dict | None = None
    error_message: str | None = None
    token_usage: dict | None = None
    finish_reason: str | None = None
