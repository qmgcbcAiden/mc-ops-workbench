from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import load_settings
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.app_settings_repository import AppSettingsRepository
from src.service.ai_model_service import AiModelService


def _service(tmp_path: Path, environ: dict[str, str] | None = None) -> AiModelService:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ=environ or {},
        project_root=tmp_path,
    )
    return AiModelService(settings, AppSettingsRepository(connection))


def test_selected_model_defaults_to_settings_value(tmp_path: Path) -> None:
    service = _service(tmp_path)

    selected = service.get_selected_model()

    assert selected["id"] == "deepseek-v4-flash"
    assert selected["selected"] is True


def test_selected_model_persists_in_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={"AI_DEFAULT_MODEL": "deepseek-v4-flash"},
        project_root=tmp_path,
    )
    repo = AppSettingsRepository(connection)

    first = AiModelService(settings, repo)
    first.select_model("qwen3.7-max")
    second = AiModelService(settings, repo)

    assert second.get_selected_model_id() == "qwen3.7-max"
    assert repo.get("selected_ai_model") == "qwen3.7-max"


def test_request_config_uses_selected_provider_credentials(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        {
            "DEEPSEEK_API_KEY": "deepseek-key",
            "DEEPSEEK_BASE_URL": "https://deepseek.example.com",
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
        },
    )

    deepseek_config = service.get_request_config()
    service.select_model("qwen3.5-plus")
    qwen_config = service.get_request_config()

    assert deepseek_config.provider == "deepseek"
    assert deepseek_config.model == "deepseek-v4-flash"
    assert deepseek_config.api_key == "deepseek-key"
    assert deepseek_config.base_url == "https://deepseek.example.com"
    assert qwen_config.provider == "qwen"
    assert qwen_config.model == "qwen3.5-plus"
    assert qwen_config.api_key == "qwen-key"
    assert qwen_config.base_url == "https://qwen.example.com/v1"


def test_select_model_rejects_unknown_model(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="Unsupported AI model"):
        service.select_model("unknown-model")
