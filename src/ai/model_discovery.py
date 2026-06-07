from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI


@dataclass(frozen=True)
class ModelDiscoveryConfig:
    provider: str
    api_key: str
    base_url: str
    timeout_seconds: int


def list_provider_model_ids(config: ModelDiscoveryConfig) -> list[str]:
    if not config.api_key or not config.base_url:
        return []
    if not config.base_url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid base URL for {config.provider}")

    response = OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        max_retries=0,
    ).models.list()
    model_ids = {
        str(model.id).strip()
        for model in getattr(response, "data", [])
        if str(getattr(model, "id", "")).strip()
    }
    return sorted(model_ids, key=str.casefold)
