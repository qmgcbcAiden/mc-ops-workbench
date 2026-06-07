from __future__ import annotations

from src.ai.model_catalog import (
    decode_model_selection,
    encode_model_selection,
    make_ai_model_definition,
)


def test_model_definition_uses_provider_qualified_selection_id() -> None:
    model = make_ai_model_definition("QWEN", "qwen-example")

    assert model.id == "qwen-example"
    assert model.provider == "qwen"
    assert model.selection_id == "qwen::qwen-example"
    assert model.display_name == "qwen-example"


def test_model_selection_round_trip() -> None:
    encoded = encode_model_selection("deepseek", "deepseek-v4-flash")

    assert encoded == "deepseek::deepseek-v4-flash"
    assert decode_model_selection(encoded) == ("deepseek", "deepseek-v4-flash")
    assert decode_model_selection("legacy-model-id") is None
