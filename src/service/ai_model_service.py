from __future__ import annotations

from collections.abc import Callable

from src.ai.llm_client import LlmRequestConfig
from src.ai.model_catalog import (
    SELECTED_AI_MODEL_SETTING_KEY,
    AiModelDefinition,
    coerce_ai_model_id,
    get_ai_model_definition,
    list_ai_model_definitions,
)
from src.config.settings import Settings
from src.repositories.app_settings_repository import AppSettingsRepository


class AiModelService:
    def __init__(
        self,
        settings: Settings,
        app_settings: AppSettingsRepository,
        is_authenticated: Callable[[], bool] | None = None,
    ) -> None:
        self._settings = settings
        self._app_settings = app_settings
        self._is_authenticated = is_authenticated or (lambda: True)

    def list_models(self) -> list[dict]:
        selected_id = self.get_selected_model_id()
        return [
            self._model_view(model, selected_id=selected_id)
            for model in list_ai_model_definitions(
                is_authenticated=self._is_authenticated(),
            )
        ]

    def get_selected_model(self) -> dict:
        return self._model_view(
            get_ai_model_definition(self.get_selected_model_id()),
            selected_id=self.get_selected_model_id(),
        )

    def get_selected_model_id(self) -> str:
        stored = self._app_settings.get(SELECTED_AI_MODEL_SETTING_KEY)
        if stored:
            return coerce_ai_model_id(stored)
        return coerce_ai_model_id(self._settings.ai_default_model)

    def select_model(self, model_id: str) -> dict:
        model = get_ai_model_definition(model_id)
        self._app_settings.set(SELECTED_AI_MODEL_SETTING_KEY, model.id)
        return self._model_view(model, selected_id=model.id)

    def get_request_config(self) -> LlmRequestConfig:
        model = get_ai_model_definition(self.get_selected_model_id())
        if model.provider == "deepseek":
            return LlmRequestConfig(
                provider=model.provider,
                model=model.id,
                api_key=self._settings.deepseek_api_key,
                base_url=self._settings.deepseek_base_url,
                timeout_seconds=self._settings.qwen_timeout_seconds,
                max_tokens=self._settings.qwen_max_tokens,
                temperature=self._settings.qwen_temperature,
                api_key_env_name="DEEPSEEK_API_KEY",
                base_url_env_name="DEEPSEEK_BASE_URL",
            )
        if model.provider == "qwen":
            return LlmRequestConfig(
                provider=model.provider,
                model=model.id,
                api_key=self._settings.qwen_api_key,
                base_url=self._settings.qwen_base_url,
                timeout_seconds=self._settings.qwen_timeout_seconds,
                max_tokens=self._settings.qwen_max_tokens,
                temperature=self._settings.qwen_temperature,
                api_key_env_name="QWEN_API_KEY",
                base_url_env_name="QWEN_BASE_URL",
            )
        raise ValueError(f"Unsupported AI provider: {model.provider}")

    def is_selected_model_configured(self) -> bool:
        config = self.get_request_config()
        return bool(config.api_key and _is_http_url(config.base_url))

    def _model_view(self, model: AiModelDefinition, selected_id: str) -> dict:
        return {
            "id": model.id,
            "provider": model.provider,
            "display_name": model.display_name,
            "short_name": model.short_name,
            "capability_rank": model.capability_rank,
            "requires_authentication": model.requires_authentication,
            "configured": self._is_model_configured(model),
            "selected": model.id == selected_id,
        }

    def _is_model_configured(self, model: AiModelDefinition) -> bool:
        if model.provider == "deepseek":
            return bool(
                self._settings.deepseek_api_key
                and _is_http_url(self._settings.deepseek_base_url)
            )
        if model.provider == "qwen":
            return bool(
                self._settings.qwen_api_key
                and _is_http_url(self._settings.qwen_base_url)
            )
        return False


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))
