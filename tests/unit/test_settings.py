from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import load_settings


def test_load_settings_uses_defaults(tmp_path: Path) -> None:
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={},
        project_root=tmp_path,
    )

    assert settings.app_env == "dev"
    assert settings.db_path == tmp_path / "data/app.db"
    assert settings.flet_run_view == "desktop"
    assert settings.flet_server_host == "127.0.0.1"
    assert settings.flet_server_port == 8550
    assert settings.qwen_model == "qwen3.5-plus"
    assert settings.qwen_timeout_seconds == 30
    assert settings.qwen_max_tokens == 2400
    assert settings.qwen_temperature == 0.2
    assert settings.deepseek_api_key == ""
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.ai_default_provider == "deepseek"
    assert settings.ai_default_model == "deepseek-v4-flash"
    assert settings.ai_model_cache_ttl_seconds == 300
    assert settings.ai_model_discovery_timeout_seconds == 10
    assert settings.mc_server_dir == tmp_path / "mc_server"
    assert settings.mc_log_path == tmp_path / "mc_server/logs/latest.log"
    assert settings.mc_command_mode == "stdin"
    assert settings.mc_rcon_port == 25575
    assert settings.mc_start_timeout_seconds == 30
    assert settings.mc_stop_timeout_seconds == 30
    assert settings.config_versioning_enabled is True
    assert settings.config_version_repo_dir == tmp_path / "data/config_versions/default/repo"
    assert settings.config_auto_approve_max_risk == "NONE"
    assert settings.config_redaction_version == "v1"
    assert settings.autonomous_config_loop_enabled is False
    assert settings.autonomous_config_loop_max_rounds == 3
    assert settings.autonomous_config_loop_max_llm_calls == 8
    assert settings.autonomous_config_loop_max_tool_calls == 20
    assert settings.autonomous_config_loop_auto_apply_max_risk == "LOW"
    assert settings.autonomous_config_loop_verify_runtime is False
    assert settings.java_auto_install_enabled is True
    assert settings.java_auto_install_dir == tmp_path / "data/java"
    assert settings.java_distribution == "temurin"
    assert settings.java_package_type == "jre"


def test_load_settings_converts_environment_values(tmp_path: Path) -> None:
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={
            "APP_ENV": "test",
            "APP_DB_PATH": "runtime/test.db",
            "FLET_RUN_VIEW": "desktop",
            "FLET_SERVER_HOST": "0.0.0.0",
            "FLET_SERVER_PORT": "9000",
            "QWEN_API_KEY": "secret",
            "QWEN_BASE_URL": "https://example.com/v1",
            "QWEN_MODEL": "qwen-test",
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "DEEPSEEK_BASE_URL": "https://deepseek.example.com",
            "DEEPSEEK_MODEL": "deepseek-reasoner",
            "AI_DEFAULT_PROVIDER": "qwen",
            "AI_DEFAULT_MODEL": "qwen3.7-max",
            "AI_MODEL_CACHE_TTL_SECONDS": "45",
            "AI_MODEL_DISCOVERY_TIMEOUT_SECONDS": "7",
            "QWEN_TIMEOUT_SECONDS": "12",
            "QWEN_MAX_TOKENS": "345",
            "QWEN_TEMPERATURE": "0.5",
            "MC_RCON_PORT": "25580",
            "CONFIG_VERSIONING_ENABLED": "false",
            "CONFIG_VERSION_REPO_DIR": "runtime/config_versions/repo",
            "CONFIG_AUTO_APPROVE_MAX_RISK": "LOW",
            "CONFIG_REDACTION_VERSION": "v2",
            "AUTONOMOUS_CONFIG_LOOP_ENABLED": "true",
            "AUTONOMOUS_CONFIG_LOOP_MAX_ROUNDS": "4",
            "AUTONOMOUS_CONFIG_LOOP_MAX_LLM_CALLS": "9",
            "AUTONOMOUS_CONFIG_LOOP_MAX_TOOL_CALLS": "18",
            "AUTONOMOUS_CONFIG_LOOP_AUTO_APPLY_MAX_RISK": "MEDIUM",
            "AUTONOMOUS_CONFIG_LOOP_VERIFY_RUNTIME": "true",
            "JAVA_AUTO_INSTALL_ENABLED": "false",
            "JAVA_AUTO_INSTALL_DIR": "runtime/java",
            "JAVA_DISTRIBUTION": "temurin",
            "JAVA_PACKAGE_TYPE": "jdk",
            "AI_CUSTOM_PROVIDER_1_NAME": "OpenAI Proxy",
            "AI_CUSTOM_PROVIDER_1_API_KEY": "proxy-key",
            "AI_CUSTOM_PROVIDER_1_BASE_URL": "https://proxy.example/v1",
            "AI_CUSTOM_PROVIDER_1_MODEL": "gpt-4.1-mini",
        },
        project_root=tmp_path,
    )

    assert settings.app_env == "test"
    assert settings.db_path == tmp_path / "runtime/test.db"
    assert settings.flet_run_view == "desktop"
    assert settings.flet_server_host == "0.0.0.0"
    assert settings.flet_server_port == 9000
    assert settings.qwen_api_key == "secret"
    assert settings.qwen_base_url == "https://example.com/v1"
    assert settings.qwen_model == "qwen-test"
    assert settings.deepseek_api_key == "deepseek-secret"
    assert settings.deepseek_base_url == "https://deepseek.example.com"
    assert settings.deepseek_model == "deepseek-reasoner"
    assert settings.ai_default_provider == "qwen"
    assert settings.ai_default_model == "qwen3.7-max"
    assert settings.ai_model_cache_ttl_seconds == 45
    assert settings.ai_model_discovery_timeout_seconds == 7
    assert settings.qwen_timeout_seconds == 12
    assert settings.qwen_max_tokens == 345
    assert settings.qwen_temperature == 0.5
    assert settings.mc_rcon_port == 25580
    assert settings.config_versioning_enabled is False
    assert settings.config_version_repo_dir == tmp_path / "runtime/config_versions/repo"
    assert settings.config_auto_approve_max_risk == "LOW"
    assert settings.config_redaction_version == "v2"
    assert settings.autonomous_config_loop_enabled is True
    assert settings.autonomous_config_loop_max_rounds == 4
    assert settings.autonomous_config_loop_max_llm_calls == 9
    assert settings.autonomous_config_loop_max_tool_calls == 18
    assert settings.autonomous_config_loop_auto_apply_max_risk == "MEDIUM"
    assert settings.autonomous_config_loop_verify_runtime is True
    assert settings.java_auto_install_enabled is False
    assert settings.java_auto_install_dir == tmp_path / "runtime/java"
    assert settings.java_distribution == "temurin"
    assert settings.java_package_type == "jdk"
    assert settings.ai_custom_providers[0].id == "custom_1"
    assert settings.ai_custom_providers[0].label == "OpenAI Proxy"
    assert settings.ai_custom_providers[0].api_key == "proxy-key"
    assert settings.ai_custom_providers[0].base_url == "https://proxy.example/v1"
    assert settings.ai_custom_providers[0].fallback_model == "gpt-4.1-mini"
    assert settings.ai_custom_providers[0].api_key_env_name == "AI_CUSTOM_PROVIDER_1_API_KEY"


def test_load_settings_rejects_invalid_numbers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="QWEN_TIMEOUT_SECONDS"):
        load_settings(
            env_file=tmp_path / ".env",
            environ={"QWEN_TIMEOUT_SECONDS": "nope"},
            project_root=tmp_path,
        )


def test_explicit_environment_overrides_file_without_polluting_process(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MC_JAVA_XMX=2G\nQWEN_API_KEY=file-key\n",
        encoding="utf-8",
    )

    settings = load_settings(
        env_file=env_path,
        environ={"MC_JAVA_XMX": "6G"},
        project_root=tmp_path,
    )
    file_only = load_settings(
        env_file=env_path,
        environ={},
        project_root=tmp_path,
    )

    assert settings.mc_java_xmx == "6G"
    assert settings.qwen_api_key == "file-key"
    assert file_only.mc_java_xmx == "2G"
