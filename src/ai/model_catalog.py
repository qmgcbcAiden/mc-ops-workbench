from __future__ import annotations

from dataclasses import dataclass


DEFAULT_AI_MODEL_ID = "deepseek-v4-flash"
SELECTED_AI_MODEL_SETTING_KEY = "selected_ai_model"


@dataclass(frozen=True)
class AiModelDefinition:
    id: str
    provider: str
    display_name: str
    short_name: str
    capability_rank: int
    requires_authentication: bool = False


AI_MODEL_CATALOG: tuple[AiModelDefinition, ...] = (
    AiModelDefinition(
        id="qwen3.7-max",
        provider="qwen",
        display_name="Qwen 3.7 Max",
        short_name="Qwen Max",
        capability_rank=100,
    ),
    AiModelDefinition(
        id="deepseek-v4-pro",
        provider="deepseek",
        display_name="DeepSeek v4 Pro",
        short_name="DS Pro",
        capability_rank=90,
    ),
    AiModelDefinition(
        id="qwen3.6-plus",
        provider="qwen",
        display_name="Qwen 3.6 Plus",
        short_name="Qwen 3.6",
        capability_rank=80,
    ),
    AiModelDefinition(
        id="qwen3.5-plus",
        provider="qwen",
        display_name="Qwen 3.5 Plus",
        short_name="Qwen 3.5",
        capability_rank=70,
    ),
    AiModelDefinition(
        id=DEFAULT_AI_MODEL_ID,
        provider="deepseek",
        display_name="DeepSeek v4 Flash",
        short_name="DS Flash",
        capability_rank=60,
    ),
)


def list_ai_model_definitions(is_authenticated: bool = True) -> list[AiModelDefinition]:
    # TODO(auth): when authentication lands, return only DEFAULT_AI_MODEL_ID for
    # unauthenticated users. During MVP development all models remain selectable.
    del is_authenticated
    return sorted(
        AI_MODEL_CATALOG,
        key=lambda model: model.capability_rank,
        reverse=True,
    )


def get_ai_model_definition(model_id: str) -> AiModelDefinition:
    for model in AI_MODEL_CATALOG:
        if model.id == model_id:
            return model
    raise ValueError(f"Unsupported AI model: {model_id}")


def is_supported_ai_model(model_id: str | None) -> bool:
    if not model_id:
        return False
    return any(model.id == model_id for model in AI_MODEL_CATALOG)


def coerce_ai_model_id(model_id: str | None) -> str:
    if is_supported_ai_model(model_id):
        return str(model_id)
    return DEFAULT_AI_MODEL_ID
