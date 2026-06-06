from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Iterator

from src.ai.llm_client import LlmClient, LlmResponse
from src.ai.stream_events import ChatStreamEvent, StreamEventType
from src.ai.tool_registry import ToolRegistry
from src.repositories.llm_repository import LlmRepository

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5


@dataclass(frozen=True)
class ExecutedToolCall:
    tool_call_db_id: int
    tool_name: str
    arguments: dict
    result: str
    provider_tool_call_id: str
    ok: bool
    error_message: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class StreamRoundResult:
    content: str
    tool_calls: list[dict]
    token_usage: dict
    llm_call_id: int
    error_message: str | None = None


class AssistantService:
    def __init__(
        self,
        llm_client: LlmClient,
        tool_registry: ToolRegistry,
        llm_repo: LlmRepository,
    ):
        self._llm = llm_client
        self._tools = tool_registry
        self._llm_repo = llm_repo

    def reply(
        self,
        messages: list[dict],
        session_id: str | None = None,
        turn_id: str | None = None,
        context_snapshot_id: int | None = None,
    ) -> dict:
        """Non-streaming reply. Returns full response dict."""
        tools = self._tools.get_schemas() or None
        llm_call_ids: list[int] = []
        tool_call_ids: list[int] = []

        try:
            call_id, response = self._chat_once(
                messages,
                tools,
                purpose="chat",
                session_id=session_id,
                turn_id=turn_id,
                context_snapshot_id=context_snapshot_id,
            )
            llm_call_ids.append(call_id)
            tool_results: list[dict] = []

            tool_rounds = 0
            while response.tool_calls:
                if tool_rounds >= MAX_TOOL_ROUNDS:
                    return {
                        "content": "工具调用轮数超过上限，已停止执行以避免循环。",
                        "model": response.model,
                        "token_usage": response.token_usage,
                        "tool_results": tool_results,
                        "error": "max_tool_rounds_exceeded",
                        "trace": _trace(llm_call_ids, tool_call_ids),
                    }
                tool_rounds += 1
                self._append_assistant_tool_request(
                    messages,
                    content=response.content,
                    tool_calls=response.tool_calls,
                )

                for tc in response.tool_calls:
                    executed = self._execute_tool_call(
                        call_id,
                        tc,
                        session_id=session_id,
                        turn_id=turn_id,
                        context_snapshot_id=context_snapshot_id,
                    )
                    tool_call_ids.append(executed.tool_call_db_id)
                    tool_results.append({
                        "tool_name": executed.tool_name,
                        "arguments": executed.arguments,
                        "result": executed.result,
                        "tool_call_id": executed.tool_call_db_id,
                        "provider_tool_call_id": executed.provider_tool_call_id,
                        "ok": executed.ok,
                        "error_message": executed.error_message,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": executed.provider_tool_call_id,
                        "content": executed.result,
                    })

                call_id, response = self._chat_once(
                    messages,
                    tools,
                    purpose="chat",
                    session_id=session_id,
                    turn_id=turn_id,
                    context_snapshot_id=context_snapshot_id,
                )
                llm_call_ids.append(call_id)

            return {
                "content": response.content,
                "model": response.model,
                "token_usage": response.token_usage,
                "tool_results": tool_results,
                "trace": _trace(llm_call_ids, tool_call_ids),
            }
        except Exception as exc:
            return {
                "content": f"AI 服务暂时不可用：{exc}",
                "model": self._llm.model,
                "error": str(exc),
                "trace": _trace(llm_call_ids, tool_call_ids),
            }

    def reply_without_tools(
        self,
        messages: list[dict],
        purpose: str = "chat_followup",
        session_id: str | None = None,
        turn_id: str | None = None,
        context_snapshot_id: int | None = None,
    ) -> dict:
        """Non-streaming reply for follow-up text after local interactive actions."""
        try:
            call_id, response = self._chat_once(
                messages,
                tools=None,
                purpose=purpose,
                session_id=session_id,
                turn_id=turn_id,
                context_snapshot_id=context_snapshot_id,
            )
            return {
                "llm_call_id": call_id,
                "content": response.content,
                "model": response.model,
                "token_usage": response.token_usage,
            }
        except Exception as exc:
            return {
                "content": f"AI 服务暂时不可用：{exc}",
                "model": self._llm.model,
                "error": str(exc),
            }

    def stream_reply(
        self,
        messages: list[dict],
        session_id: str | None = None,
        turn_id: str | None = None,
        context_snapshot_id: int | None = None,
    ) -> Iterator[ChatStreamEvent]:
        """Streaming reply. Yields ChatStreamEvent for UI consumption."""
        tools = self._tools.get_schemas() or None

        try:
            tool_rounds = 0
            while True:
                round_result = yield from self._stream_once(
                    messages,
                    tools,
                    session_id=session_id,
                    turn_id=turn_id,
                    context_snapshot_id=context_snapshot_id,
                )
                if round_result.error_message or not round_result.tool_calls:
                    return

                if tool_rounds >= MAX_TOOL_ROUNDS:
                    yield ChatStreamEvent(
                        event_type=StreamEventType.ERROR,
                        error_message="工具调用轮数超过上限，已停止执行以避免循环。",
                    )
                    return

                tool_rounds += 1
                self._append_assistant_tool_request(
                    messages,
                    content=round_result.content,
                    tool_calls=round_result.tool_calls,
                )
                for tc in round_result.tool_calls:
                    executed = self._execute_tool_call(
                        round_result.llm_call_id,
                        tc,
                        session_id=session_id,
                        turn_id=turn_id,
                        context_snapshot_id=context_snapshot_id,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": executed.provider_tool_call_id,
                        "content": executed.result,
                    })
                    yield ChatStreamEvent(
                        event_type=StreamEventType.TOOL_RESULT,
                        tool_name=executed.tool_name,
                        tool_arguments={
                            **executed.arguments,
                            "_provider_tool_call_id": executed.provider_tool_call_id,
                        },
                        tool_result=executed.result,
                    )
        except Exception as exc:
            yield ChatStreamEvent(
                event_type=StreamEventType.ERROR,
                error_message=str(exc),
            )

    def _chat_once(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        purpose: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        context_snapshot_id: int | None = None,
    ) -> tuple[int, LlmResponse]:
        call_id = self._llm_repo.create_llm_call(
            purpose=purpose,
            model=self._llm.model,
            status="started",
            session_id=session_id,
            turn_id=turn_id,
            context_snapshot_id=context_snapshot_id,
        )
        t0 = time.time()
        try:
            response = self._llm.chat(messages, tools=tools)
        except Exception as exc:
            self._llm_repo.finish_llm_call(
                call_id=call_id,
                status="failed",
                error_message=str(exc),
                latency_ms=_elapsed_ms(t0),
            )
            raise

        status = "truncated" if response.finish_reason == "length" else "completed"
        error_message = (
            "Model output reached max_tokens limit."
            if response.finish_reason == "length"
            else None
        )
        self._llm_repo.finish_llm_call(
            call_id=call_id,
            status=status,
            token_usage=response.token_usage,
            latency_ms=_elapsed_ms(t0),
            error_message=error_message,
        )
        return call_id, response

    def _stream_once(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        session_id: str | None = None,
        turn_id: str | None = None,
        context_snapshot_id: int | None = None,
    ) -> Iterator[ChatStreamEvent]:
        call_id = self._llm_repo.create_llm_call(
            purpose="chat_stream",
            model=self._llm.model,
            status="started",
            session_id=session_id,
            turn_id=turn_id,
            context_snapshot_id=context_snapshot_id,
        )
        t0 = time.time()
        content_parts: list[str] = []
        tool_calls: list[dict] = []
        token_usage: dict = {}
        finish_reason: str | None = None

        try:
            for event in self._llm.stream_chat(messages, tools=tools):
                if event.event_type == StreamEventType.DELTA:
                    content_parts.append(event.text or "")
                elif event.event_type == StreamEventType.MESSAGE_END and event.token_usage:
                    token_usage = event.token_usage
                if event.event_type == StreamEventType.MESSAGE_END and event.finish_reason:
                    finish_reason = event.finish_reason
                elif event.event_type == StreamEventType.TOOL_RESULT and event.tool_arguments:
                    raw_arguments = event.tool_arguments.get("raw", "")
                    tool_calls.append({
                        "id": event.tool_arguments.get("id") or _fallback_tool_call_id(len(tool_calls)),
                        "name": event.tool_name or "",
                        "arguments": raw_arguments,
                    })
                elif event.event_type == StreamEventType.ERROR:
                    self._llm_repo.finish_llm_call(
                        call_id=call_id,
                        status="failed",
                        error_message=event.error_message,
                        latency_ms=_elapsed_ms(t0),
                    )
                    yield event
                    return StreamRoundResult(
                        content="".join(content_parts),
                        tool_calls=tool_calls,
                        token_usage=token_usage,
                        error_message=event.error_message,
                        llm_call_id=call_id,
                    )

                yield event

            status = "truncated" if finish_reason == "length" else "completed"
            error_message = (
                "Model output reached max_tokens limit."
                if finish_reason == "length"
                else None
            )
            self._llm_repo.finish_llm_call(
                call_id=call_id,
                status=status,
                token_usage=token_usage,
                latency_ms=_elapsed_ms(t0),
                error_message=error_message,
            )
            return StreamRoundResult(
                content="".join(content_parts),
                tool_calls=tool_calls,
                token_usage=token_usage,
                llm_call_id=call_id,
            )
        except Exception as exc:
            self._llm_repo.finish_llm_call(
                call_id=call_id,
                status="failed",
                error_message=str(exc),
                latency_ms=_elapsed_ms(t0),
            )
            yield ChatStreamEvent(
                event_type=StreamEventType.ERROR,
                error_message=str(exc),
            )
            return StreamRoundResult(
                content="".join(content_parts),
                tool_calls=tool_calls,
                token_usage=token_usage,
                error_message=str(exc),
                llm_call_id=call_id,
            )

    def _execute_tool_call(
        self,
        llm_call_id: int,
        tool_call: dict,
        session_id: str | None = None,
        turn_id: str | None = None,
        context_snapshot_id: int | None = None,
    ) -> ExecutedToolCall:
        provider_tool_call_id = str(tool_call.get("id") or _fallback_tool_call_id(0))
        tool_name = str(tool_call.get("name") or "")
        raw_arguments = tool_call.get("arguments", "{}")
        if isinstance(raw_arguments, dict):
            arguments_json = json.dumps(raw_arguments, ensure_ascii=False)
        else:
            arguments_json = str(raw_arguments or "{}")

        tool_call_db_id = self._llm_repo.create_tool_call(
            llm_call_id=llm_call_id,
            tool_name=tool_name,
            arguments_json=arguments_json,
            status="started",
            provider_tool_call_id=provider_tool_call_id,
            session_id=session_id,
            turn_id=turn_id,
            context_snapshot_id=context_snapshot_id,
        )
        t0 = time.time()

        try:
            arguments = _decode_tool_arguments(arguments_json)
        except ValueError as exc:
            result = _tool_error_json(str(exc), "invalid_arguments")
            self._llm_repo.finish_tool_call(
                tool_call_id=tool_call_db_id,
                status="failed",
                result_json=result,
                error_message=str(exc),
                latency_ms=_elapsed_ms(t0),
                error_type="invalid_arguments",
            )
            return ExecutedToolCall(
                tool_call_db_id=tool_call_db_id,
                tool_name=tool_name,
                arguments={},
                result=result,
                provider_tool_call_id=provider_tool_call_id,
                ok=False,
                error_message=str(exc),
                error_type="invalid_arguments",
            )

        execution = self._tools.execute(tool_name, arguments)
        self._llm_repo.finish_tool_call(
            tool_call_id=tool_call_db_id,
            status="completed" if execution.ok else "failed",
            result_json=execution.content,
            error_message=execution.error_message,
            latency_ms=_elapsed_ms(t0),
            error_type=execution.error_type,
        )
        return ExecutedToolCall(
            tool_call_db_id=tool_call_db_id,
            tool_name=tool_name,
            arguments=arguments,
            result=execution.content,
            provider_tool_call_id=provider_tool_call_id,
            ok=execution.ok,
            error_message=execution.error_message,
            error_type=execution.error_type,
        )

    def _append_assistant_tool_request(
        self,
        messages: list[dict],
        content: str,
        tool_calls: list[dict],
    ) -> None:
        messages.append({
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": str(tc.get("id") or _fallback_tool_call_id(idx)),
                    "type": "function",
                    "function": {
                        "name": str(tc.get("name") or ""),
                        "arguments": (
                            json.dumps(tc.get("arguments"), ensure_ascii=False)
                            if isinstance(tc.get("arguments"), dict)
                            else str(tc.get("arguments") or "{}")
                        ),
                    },
                }
                for idx, tc in enumerate(tool_calls)
            ],
        })


def _decode_tool_arguments(arguments_json: str) -> dict:
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"工具参数不是合法 JSON：{exc.msg}") from exc
    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须是 JSON object。")
    return arguments


def _tool_error_json(message: str, error_type: str) -> str:
    return json.dumps({
        "status": "error",
        "error": message,
        "error_type": error_type,
    }, ensure_ascii=False)


def _trace(llm_call_ids: list[int], tool_call_ids: list[int]) -> dict:
    return {
        "llm_call_ids": list(llm_call_ids),
        "tool_call_ids": list(tool_call_ids),
        "tool_call_count": len(tool_call_ids),
    }


def _elapsed_ms(start: float) -> int:
    return int((time.time() - start) * 1000)


def _fallback_tool_call_id(index: int) -> str:
    return f"call_local_{index}"
