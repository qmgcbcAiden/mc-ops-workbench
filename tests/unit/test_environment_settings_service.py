from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from src.service.environment_settings_service import (
    EnvironmentSettingsService,
    recommend_java_xmx,
)


class _JavaEnvironmentStub:
    def check_environment(self) -> dict:
        return {
            "status": "ok",
            "selected_java": {
                "java_path": "/detected/java",
                "major": 21,
            },
            "message": "Java 21 已匹配。",
        }


def _write_example(path: Path) -> None:
    path.write_text(
        "# keep this comment\n"
        "AI_DEFAULT_PROVIDER=deepseek\n"
        "DEEPSEEK_API_KEY=replace_me\n"
        "DEEPSEEK_BASE_URL=https://api.deepseek.com\n"
        "QWEN_API_KEY=replace_me\n"
        "QWEN_BASE_URL=https://qwen.example/v1\n"
        "MC_SERVER_DIR=mc_server\n"
        "MC_SERVER_JAR=server.jar\n"
        "MC_JAVA_PATH=java\n"
        "MC_JAVA_XMX=2G\n"
        "MC_LOG_PATH=mc_server/logs/latest.log\n"
        "MC_RCON_PASSWORD=replace_me\n"
        "ADVANCED_SETTING=keep-me\n",
        encoding="utf-8",
    )


def test_first_run_copies_template_and_generates_safe_values(tmp_path: Path) -> None:
    _write_example(tmp_path / ".env.example")

    result = EnvironmentSettingsService.ensure_environment_file(
        project_root=tmp_path,
        memory_total_bytes=lambda: 16 * 1024 ** 3,
    )
    values = dotenv_values(result.env_path)

    assert result.created is True
    assert values["DEEPSEEK_API_KEY"] == ""
    assert values["QWEN_API_KEY"] == ""
    assert values["MC_RCON_PASSWORD"] not in {"", "replace_me"}
    assert len(values["MC_RCON_PASSWORD"]) >= 24
    assert values["MC_JAVA_XMX"] == "8G"
    assert values["ADVANCED_SETTING"] == "keep-me"
    assert "# keep this comment" in result.env_path.read_text(encoding="utf-8")


def test_existing_env_is_never_replaced_during_bootstrap(tmp_path: Path) -> None:
    _write_example(tmp_path / ".env.example")
    env_path = tmp_path / ".env"
    env_path.write_text("# mine\nCUSTOM=value\n", encoding="utf-8")

    result = EnvironmentSettingsService.ensure_environment_file(project_root=tmp_path)

    assert result.created is False
    assert env_path.read_text(encoding="utf-8") == "# mine\nCUSTOM=value\n"


def test_inspect_detects_unique_server_java_and_visible_provider_keys(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "mcServer"
    server_dir.mkdir()
    (server_dir / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "AI_DEFAULT_PROVIDER=qwen\n"
        "QWEN_API_KEY=secret-value\n"
        "QWEN_BASE_URL=https://qwen.example/v1\n"
        "MC_SERVER_DIR=missing\n"
        "MC_RCON_PASSWORD=rcon-secret\n",
        encoding="utf-8",
    )

    result = EnvironmentSettingsService(
        project_root=tmp_path,
        environ={},
        java_environment_service=_JavaEnvironmentStub(),
        memory_total_bytes=lambda: 6 * 1024 ** 3,
    ).inspect()

    assert result["server_dir"] == str(server_dir)
    assert result["server_detection"]["source"] == "detected"
    assert result["server_detection"]["start_scripts"] == ["start.sh"]
    assert result["java_path"] == "/detected/java"
    assert result["recommended_java_xmx"] == "3G"
    assert result["api_key_configured"] is True
    assert result["providers"] == [
        {
            "id": "qwen",
            "kind": "builtin",
            "label": "Qwen",
            "name": "Qwen",
            "api_key": "secret-value",
            "base_url": "https://qwen.example/v1",
            "fallback_model": "qwen3.5-plus",
            "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "default_model": "qwen3.5-plus",
            "api_key_configured": True,
            "api_key_env_name": "QWEN_API_KEY",
            "base_url_env_name": "QWEN_BASE_URL",
            "model_env_name": "QWEN_MODEL",
            "name_env_name": "",
            "openai_compatible_required": True,
        }
    ]
    assert result["rcon_configured"] is True
    assert "rcon-secret" not in str(result)


def test_save_updates_only_basic_keys_and_preserves_comments_and_advanced_values(
    tmp_path: Path,
) -> None:
    _write_example(tmp_path / ".env")
    server_dir = tmp_path / "My Server"
    server_dir.mkdir()
    service = EnvironmentSettingsService(project_root=tmp_path, environ={})

    result = service.save_basic_settings(
        {
            "provider": "qwen",
            "api_key": "new-key",
            "base_url": "https://qwen.example/v1",
            "server_dir": str(server_dir),
            "java_xmx": "4G",
            "server_jar": "paper.jar",
            "java_path": "/opt/java/bin/java",
        }
    )
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    values = dotenv_values(tmp_path / ".env")

    assert result["status"] == "saved"
    assert "# keep this comment" in content
    assert values["ADVANCED_SETTING"] == "keep-me"
    assert values["AI_DEFAULT_PROVIDER"] == "qwen"
    assert values["QWEN_API_KEY"] == "new-key"
    assert values["MC_SERVER_DIR"] == "My Server"
    assert values["MC_LOG_PATH"] == "My Server/logs/latest.log"
    assert values["MC_JAVA_XMX"] == "4G"
    assert values["MC_SERVER_JAR"] == "paper.jar"
    assert values["MC_RCON_PASSWORD"] not in {"", "replace_me"}
    assert not list(tmp_path.glob(".*.tmp"))


def test_save_multiple_providers_and_custom_openai_compatible_provider(
    tmp_path: Path,
) -> None:
    _write_example(tmp_path / ".env")
    service = EnvironmentSettingsService(project_root=tmp_path, environ={})

    service.save_basic_settings(
        {
            "provider": "deepseek",
            "providers": [
                {
                    "id": "deepseek",
                    "kind": "builtin",
                    "name": "DeepSeek",
                    "api_key": "deepseek-key",
                    "base_url": "https://api.deepseek.com",
                    "fallback_model": "deepseek-reasoner",
                },
                {
                    "id": "custom_1",
                    "kind": "custom",
                    "name": "OpenAI Proxy",
                    "api_key": "proxy-key",
                    "base_url": "https://proxy.example/v1",
                    "fallback_model": "gpt-4.1-mini",
                },
            ],
            "server_dir": "mc_server",
            "java_xmx": "2G",
        }
    )

    values = dotenv_values(tmp_path / ".env")

    assert values["AI_DEFAULT_PROVIDER"] == "deepseek"
    assert values["DEEPSEEK_API_KEY"] == "deepseek-key"
    assert values["DEEPSEEK_MODEL"] == "deepseek-reasoner"
    assert values["AI_CUSTOM_PROVIDER_1_NAME"] == "OpenAI Proxy"
    assert values["AI_CUSTOM_PROVIDER_1_API_KEY"] == "proxy-key"
    assert values["AI_CUSTOM_PROVIDER_1_BASE_URL"] == "https://proxy.example/v1"
    assert values["AI_CUSTOM_PROVIDER_1_MODEL"] == "gpt-4.1-mini"


def test_save_prefers_first_configured_provider_over_empty_default(
    tmp_path: Path,
) -> None:
    _write_example(tmp_path / ".env")
    service = EnvironmentSettingsService(project_root=tmp_path, environ={})

    result = service.save_basic_settings(
        {
            "provider": "deepseek",
            "providers": [
                {
                    "id": "deepseek",
                    "kind": "builtin",
                    "name": "DeepSeek",
                    "api_key": "",
                    "base_url": "https://api.deepseek.com",
                    "fallback_model": "deepseek-v4-flash",
                },
                {
                    "id": "custom_1",
                    "kind": "custom",
                    "name": "OpenAI Proxy",
                    "api_key": "proxy-key",
                    "base_url": "https://proxy.example/v1",
                    "fallback_model": "gpt-4.1-mini",
                },
            ],
            "server_dir": "mc_server",
            "java_xmx": "2G",
        }
    )

    values = dotenv_values(tmp_path / ".env")

    assert result["provider"] == "custom_1"
    assert values["AI_DEFAULT_PROVIDER"] == "custom_1"


def test_system_environment_locked_fields_are_not_overwritten(tmp_path: Path) -> None:
    _write_example(tmp_path / ".env")
    service = EnvironmentSettingsService(
        project_root=tmp_path,
        environ={"MC_JAVA_XMX": "6G"},
    )

    result = service.save_basic_settings(
        {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "server_dir": "mc_server",
            "java_xmx": "4G",
            "server_jar": "server.jar",
            "java_path": "java",
        }
    )

    assert result["locked_fields"] == ["MC_JAVA_XMX"]
    assert dotenv_values(tmp_path / ".env")["MC_JAVA_XMX"] == "2G"
    assert service.inspect()["java_xmx"] == "6G"


def test_ai_connection_uses_supplied_secret_but_never_returns_it(tmp_path: Path) -> None:
    _write_example(tmp_path / ".env")
    captured = {}

    def list_models(config):
        captured.update(
            provider=config.provider,
            api_key=config.api_key,
            base_url=config.base_url,
        )
        return ["model-a", "model-b"]

    service = EnvironmentSettingsService(
        project_root=tmp_path,
        environ={},
        model_lister=list_models,
    )
    result = service.test_ai_connection(
        "qwen",
        "temporary-secret",
        "https://qwen.example/v1",
    )

    assert result == {
        "status": "ok",
        "provider": "qwen",
        "model_count": 2,
        "message": "连接成功，发现 2 个可用模型。",
    }
    assert captured["api_key"] == "temporary-secret"
    assert "temporary-secret" not in str(result)


def test_memory_recommendation_is_bounded_for_new_users() -> None:
    assert recommend_java_xmx(3 * 1024 ** 3) == "1G"
    assert recommend_java_xmx(4 * 1024 ** 3) == "2G"
    assert recommend_java_xmx(10 * 1024 ** 3) == "5G"
    assert recommend_java_xmx(64 * 1024 ** 3) == "8G"
