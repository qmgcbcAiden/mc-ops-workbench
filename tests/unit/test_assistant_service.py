from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from src.ai.assistant_service import AssistantService
from src.ai.llm_client import LlmResponse
from src.ai.stream_events import ChatStreamEvent, StreamEventType
from src.ai.tool_registry import ToolRegistry
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.llm_repository import LlmRepository


TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo a value.",
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
            },
            "required": ["value"],
        },
    },
}


class InvalidJsonToolLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            return LlmResponse(
                content="",
                model=self.model,
                tool_calls=[
                    {
                        "id": "call_bad_args",
                        "name": "echo",
                        "arguments": "{",
                    }
                ],
            )
        assert any(message["role"] == "tool" for message in messages)
        return LlmResponse(content="工具参数格式有误。", model=self.model)


class StreamingToolLlm:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> Iterator[ChatStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_START)
            yield ChatStreamEvent(
                event_type=StreamEventType.TOOL_START,
                tool_name="echo",
            )
            yield ChatStreamEvent(
                event_type=StreamEventType.TOOL_RESULT,
                tool_name="echo",
                tool_arguments={
                    "id": "call_stream_echo",
                    "raw": json.dumps({"value": "ok"}),
                },
            )
            return

        assert any(message["role"] == "tool" for message in messages)
        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_START)
        yield ChatStreamEvent(event_type=StreamEventType.DELTA, text="完成")
        yield ChatStreamEvent(
            event_type=StreamEventType.MESSAGE_END,
            token_usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )


class LoopingToolLlm:
    model = "fake-model"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        return LlmResponse(
            content="",
            model=self.model,
            tool_calls=[
                {
                    "id": "call_loop",
                    "name": "echo",
                    "arguments": json.dumps({"value": "again"}),
                }
            ],
        )


class TruncatedStreamingLlm:
    model = "fake-model"

    def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> Iterator[ChatStreamEvent]:
        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_START)
        yield ChatStreamEvent(event_type=StreamEventType.DELTA, text="半句回复")
        yield ChatStreamEvent(
            event_type=StreamEventType.DELTA,
            text="\n\n> 注意：模型输出达到当前 `max_tokens` 上限，回复已被截断。当前上限为 800，可调大 `QWEN_MAX_TOKENS` 后重试。",
        )
        yield ChatStreamEvent(
            event_type=StreamEventType.MESSAGE_END,
            finish_reason="length",
            token_usage={"prompt_tokens": 10, "completion_tokens": 800, "total_tokens": 810},
        )


def _assistant(tmp_path: Path, llm: object) -> tuple[AssistantService, object]:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    registry = ToolRegistry()
    registry.register("echo", lambda args: json.dumps({"echo": args["value"]}), TOOL_SCHEMA)
    return AssistantService(llm, registry, LlmRepository(connection)), connection


def test_reply_marks_invalid_tool_arguments_failed(tmp_path: Path) -> None:
    assistant, connection = _assistant(tmp_path, InvalidJsonToolLlm())

    result = assistant.reply([{"role": "user", "content": "call echo"}])

    assert result["content"] == "工具参数格式有误。"
    assert result["trace"]["llm_call_ids"] == [1, 2]
    assert result["trace"]["tool_call_ids"] == [1]
    assert result["tool_results"][0]["tool_call_id"] == 1
    row = connection.execute(
        "SELECT status, error_type, error_message FROM tool_calls"
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error_type"] == "invalid_arguments"
    assert "合法 JSON" in row["error_message"]


def test_stream_reply_executes_tool_call_and_continues_model_response(tmp_path: Path) -> None:
    assistant, connection = _assistant(tmp_path, StreamingToolLlm())

    events = list(assistant.stream_reply([{"role": "user", "content": "call echo"}]))
    text = "".join(event.text for event in events if event.event_type == StreamEventType.DELTA)

    assert text == "完成"
    assert connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
    row = connection.execute(
        "SELECT status, provider_tool_call_id, result_json FROM tool_calls"
    ).fetchone()
    assert row["status"] == "completed"
    assert row["provider_tool_call_id"] == "call_stream_echo"
    assert '"echo": "ok"' in row["result_json"]


def test_reply_stops_after_max_tool_rounds(tmp_path: Path) -> None:
    assistant, connection = _assistant(tmp_path, LoopingToolLlm())

    result = assistant.reply([{"role": "user", "content": "loop"}])

    assert result["error"] == "max_tool_rounds_exceeded"
    assert result["trace"]["tool_call_count"] == 5
    assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 5


def test_stream_reply_records_length_finish_as_truncated(tmp_path: Path) -> None:
    assistant, connection = _assistant(tmp_path, TruncatedStreamingLlm())

    events = list(assistant.stream_reply([{"role": "user", "content": "long"}]))
    text = "".join(event.text for event in events if event.event_type == StreamEventType.DELTA)
    row = connection.execute(
        "SELECT status, error_message, completion_tokens FROM llm_calls"
    ).fetchone()

    assert "已被截断" in text
    assert row["status"] == "truncated"
    assert row["error_message"] == "Model output reached max_tokens limit."
    assert row["completion_tokens"] == 800
