from __future__ import annotations

import pytest

from src.ai.model_catalog import (
    DEFAULT_AI_MODEL_ID,
    coerce_ai_model_id,
    get_ai_model_definition,
    list_ai_model_definitions,
)


def test_models_are_listed_by_capability_descending() -> None:
    models = list_ai_model_definitions()

    assert [model.id for model in models] == [
        "qwen3.7-max",
        "deepseek-v4-pro",
        "qwen3.6-plus",
        "qwen3.5-plus",
        "deepseek-v4-flash",
    ]
    assert [model.capability_rank for model in models] == sorted(
        [model.capability_rank for model in models],
        reverse=True,
    )


def test_unauthenticated_filter_entry_currently_allows_all_models() -> None:
    authenticated = list_ai_model_definitions(is_authenticated=True)
    unauthenticated = list_ai_model_definitions(is_authenticated=False)

    assert [model.id for model in unauthenticated] == [model.id for model in authenticated]


def test_model_lookup_and_fallback() -> None:
    assert get_ai_model_definition("deepseek-v4-pro").provider == "deepseek"
    assert coerce_ai_model_id("missing-model") == DEFAULT_AI_MODEL_ID

    with pytest.raises(ValueError, match="Unsupported AI model"):
        get_ai_model_definition("missing-model")
