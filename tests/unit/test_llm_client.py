from __future__ import annotations

from types import SimpleNamespace

from src.ai.llm_client import (
    FakeLlmClient,
    LlmClient,
    LlmRequestConfig,
    LlmRequestOptions,
    LlmResponse,
)
from src.ai.stream_events import StreamEventType


class TestFakeLlmClient:
    def test_chat_returns_deterministic_response(self):
        client = FakeLlmClient(responses=["测试回复"])
        response = client.chat([{"role": "user", "content": "你好"}])
        assert isinstance(response, LlmResponse)
        assert response.content == "测试回复"
        assert response.model == "fake-model"

    def test_chat_cycles_responses(self):
        client = FakeLlmClient(responses=["第一", "第二"])
        assert client.chat([]).content == "第一"
        assert client.chat([]).content == "第二"
        assert client.chat([]).content == "第二"  # Stays at last

    def test_stream_chat_yields_events(self):
        client = FakeLlmClient(responses=["流式测试"])
        events = list(client.stream_chat([]))
        event_types = [e.event_type for e in events]
        assert StreamEventType.MESSAGE_START in event_types
        assert StreamEventType.DELTA in event_types
        assert StreamEventType.MESSAGE_END in event_types

    def test_stream_chat_content_assembles(self):
        client = FakeLlmClient(responses=["Hello World"])
        text = ""
        for event in client.stream_chat([]):
            if event.event_type == StreamEventType.DELTA:
                text += event.text
        assert text == "Hello World"


def test_llm_client_uses_current_model_request_config(monkeypatch):
    calls = []
    selected = {"model": "deepseek-v4-flash"}

    configs = {
        "deepseek-v4-flash": LlmRequestConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key="deepseek-key",
            base_url="https://api.deepseek.com",
            timeout_seconds=12,
            max_tokens=345,
            temperature=0.3,
            api_key_env_name="DEEPSEEK_API_KEY",
            base_url_env_name="DEEPSEEK_BASE_URL",
        ),
        "qwen3.7-max": LlmRequestConfig(
            provider="qwen",
            model="qwen3.7-max",
            api_key="qwen-key",
            base_url="https://qwen.example.com/v1",
            timeout_seconds=20,
            max_tokens=678,
            temperature=0.1,
            api_key_env_name="QWEN_API_KEY",
            base_url_env_name="QWEN_BASE_URL",
        ),
    }

    class FakeCompletions:
        def create(self, **kwargs):
            calls[-1]["kwargs"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="ok", tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=1,
                    completion_tokens=2,
                    total_tokens=3,
                ),
                model=kwargs["model"],
            )

    class FakeOpenAI:
        def __init__(self, api_key, base_url, timeout, max_retries=2):
            calls.append({
                "api_key": api_key,
                "base_url": base_url,
                "timeout": timeout,
                "max_retries": max_retries,
            })
            self.chat = SimpleNamespace(
                completions=FakeCompletions(),
            )

    monkeypatch.setattr("src.ai.llm_client.OpenAI", FakeOpenAI)

    client = LlmClient(
        api_key="unused",
        base_url="https://unused.example.com",
        model="unused-model",
        model_config_provider=lambda: configs[selected["model"]],
    )
    tools = [{"type": "function", "function": {"name": "get_status"}}]

    first = client.chat([{"role": "user", "content": "hi"}], tools=tools)
    selected["model"] = "qwen3.7-max"
    second = client.chat([{"role": "user", "content": "hi"}])
    third = client.chat(
        [{"role": "user", "content": "fast"}],
        request_options=LlmRequestOptions(
            max_tokens=256,
            temperature=0.2,
            timeout_seconds=15,
            max_retries=0,
            extra_body={"enable_thinking": False},
        ),
    )

    assert first.model == "deepseek-v4-flash"
    assert calls[0]["api_key"] == "deepseek-key"
    assert calls[0]["base_url"] == "https://api.deepseek.com"
    assert calls[0]["timeout"] == 12
    assert calls[0]["kwargs"]["model"] == "deepseek-v4-flash"
    assert calls[0]["kwargs"]["max_tokens"] == 345
    assert calls[0]["kwargs"]["temperature"] == 0.3
    assert calls[0]["kwargs"]["tools"] == tools
    assert second.model == "qwen3.7-max"
    assert calls[1]["api_key"] == "qwen-key"
    assert calls[1]["base_url"] == "https://qwen.example.com/v1"
    assert calls[1]["timeout"] == 20
    assert calls[1]["kwargs"]["model"] == "qwen3.7-max"
    assert calls[1]["kwargs"]["max_tokens"] == 678
    assert calls[1]["kwargs"]["temperature"] == 0.1
    assert third.model == "qwen3.7-max"
    assert calls[2]["timeout"] == 15
    assert calls[2]["max_retries"] == 0
    assert calls[2]["kwargs"]["max_tokens"] == 256
    assert calls[2]["kwargs"]["temperature"] == 0.2
    assert calls[2]["kwargs"]["extra_body"] == {"enable_thinking": False}
