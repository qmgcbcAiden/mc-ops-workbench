from __future__ import annotations

import time
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Iterator

from openai import OpenAI
from openai import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    APITimeoutError,
)
from openai.types.chat import ChatCompletionMessageParam

from src.ai.stream_events import ChatStreamEvent, StreamEventType


@dataclass
class LlmResponse:
    content: str
    model: str
    token_usage: dict = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass
class LlmToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LlmStreamDelta:
    content: str | None = None
    tool_calls: list[dict] | None = None
    finish_reason: str | None = None
    token_usage: dict | None = None


@dataclass(frozen=True)
class LlmRequestConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    timeout_seconds: int
    max_tokens: int
    temperature: float
    api_key_env_name: str = "API_KEY"
    base_url_env_name: str = "BASE_URL"


class LlmClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "deepseek-v4-flash",
        timeout_seconds: int = 60,
        max_tokens: int = 1200,
        temperature: float = 0.2,
        provider: str = "openai_compatible",
        api_key_env_name: str = "API_KEY",
        base_url_env_name: str = "BASE_URL",
        model_config_provider: Callable[[], LlmRequestConfig] | None = None,
    ):
        self._static_config = LlmRequestConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            temperature=temperature,
            api_key_env_name=api_key_env_name,
            base_url_env_name=base_url_env_name,
        )
        self._model_config_provider = model_config_provider

    @property
    def model(self) -> str:
        return self._request_config().model

    @property
    def max_tokens(self) -> int:
        return self._request_config().max_tokens

    @property
    def temperature(self) -> float:
        return self._request_config().temperature

    @property
    def timeout_seconds(self) -> int:
        return self._request_config().timeout_seconds

    def _request_config(self) -> LlmRequestConfig:
        if self._model_config_provider is not None:
            return self._model_config_provider()
        return self._static_config

    def _client(self, config: LlmRequestConfig) -> OpenAI:
        return OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LlmResponse:
        config = self._request_config()
        try:
            _ensure_configured(config)
            kwargs: dict = {
                "model": config.model,
                "messages": messages,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
            }
            if tools:
                kwargs["tools"] = tools

            response = self._client(config).chat.completions.create(**kwargs)
            choice = response.choices[0]
            content = choice.message.content or ""
            finish_reason = getattr(choice, "finish_reason", None)

            tool_calls: list[dict] = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    })

            token_usage = {}
            if response.usage:
                token_usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

            return LlmResponse(
                content=_content_with_finish_notice(content, finish_reason, config.max_tokens),
                model=response.model,
                token_usage=token_usage,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )
        except AuthenticationError as e:
            raise RuntimeError(
                f"{_provider_label(config)} API Key 无效或已过期，请检查 .env 中的 {config.api_key_env_name}"
            ) from e
        except APIConnectionError as e:
            raise RuntimeError(
                f"无法连接到 {_provider_label(config)} API（{config.base_url}），请检查 {config.base_url_env_name} 和网络连接"
            ) from e
        except APITimeoutError as e:
            raise RuntimeError(
                f"{_provider_label(config)} API 超时（{config.timeout_seconds}s），请检查网络或适当增加请求超时时间"
            ) from e
        except APIError as e:
            raise RuntimeError(
                f"大模型 API 返回错误（状态码 {e.status_code}）：{e.message}"
            ) from e
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"LLM 调用失败: {e}") from e

    def stream_chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> Iterator[ChatStreamEvent]:
        config = self._request_config()
        try:
            _ensure_configured(config)
            kwargs: dict = {
                "model": config.model,
                "messages": messages,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                kwargs["tools"] = tools

            yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_START)

            stream = self._client(config).chat.completions.create(**kwargs)
            accumulated_tool_calls: dict[int, dict] = {}
            last_finish_reason: str | None = None

            for chunk in stream:
                if not chunk.choices:
                    if chunk.usage:
                        yield ChatStreamEvent(
                            event_type=StreamEventType.MESSAGE_END,
                            token_usage={
                                "prompt_tokens": chunk.usage.prompt_tokens,
                                "completion_tokens": chunk.usage.completion_tokens,
                                "total_tokens": chunk.usage.total_tokens,
                            },
                            finish_reason=last_finish_reason,
                        )
                    continue

                choice = chunk.choices[0]
                delta = choice.delta
                finish_reason = getattr(choice, "finish_reason", None)

                if delta and delta.content:
                    yield ChatStreamEvent(
                        event_type=StreamEventType.DELTA, text=delta.content
                    )

                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "id": tc.id or "",
                                "name": "",
                                "arguments": "",
                            }
                        if tc.id:
                            accumulated_tool_calls[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            accumulated_tool_calls[idx]["name"] = tc.function.name
                            yield ChatStreamEvent(
                                event_type=StreamEventType.TOOL_START,
                                tool_name=tc.function.name,
                            )
                        if tc.function and tc.function.arguments:
                            accumulated_tool_calls[idx]["arguments"] += (
                                tc.function.arguments
                            )

                if finish_reason:
                    last_finish_reason = finish_reason

                if finish_reason == "tool_calls":
                    for tc in accumulated_tool_calls.values():
                        yield ChatStreamEvent(
                            event_type=StreamEventType.TOOL_RESULT,
                            tool_name=tc["name"],
                            tool_arguments={"raw": tc["arguments"], "id": tc["id"]},
                        )

                if finish_reason == "stop":
                    yield ChatStreamEvent(
                        event_type=StreamEventType.MESSAGE_END,
                        finish_reason=finish_reason,
                    )
                elif finish_reason and finish_reason != "tool_calls":
                    yield ChatStreamEvent(
                        event_type=StreamEventType.DELTA,
                        text=_finish_reason_notice(finish_reason, config.max_tokens),
                    )
                    yield ChatStreamEvent(
                        event_type=StreamEventType.MESSAGE_END,
                        finish_reason=finish_reason,
                    )

        except AuthenticationError as e:
            yield ChatStreamEvent(
                event_type=StreamEventType.ERROR,
                error_message=(
                    f"{_provider_label(config)} API Key 无效或已过期，"
                    f"请检查 .env 中的 {config.api_key_env_name}"
                ),
            )
        except APIConnectionError as e:
            yield ChatStreamEvent(
                event_type=StreamEventType.ERROR,
                error_message=(
                    f"无法连接到 {_provider_label(config)} API（{config.base_url}），"
                    f"请检查 {config.base_url_env_name} 和网络连接"
                ),
            )
        except APITimeoutError as e:
            yield ChatStreamEvent(
                event_type=StreamEventType.ERROR,
                error_message=(
                    f"{_provider_label(config)} API 超时（{config.timeout_seconds}s），"
                    "请检查网络或增加请求超时时间"
                ),
            )
        except APIError as e:
            yield ChatStreamEvent(
                event_type=StreamEventType.ERROR,
                error_message=f"大模型 API 返回错误（状态码 {e.status_code}）：{e.message}",
            )
        except RuntimeError as e:
            yield ChatStreamEvent(
                event_type=StreamEventType.ERROR,
                error_message=str(e),
            )
        except Exception as e:
            yield ChatStreamEvent(
                event_type=StreamEventType.ERROR,
                error_message=f"LLM 调用失败: {e}",
            )


def _ensure_configured(config: LlmRequestConfig) -> None:
    if not config.api_key or not config.base_url:
        raise RuntimeError(
            f"{_provider_label(config)} API 未配置，请检查 .env 中的 "
            f"{config.api_key_env_name} 和 {config.base_url_env_name}"
        )
    if not config.base_url.startswith(("http://", "https://")):
        raise RuntimeError(
            f"{_provider_label(config)} API Base URL 无效，请检查 .env 中的 "
            f"{config.base_url_env_name}"
        )


def _provider_label(config: LlmRequestConfig) -> str:
    labels = {
        "deepseek": "DeepSeek",
        "qwen": "Qwen",
    }
    return labels.get(config.provider, "大模型")


def _content_with_finish_notice(
    content: str,
    finish_reason: str | None,
    max_tokens: int,
) -> str:
    return content + _finish_reason_notice(finish_reason, max_tokens)


def _finish_reason_notice(finish_reason: str | None, max_tokens: int) -> str:
    if finish_reason == "length":
        return (
            "\n\n> 注意：模型输出达到当前 `max_tokens` 上限，回复已被截断。"
            f"当前上限为 {max_tokens}，可调大 `QWEN_MAX_TOKENS` 后重试。"
        )
    if finish_reason and finish_reason not in {"stop", "tool_calls"}:
        return f"\n\n> 注意：模型以 `{finish_reason}` 结束，回复可能不完整。"
    return ""


class FakeLlmClient(LlmClient):
    """Fake LLM client for testing. Returns deterministic responses."""

    def __init__(self, responses: list[str] | None = None):
        super().__init__(
            api_key="fake-key",
            base_url="http://fake.test/v1",
            model="fake-model",
        )
        self._responses = responses or ["这是一条测试回复。"]
        self._call_count = 0

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LlmResponse:
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return LlmResponse(
            content=self._responses[idx],
            model="fake-model",
            token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    def stream_chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> Iterator[ChatStreamEvent]:
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        content = self._responses[idx]

        yield ChatStreamEvent(event_type=StreamEventType.MESSAGE_START)
        # Emit content in chunks to simulate streaming
        chunk_size = max(1, len(content) // 3)
        for i in range(0, len(content), chunk_size):
            time.sleep(0.01)  # tiny delay to simulate network
            yield ChatStreamEvent(
                event_type=StreamEventType.DELTA,
                text=content[i : i + chunk_size],
            )
        yield ChatStreamEvent(
            event_type=StreamEventType.MESSAGE_END,
            token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
