from __future__ import annotations

import logging
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from src.ai.llm_client import LlmRequestConfig
from src.ai.model_catalog import (
    DEFAULT_AI_PROVIDER,
    ENABLED_AI_MODELS_SETTING_KEY,
    SELECTED_AI_MODEL_SETTING_KEY,
    AiModelDefinition,
    decode_model_selection,
    encode_model_selection,
    make_ai_model_definition,
)
from src.ai.model_discovery import ModelDiscoveryConfig, list_provider_model_ids
from src.config.settings import Settings
from src.repositories.app_settings_repository import AppSettingsRepository


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ProviderConfig:
    id: str
    api_key: str
    base_url: str
    fallback_model: str
    api_key_env_name: str
    base_url_env_name: str
    model_env_name: str = ""


@dataclass(frozen=True)
class _ModelCacheEntry:
    expires_at: float
    models: tuple[AiModelDefinition, ...]


class AiModelService:
    def __init__(
        self,
        settings: Settings,
        app_settings: AppSettingsRepository,
        is_authenticated: Callable[[], bool] | None = None,
        model_lister: Callable[[ModelDiscoveryConfig], list[str]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._app_settings = app_settings
        self._is_authenticated = is_authenticated or (lambda: True)
        self._model_lister = model_lister or list_provider_model_ids
        self._clock = clock or time.monotonic
        self._cache: dict[str, _ModelCacheEntry] = {}

    def list_models(
        self,
        refresh: bool = False,
        *,
        include_disabled: bool = False,
    ) -> list[dict]:
        models = self._available_models(refresh=refresh)
        selected = self._resolve_selected_model(models=models)
        if not models:
            models = [selected]
        elif not any(model.selection_id == selected.selection_id for model in models):
            models.insert(0, selected)
        enabled_ids = self._enabled_model_ids(models, selected)
        visible_models = (
            models if include_disabled
            else [
                model for model in models
                if model.selection_id in enabled_ids
                or model.selection_id == selected.selection_id
            ]
        )
        return [
            self._model_view(
                model,
                selected_selection_id=selected.selection_id,
                enabled_selection_ids=enabled_ids,
            )
            for model in visible_models
        ]

    def refresh_models(self, *, include_disabled: bool = False) -> list[dict]:
        self._cache.clear()
        return self.list_models(refresh=True, include_disabled=include_disabled)

    def get_selected_model(self) -> dict:
        model = self._resolve_selected_model()
        return self._model_view(
            model,
            selected_selection_id=model.selection_id,
            enabled_selection_ids=self._enabled_model_ids([model], model),
        )

    def get_selected_model_id(self) -> str:
        return self._resolve_selected_model().id

    def select_model(self, selection: str) -> dict:
        model = self._find_selectable_model(selection)
        self._app_settings.set(SELECTED_AI_MODEL_SETTING_KEY, model.selection_id)
        enabled_ids = self._enabled_model_ids([model], model)
        if model.selection_id not in enabled_ids:
            enabled_ids = {*enabled_ids, model.selection_id}
            self._app_settings.set(
                ENABLED_AI_MODELS_SETTING_KEY,
                json.dumps(sorted(enabled_ids), ensure_ascii=True),
            )
        return self._model_view(
            model,
            selected_selection_id=model.selection_id,
            enabled_selection_ids=enabled_ids,
        )

    def set_enabled_models(self, selections: list[str]) -> list[dict]:
        models = self._available_models()
        known_ids = {model.selection_id for model in models}
        selected_ids = {
            str(selection).strip()
            for selection in selections
            if str(selection).strip() in known_ids
        }
        selected = self._resolve_selected_model(models=models)
        if selected.selection_id in known_ids:
            selected_ids.add(selected.selection_id)
        self._app_settings.set(
            ENABLED_AI_MODELS_SETTING_KEY,
            json.dumps(sorted(selected_ids), ensure_ascii=True),
        )
        return self.list_models(include_disabled=True)

    def get_request_config(self) -> LlmRequestConfig:
        model = self._resolve_selected_model()
        provider = self._provider_config(model.provider)
        return LlmRequestConfig(
            provider=provider.id,
            model=model.id,
            api_key=provider.api_key,
            base_url=provider.base_url,
            timeout_seconds=self._settings.qwen_timeout_seconds,
            max_tokens=self._settings.qwen_max_tokens,
            temperature=self._settings.qwen_temperature,
            api_key_env_name=provider.api_key_env_name,
            base_url_env_name=provider.base_url_env_name,
        )

    def is_selected_model_configured(self) -> bool:
        config = self.get_request_config()
        return bool(config.api_key and _is_http_url(config.base_url))

    def _resolve_selected_model(
        self,
        *,
        models: list[AiModelDefinition] | None = None,
    ) -> AiModelDefinition:
        if models is None:
            models = self._available_models()
        configured_providers = {
            provider.id
            for provider in self._provider_configs()
            if self._is_provider_configured(provider)
        }

        stored = self._app_settings.get(SELECTED_AI_MODEL_SETTING_KEY)
        stored_selection = decode_model_selection(stored)
        if stored_selection and stored_selection[0] in configured_providers:
            match = _find_model(models, *stored_selection)
            if match is not None:
                return match

        if stored and stored_selection is None:
            match = _find_legacy_model(models, stored)
            if match is not None and match.provider in configured_providers:
                return match

        enabled_ids = self._enabled_model_ids(models, fallback_selected=None)
        preferred_provider = self._settings.ai_default_provider.strip().lower()
        preferred_model = self._settings.ai_default_model.strip()
        if preferred_provider in configured_providers and preferred_model:
            match = _find_model(models, preferred_provider, preferred_model)
            if match is not None and match.selection_id in enabled_ids:
                return match

        for provider in self._provider_configs():
            if provider.id not in configured_providers:
                continue
            match = _find_model(models, provider.id, provider.fallback_model)
            if match is not None and match.selection_id in enabled_ids:
                return match
            provider_models = [
                model for model in models
                if model.provider == provider.id and model.selection_id in enabled_ids
            ]
            if provider_models:
                return provider_models[0]

        provider = self._provider_config(preferred_provider, allow_default=True)
        model_id = preferred_model or provider.fallback_model
        return make_ai_model_definition(provider.id, model_id, source="config")

    def _find_selectable_model(self, selection: str) -> AiModelDefinition:
        models = self._available_models()
        decoded = decode_model_selection(selection)
        if decoded:
            match = _find_model(models, *decoded)
        else:
            match = _find_legacy_model(models, selection)
        if match is None:
            raise ValueError(f"Unsupported or unavailable AI model: {selection}")
        return match

    def _available_models(self, refresh: bool = False) -> list[AiModelDefinition]:
        # TODO(auth): apply account-level model filtering when application
        # authentication exists. Provider credentials remain the current gate.
        self._is_authenticated()
        models: list[AiModelDefinition] = []
        for provider in self._provider_configs():
            if self._is_provider_configured(provider):
                models.extend(self._models_for_provider(provider, refresh=refresh))
        return models

    def _models_for_provider(
        self,
        provider: _ProviderConfig,
        *,
        refresh: bool,
    ) -> list[AiModelDefinition]:
        now = self._clock()
        cached = self._cache.get(provider.id)
        if not refresh and cached is not None and cached.expires_at > now:
            return list(cached.models)

        try:
            discovered_ids = self._model_lister(
                ModelDiscoveryConfig(
                    provider=provider.id,
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                    timeout_seconds=self._settings.ai_model_discovery_timeout_seconds,
                )
            )
            models = [
                make_ai_model_definition(provider.id, model_id)
                for model_id in discovered_ids
                if model_id.strip()
            ]
        except Exception as exc:
            logger.warning(
                "Could not discover %s models; using configured fallback model: %s",
                provider.id,
                exc,
            )
            models = []

        if not models and provider.fallback_model.strip():
            models = [
                make_ai_model_definition(
                    provider.id,
                    provider.fallback_model,
                    source="config",
                )
            ]

        ttl = max(0, self._settings.ai_model_cache_ttl_seconds)
        self._cache[provider.id] = _ModelCacheEntry(
            expires_at=now + ttl,
            models=tuple(models),
        )
        return models

    def _provider_configs(self) -> tuple[_ProviderConfig, ...]:
        return (
            _ProviderConfig(
                id="deepseek",
                api_key=self._settings.deepseek_api_key,
                base_url=self._settings.deepseek_base_url,
                fallback_model=self._settings.deepseek_model,
                api_key_env_name="DEEPSEEK_API_KEY",
                base_url_env_name="DEEPSEEK_BASE_URL",
                model_env_name="DEEPSEEK_MODEL",
            ),
            _ProviderConfig(
                id="qwen",
                api_key=self._settings.qwen_api_key,
                base_url=self._settings.qwen_base_url,
                fallback_model=self._settings.qwen_model,
                api_key_env_name="QWEN_API_KEY",
                base_url_env_name="QWEN_BASE_URL",
                model_env_name="QWEN_MODEL",
            ),
            *(
                _ProviderConfig(
                    id=provider.id,
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                    fallback_model=provider.fallback_model,
                    api_key_env_name=provider.api_key_env_name,
                    base_url_env_name=provider.base_url_env_name,
                    model_env_name=provider.model_env_name,
                )
                for provider in getattr(self._settings, "ai_custom_providers", ())
            ),
        )

    def _provider_config(
        self,
        provider_id: str,
        *,
        allow_default: bool = False,
    ) -> _ProviderConfig:
        normalized = provider_id.strip().lower()
        for provider in self._provider_configs():
            if provider.id == normalized:
                return provider
        if allow_default:
            return next(
                provider
                for provider in self._provider_configs()
                if provider.id == DEFAULT_AI_PROVIDER
            )
        raise ValueError(f"Unsupported AI provider: {provider_id}")

    def _model_view(
        self,
        model: AiModelDefinition,
        *,
        selected_selection_id: str,
        enabled_selection_ids: set[str],
    ) -> dict:
        provider = self._provider_config(model.provider)
        return {
            "id": model.id,
            "selection_id": model.selection_id,
            "provider": model.provider,
            "display_name": model.display_name,
            "short_name": model.short_name,
            "source": model.source,
            "requires_authentication": model.requires_authentication,
            "configured": self._is_provider_configured(provider),
            "enabled": model.selection_id in enabled_selection_ids,
            "selected": model.selection_id == selected_selection_id,
        }

    @staticmethod
    def _is_provider_configured(provider: _ProviderConfig) -> bool:
        return bool(provider.api_key and _is_http_url(provider.base_url))

    def _enabled_model_ids(
        self,
        models: list[AiModelDefinition],
        fallback_selected: AiModelDefinition | None,
    ) -> set[str]:
        known_ids = {model.selection_id for model in models}
        stored = self._app_settings.get(ENABLED_AI_MODELS_SETTING_KEY)
        if stored:
            try:
                decoded = json.loads(stored)
            except json.JSONDecodeError:
                decoded = []
            enabled = {
                str(item).strip()
                for item in decoded
                if str(item).strip() in known_ids
            }
            if enabled:
                return enabled
        if fallback_selected is not None:
            return {fallback_selected.selection_id}
        fallback_ids = {
            model.selection_id
            for model in models
            if any(
                provider.id == model.provider
                and provider.fallback_model == model.id
                for provider in self._provider_configs()
            )
        }
        if fallback_ids:
            return fallback_ids
        return {models[0].selection_id} if models else set()


def _find_model(
    models: list[AiModelDefinition],
    provider: str,
    model_id: str,
) -> AiModelDefinition | None:
    selection_id = encode_model_selection(provider, model_id)
    return next(
        (model for model in models if model.selection_id == selection_id),
        None,
    )


def _find_legacy_model(
    models: list[AiModelDefinition],
    model_id: str,
) -> AiModelDefinition | None:
    matches = [model for model in models if model.id == model_id]
    if len(matches) == 1:
        return matches[0]
    return None


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))
