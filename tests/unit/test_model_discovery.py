from __future__ import annotations

from types import SimpleNamespace

from src.ai.model_discovery import ModelDiscoveryConfig, list_provider_model_ids


def test_model_discovery_reads_and_sorts_provider_models(monkeypatch) -> None:
    captured = {}

    class FakeModels:
        def list(self):
            return SimpleNamespace(
                data=[
                    SimpleNamespace(id="model-z"),
                    SimpleNamespace(id="model-a"),
                    SimpleNamespace(id="model-a"),
                ]
            )

    class FakeOpenAI:
        def __init__(self, api_key, base_url, timeout, max_retries):
            captured.update(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=max_retries,
            )
            self.models = FakeModels()

    monkeypatch.setattr("src.ai.model_discovery.OpenAI", FakeOpenAI)

    models = list_provider_model_ids(
        ModelDiscoveryConfig(
            provider="qwen",
            api_key="secret",
            base_url="https://example.com/v1",
            timeout_seconds=12,
        )
    )

    assert models == ["model-a", "model-z"]
    assert captured == {
        "api_key": "secret",
        "base_url": "https://example.com/v1",
        "timeout": 12,
        "max_retries": 0,
    }


def test_model_discovery_skips_unconfigured_provider(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("OpenAI client should not be created")

    monkeypatch.setattr("src.ai.model_discovery.OpenAI", fail_if_called)

    assert list_provider_model_ids(
        ModelDiscoveryConfig(
            provider="deepseek",
            api_key="",
            base_url="https://api.deepseek.com",
            timeout_seconds=30,
        )
    ) == []
