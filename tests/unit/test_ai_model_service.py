from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import load_settings
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.app_settings_repository import AppSettingsRepository
from src.service.ai_model_service import AiModelService


def _service(
    tmp_path: Path,
    environ: dict[str, str] | None = None,
    *,
    model_lister=None,
    clock=None,
) -> tuple[AiModelService, AppSettingsRepository]:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ=environ or {},
        project_root=tmp_path,
    )
    repo = AppSettingsRepository(connection)
    return (
        AiModelService(
            settings,
            repo,
            model_lister=model_lister,
            clock=clock,
        ),
        repo,
    )


def test_unconfigured_service_uses_non_network_default(tmp_path: Path) -> None:
    service, _repo = _service(
        tmp_path,
        model_lister=lambda _config: pytest.fail("discovery should not run"),
    )

    selected = service.get_selected_model()

    assert selected["id"] == "deepseek-v4-flash"
    assert selected["provider"] == "deepseek"
    assert selected["configured"] is False


def test_only_qwen_key_automatically_selects_qwen(tmp_path: Path) -> None:
    discovered = []

    def list_models(config):
        discovered.append(config.provider)
        return ["qwen-a", "qwen-b"]

    service, _repo = _service(
        tmp_path,
        {
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
            "QWEN_MODEL": "qwen-b",
            "AI_DEFAULT_PROVIDER": "deepseek",
            "AI_DEFAULT_MODEL": "deepseek-v4-flash",
        },
        model_lister=list_models,
    )

    selected = service.get_selected_model()
    config = service.get_request_config()

    assert discovered == ["qwen"]
    assert selected["provider"] == "qwen"
    assert selected["id"] == "qwen-b"
    assert config.api_key == "qwen-key"
    assert config.model == "qwen-b"


def test_only_deepseek_key_automatically_selects_deepseek(tmp_path: Path) -> None:
    service, _repo = _service(
        tmp_path,
        {
            "DEEPSEEK_API_KEY": "deepseek-key",
            "DEEPSEEK_BASE_URL": "https://deepseek.example.com",
            "DEEPSEEK_MODEL": "deepseek-reasoner",
            "AI_DEFAULT_PROVIDER": "qwen",
            "AI_DEFAULT_MODEL": "qwen-missing",
        },
        model_lister=lambda config: ["deepseek-v4-flash", "deepseek-reasoner"],
    )

    selected = service.get_selected_model()
    config = service.get_request_config()

    assert selected["provider"] == "deepseek"
    assert selected["id"] == "deepseek-reasoner"
    assert config.api_key == "deepseek-key"
    assert config.model == "deepseek-reasoner"


def test_models_are_discovered_for_each_configured_provider(tmp_path: Path) -> None:
    provider_models = {
        "deepseek": ["deepseek-v4-flash", "deepseek-reasoner"],
        "qwen": ["qwen-plus", "qwen-max"],
    }
    service, _repo = _service(
        tmp_path,
        {
            "DEEPSEEK_API_KEY": "deepseek-key",
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
        },
        model_lister=lambda config: provider_models[config.provider],
    )

    models = service.list_models(include_disabled=True)

    assert [(model["provider"], model["id"]) for model in models] == [
        ("deepseek", "deepseek-v4-flash"),
        ("deepseek", "deepseek-reasoner"),
        ("qwen", "qwen-plus"),
        ("qwen", "qwen-max"),
    ]
    assert all(model["source"] == "provider" for model in models)
    assert [model["enabled"] for model in models] == [True, False, False, False]


def test_enabled_models_limit_default_model_menu(tmp_path: Path) -> None:
    provider_models = {
        "deepseek": ["deepseek-v4-flash", "deepseek-reasoner"],
        "qwen": ["qwen-plus", "qwen-max"],
    }
    service, _repo = _service(
        tmp_path,
        {
            "DEEPSEEK_API_KEY": "deepseek-key",
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
        },
        model_lister=lambda config: provider_models[config.provider],
    )

    service.set_enabled_models(["deepseek::deepseek-reasoner", "qwen::qwen-max"])
    models = service.list_models()

    assert [(model["provider"], model["id"]) for model in models] == [
        ("deepseek", "deepseek-v4-flash"),
        ("deepseek", "deepseek-reasoner"),
        ("qwen", "qwen-max"),
    ]
    assert all(model["enabled"] for model in models)


def test_selected_model_persists_provider_qualified_value(tmp_path: Path) -> None:
    service, repo = _service(
        tmp_path,
        {
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
        },
        model_lister=lambda _config: ["qwen-plus", "qwen-max"],
    )

    selected = service.select_model("qwen::qwen-max")

    assert selected["id"] == "qwen-max"
    assert repo.get("selected_ai_model") == "qwen::qwen-max"
    assert service.get_selected_model_id() == "qwen-max"


def test_legacy_selected_model_id_is_still_supported(tmp_path: Path) -> None:
    service, repo = _service(
        tmp_path,
        {
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
        },
        model_lister=lambda _config: ["qwen-plus", "qwen-max"],
    )
    repo.set("selected_ai_model", "qwen-max")

    assert service.get_selected_model()["selection_id"] == "qwen::qwen-max"


def test_stored_model_from_unconfigured_provider_falls_back(tmp_path: Path) -> None:
    service, repo = _service(
        tmp_path,
        {
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
            "QWEN_MODEL": "qwen-plus",
        },
        model_lister=lambda _config: ["qwen-plus", "qwen-max"],
    )
    repo.set("selected_ai_model", "deepseek::deepseek-v4-flash")

    selected = service.get_selected_model()

    assert selected["provider"] == "qwen"
    assert selected["id"] == "qwen-plus"


def test_discovery_failure_uses_provider_configured_fallback(tmp_path: Path) -> None:
    service, _repo = _service(
        tmp_path,
        {
            "DEEPSEEK_API_KEY": "deepseek-key",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
        },
        model_lister=lambda _config: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    models = service.list_models()

    assert [(model["id"], model["source"]) for model in models] == [
        ("deepseek-v4-flash", "config")
    ]
    assert service.is_selected_model_configured() is True


def test_model_discovery_is_cached_and_can_be_refreshed(tmp_path: Path) -> None:
    calls = []
    now = [100.0]

    def list_models(_config):
        calls.append(len(calls))
        return [f"model-{len(calls)}"]

    service, _repo = _service(
        tmp_path,
        {
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
            "AI_MODEL_CACHE_TTL_SECONDS": "60",
        },
        model_lister=list_models,
        clock=lambda: now[0],
    )

    assert service.list_models()[0]["id"] == "model-1"
    assert service.list_models()[0]["id"] == "model-1"
    assert len(calls) == 1

    now[0] = 161.0
    assert service.list_models()[0]["id"] == "model-2"
    assert len(calls) == 2

    assert service.refresh_models()[0]["id"] == "model-3"
    assert len(calls) == 3


def test_non_discovery_model_view_uses_fallback_without_network(tmp_path: Path) -> None:
    service, _repo = _service(
        tmp_path,
        {
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
            "QWEN_MODEL": "qwen-plus",
        },
        model_lister=lambda _config: pytest.fail("discovery should be deferred"),
    )

    models = service.list_models(include_disabled=True, discover=False)
    selected = service.get_selected_model(discover=False)

    assert [(model["id"], model["source"]) for model in models] == [
        ("qwen-plus", "config")
    ]
    assert selected["selection_id"] == "qwen::qwen-plus"


def test_non_discovery_request_config_honors_stored_model_selection(
    tmp_path: Path,
) -> None:
    service, repo = _service(
        tmp_path,
        {
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
            "QWEN_MODEL": "qwen-plus",
        },
        model_lister=lambda _config: pytest.fail("discovery should be deferred"),
    )
    repo.set("selected_ai_model", "qwen::qwen-max")

    config = service.get_request_config()

    assert config.provider == "qwen"
    assert config.model == "qwen-max"


def test_enabled_models_can_be_saved_before_new_provider_is_reloaded(
    tmp_path: Path,
) -> None:
    service, repo = _service(tmp_path)

    service.set_enabled_models(["qwen::qwen-plus", "qwen::qwen-max"])

    assert repo.get("enabled_ai_models") == (
        '["qwen::qwen-max", "qwen::qwen-plus"]'
    )


def test_select_model_rejects_unknown_model(tmp_path: Path) -> None:
    service, _repo = _service(
        tmp_path,
        {
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example.com/v1",
        },
        model_lister=lambda _config: ["qwen-plus"],
    )

    with pytest.raises(ValueError, match="Unsupported or unavailable AI model"):
        service.select_model("qwen::missing-model")
