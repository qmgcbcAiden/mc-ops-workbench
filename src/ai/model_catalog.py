from __future__ import annotations

from dataclasses import dataclass


DEFAULT_AI_PROVIDER = "deepseek"
SELECTED_AI_MODEL_SETTING_KEY = "selected_ai_model"
ENABLED_AI_MODELS_SETTING_KEY = "enabled_ai_models"


@dataclass(frozen=True)
class AiModelDefinition:
    id: str
    provider: str
    display_name: str
    short_name: str
    selection_id: str
    source: str
    requires_authentication: bool = False


def make_ai_model_definition(
    provider: str,
    model_id: str,
    *,
    source: str = "provider",
) -> AiModelDefinition:
    normalized_provider = provider.strip().lower()
    normalized_model_id = model_id.strip()
    return AiModelDefinition(
        id=normalized_model_id,
        provider=normalized_provider,
        display_name=normalized_model_id,
        short_name=normalized_model_id,
        selection_id=encode_model_selection(normalized_provider, normalized_model_id),
        source=source,
    )


def encode_model_selection(provider: str, model_id: str) -> str:
    return f"{provider.strip().lower()}::{model_id.strip()}"


def decode_model_selection(value: str | None) -> tuple[str, str] | None:
    if not value or "::" not in value:
        return None
    provider, model_id = value.split("::", 1)
    provider = provider.strip().lower()
    model_id = model_id.strip()
    if not provider or not model_id:
        return None
    return provider, model_id
