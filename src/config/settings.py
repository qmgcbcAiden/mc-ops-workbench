from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - exercised only when dependency is absent.
    def dotenv_values(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {}


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AiProviderSettings:
    id: str
    label: str
    api_key: str
    base_url: str
    fallback_model: str
    api_key_env_name: str
    base_url_env_name: str
    model_env_name: str
    kind: str = "openai_compatible"


@dataclass(frozen=True)
class Settings:
    app_env: str
    db_path: Path
    flet_run_view: str
    flet_server_host: str
    flet_server_port: int
    qwen_api_key: str
    qwen_base_url: str
    qwen_model: str
    qwen_timeout_seconds: int
    qwen_max_tokens: int
    qwen_temperature: float
    mc_server_dir: Path
    mc_server_jar: Path
    mc_java_path: str
    mc_java_xms: str
    mc_java_xmx: str
    mc_extra_args: str
    mc_log_path: Path
    mc_command_mode: str
    mc_rcon_host: str
    mc_rcon_port: int
    mc_rcon_password: str
    mc_start_timeout_seconds: int
    mc_stop_timeout_seconds: int
    qwen_log_model: str
    ai_context_max_chars: int
    ai_recent_messages_limit: int
    ai_summary_trigger_messages: int
    ai_summary_target_chars: int
    ai_log_raw_max_chars: int
    ai_log_compressed_max_chars: int
    ai_stream_enabled: bool
    mc_file_preview_max_bytes: int
    mc_file_tree_max_depth: int
    mc_editable_file_max_bytes: int
    mc_config_backup_on_save: bool
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    ai_default_provider: str = "deepseek"
    ai_default_model: str = "deepseek-v4-flash"
    ai_model_cache_ttl_seconds: int = 300
    ai_model_discovery_timeout_seconds: int = 10
    ai_custom_providers: tuple[AiProviderSettings, ...] = ()
    config_versioning_enabled: bool = False
    config_version_repo_dir: Path = PROJECT_ROOT / "data/config_versions/default/repo"
    config_auto_approve_max_risk: str = "NONE"
    config_redaction_version: str = "v1"
    autonomous_config_loop_enabled: bool = False
    autonomous_config_loop_max_rounds: int = 3
    autonomous_config_loop_max_llm_calls: int = 8
    autonomous_config_loop_max_tool_calls: int = 20
    autonomous_config_loop_auto_apply_max_risk: str = "LOW"
    autonomous_config_loop_verify_runtime: bool = False
    java_auto_install_enabled: bool = True
    java_auto_install_dir: Path = PROJECT_ROOT / "data/java"
    java_distribution: str = "temurin"
    java_package_type: str = "jre"
    addon_knowledge_online_enabled: bool = True
    modrinth_enabled: bool = True
    curseforge_api_key: str = ""
    addon_knowledge_cache_ttl_hours: int = 72
    addon_scan_max_jar_bytes: int = 104_857_600


def _as_path(value: str, root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def _as_int(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _as_float(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float") from exc


def load_settings(
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> Settings:
    if env_file is None:
        env_path = project_root / ".env"
    else:
        env_path = Path(env_file)

    file_values = {
        key: value or ""
        for key, value in dotenv_values(env_path).items()
        if key
    } if env_path.exists() else {}
    external_values = os.environ if environ is None else environ
    env = {**file_values, **external_values}

    server_dir = _as_path(env.get("MC_SERVER_DIR", "mc_server"), project_root)
    jar_value = env.get("MC_SERVER_JAR", "")
    if jar_value:
        jar_path = Path(jar_value)
        if not jar_path.is_absolute():
            jar_path = server_dir / jar_path
    else:
        jar_path = server_dir / "server.jar"

    return Settings(
        app_env=env.get("APP_ENV", "dev"),
        db_path=_as_path(env.get("APP_DB_PATH", "data/app.db"), project_root),
        flet_run_view=env.get("FLET_RUN_VIEW", "desktop"),
        flet_server_host=env.get("FLET_SERVER_HOST", "127.0.0.1"),
        flet_server_port=_as_int("FLET_SERVER_PORT", env.get("FLET_SERVER_PORT", "8550")),
        qwen_api_key=env.get("QWEN_API_KEY", ""),
        qwen_base_url=env.get("QWEN_BASE_URL", ""),
        qwen_model=env.get("QWEN_MODEL", "qwen3.5-plus"),
        qwen_timeout_seconds=_as_int(
            "QWEN_TIMEOUT_SECONDS", env.get("QWEN_TIMEOUT_SECONDS", "30")
        ),
        qwen_max_tokens=_as_int("QWEN_MAX_TOKENS", env.get("QWEN_MAX_TOKENS", "2400")),
        qwen_temperature=_as_float(
            "QWEN_TEMPERATURE", env.get("QWEN_TEMPERATURE", "0.2")
        ),
        mc_server_dir=server_dir,
        mc_server_jar=jar_path,
        mc_java_path=env.get("MC_JAVA_PATH", "java"),
        mc_java_xms=env.get("MC_JAVA_XMS", "1G"),
        mc_java_xmx=env.get("MC_JAVA_XMX", "2G"),
        mc_extra_args=env.get("MC_EXTRA_ARGS", "nogui"),
        mc_log_path=_as_path(
            env.get("MC_LOG_PATH", "mc_server/logs/latest.log"), project_root
        ),
        mc_command_mode=env.get("MC_COMMAND_MODE", "stdin"),
        mc_rcon_host=env.get("MC_RCON_HOST", "127.0.0.1"),
        mc_rcon_port=_as_int("MC_RCON_PORT", env.get("MC_RCON_PORT", "25575")),
        mc_rcon_password=env.get("MC_RCON_PASSWORD", ""),
        mc_start_timeout_seconds=_as_int(
            "MC_START_TIMEOUT_SECONDS", env.get("MC_START_TIMEOUT_SECONDS", "30")
        ),
        mc_stop_timeout_seconds=_as_int(
            "MC_STOP_TIMEOUT_SECONDS", env.get("MC_STOP_TIMEOUT_SECONDS", "30")
        ),
        qwen_log_model=env.get("QWEN_LOG_MODEL", ""),
        ai_context_max_chars=_as_int(
            "AI_CONTEXT_MAX_CHARS", env.get("AI_CONTEXT_MAX_CHARS", "24000")
        ),
        ai_recent_messages_limit=_as_int(
            "AI_RECENT_MESSAGES_LIMIT", env.get("AI_RECENT_MESSAGES_LIMIT", "16")
        ),
        ai_summary_trigger_messages=_as_int(
            "AI_SUMMARY_TRIGGER_MESSAGES", env.get("AI_SUMMARY_TRIGGER_MESSAGES", "24")
        ),
        ai_summary_target_chars=_as_int(
            "AI_SUMMARY_TARGET_CHARS", env.get("AI_SUMMARY_TARGET_CHARS", "3000")
        ),
        ai_log_raw_max_chars=_as_int(
            "AI_LOG_RAW_MAX_CHARS", env.get("AI_LOG_RAW_MAX_CHARS", "60000")
        ),
        ai_log_compressed_max_chars=_as_int(
            "AI_LOG_COMPRESSED_MAX_CHARS", env.get("AI_LOG_COMPRESSED_MAX_CHARS", "8000")
        ),
        ai_stream_enabled=env.get("AI_STREAM_ENABLED", "true").lower() == "true",
        mc_file_preview_max_bytes=_as_int(
            "MC_FILE_PREVIEW_MAX_BYTES", env.get("MC_FILE_PREVIEW_MAX_BYTES", "1048576")
        ),
        mc_file_tree_max_depth=_as_int(
            "MC_FILE_TREE_MAX_DEPTH", env.get("MC_FILE_TREE_MAX_DEPTH", "32")
        ),
        mc_editable_file_max_bytes=_as_int(
            "MC_EDITABLE_FILE_MAX_BYTES", env.get("MC_EDITABLE_FILE_MAX_BYTES", "1048576")
        ),
        mc_config_backup_on_save=env.get("MC_CONFIG_BACKUP_ON_SAVE", "true").lower() == "true",
        deepseek_api_key=env.get("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=env.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        ai_default_provider=env.get("AI_DEFAULT_PROVIDER", "deepseek"),
        ai_default_model=env.get("AI_DEFAULT_MODEL", "deepseek-v4-flash"),
        ai_model_cache_ttl_seconds=_as_int(
            "AI_MODEL_CACHE_TTL_SECONDS",
            env.get("AI_MODEL_CACHE_TTL_SECONDS", "300"),
        ),
        ai_model_discovery_timeout_seconds=_as_int(
            "AI_MODEL_DISCOVERY_TIMEOUT_SECONDS",
            env.get("AI_MODEL_DISCOVERY_TIMEOUT_SECONDS", "10"),
        ),
        ai_custom_providers=_load_custom_ai_providers(env),
        config_versioning_enabled=env.get("CONFIG_VERSIONING_ENABLED", "true").lower() == "true",
        config_version_repo_dir=_as_path(
            env.get("CONFIG_VERSION_REPO_DIR", "data/config_versions/default/repo"),
            project_root,
        ),
        config_auto_approve_max_risk=env.get("CONFIG_AUTO_APPROVE_MAX_RISK", "NONE"),
        config_redaction_version=env.get("CONFIG_REDACTION_VERSION", "v1"),
        autonomous_config_loop_enabled=env.get(
            "AUTONOMOUS_CONFIG_LOOP_ENABLED", "false"
        ).lower() == "true",
        autonomous_config_loop_max_rounds=_as_int(
            "AUTONOMOUS_CONFIG_LOOP_MAX_ROUNDS",
            env.get("AUTONOMOUS_CONFIG_LOOP_MAX_ROUNDS", "3"),
        ),
        autonomous_config_loop_max_llm_calls=_as_int(
            "AUTONOMOUS_CONFIG_LOOP_MAX_LLM_CALLS",
            env.get("AUTONOMOUS_CONFIG_LOOP_MAX_LLM_CALLS", "8"),
        ),
        autonomous_config_loop_max_tool_calls=_as_int(
            "AUTONOMOUS_CONFIG_LOOP_MAX_TOOL_CALLS",
            env.get("AUTONOMOUS_CONFIG_LOOP_MAX_TOOL_CALLS", "20"),
        ),
        autonomous_config_loop_auto_apply_max_risk=env.get(
            "AUTONOMOUS_CONFIG_LOOP_AUTO_APPLY_MAX_RISK", "LOW"
        ),
        autonomous_config_loop_verify_runtime=env.get(
            "AUTONOMOUS_CONFIG_LOOP_VERIFY_RUNTIME", "false"
        ).lower() == "true",
        java_auto_install_enabled=env.get("JAVA_AUTO_INSTALL_ENABLED", "true").lower()
        == "true",
        java_auto_install_dir=_as_path(
            env.get("JAVA_AUTO_INSTALL_DIR", "data/java"),
            project_root,
        ),
        java_distribution=env.get("JAVA_DISTRIBUTION", "temurin"),
        java_package_type=env.get("JAVA_PACKAGE_TYPE", "jre"),
        addon_knowledge_online_enabled=env.get(
            "ADDON_KNOWLEDGE_ONLINE_ENABLED", "true"
        ).lower()
        == "true",
        modrinth_enabled=env.get("MODRINTH_ENABLED", "true").lower() == "true",
        curseforge_api_key=env.get("CURSEFORGE_API_KEY", ""),
        addon_knowledge_cache_ttl_hours=_as_int(
            "ADDON_KNOWLEDGE_CACHE_TTL_HOURS",
            env.get("ADDON_KNOWLEDGE_CACHE_TTL_HOURS", "72"),
        ),
        addon_scan_max_jar_bytes=_as_int(
            "ADDON_SCAN_MAX_JAR_BYTES",
            env.get("ADDON_SCAN_MAX_JAR_BYTES", "104857600"),
        ),
    )


def _load_custom_ai_providers(env: Mapping[str, str]) -> tuple[AiProviderSettings, ...]:
    providers: list[AiProviderSettings] = []
    for index in range(1, 6):
        prefix = f"AI_CUSTOM_PROVIDER_{index}"
        name_key = f"{prefix}_NAME"
        api_key_key = f"{prefix}_API_KEY"
        base_url_key = f"{prefix}_BASE_URL"
        model_key = f"{prefix}_MODEL"
        values = (
            env.get(name_key, ""),
            env.get(api_key_key, ""),
            env.get(base_url_key, ""),
            env.get(model_key, ""),
        )
        if not any(str(value).strip() for value in values):
            continue
        provider_id = f"custom_{index}"
        label = str(env.get(name_key, "")).strip() or f"其它服务商 {index}"
        providers.append(
            AiProviderSettings(
                id=provider_id,
                label=label,
                api_key=env.get(api_key_key, ""),
                base_url=env.get(base_url_key, ""),
                fallback_model=env.get(model_key, ""),
                api_key_env_name=api_key_key,
                base_url_env_name=base_url_key,
                model_env_name=model_key,
            )
        )
    return tuple(providers)
