from __future__ import annotations

from src.ui import theme
from src.ui.components.environment_settings_dialog import EnvironmentSettingsDialog


class _PageStub:
    def __init__(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        self.dialogs = []
        self.update_count = 0
        self.width = width
        self.height = height

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self):
        return self.dialogs.pop() if self.dialogs else None

    def update(self) -> None:
        self.update_count += 1

    def run_thread(self, callback) -> None:
        callback()


class _EnvironmentStub:
    def __init__(self) -> None:
        self.saved = []
        self.connections = []

    def inspect(self) -> dict:
        return {
            "provider": "deepseek",
            "providers": [
                {
                    "id": "deepseek",
                    "kind": "builtin",
                    "label": "DeepSeek",
                    "name": "DeepSeek",
                    "api_key": "deepseek-secret",
                    "base_url": "https://api.deepseek.com",
                    "fallback_model": "deepseek-v4-flash",
                    "api_key_configured": True,
                    "api_key_env_name": "DEEPSEEK_API_KEY",
                    "base_url_env_name": "DEEPSEEK_BASE_URL",
                    "model_env_name": "DEEPSEEK_MODEL",
                    "default_base_url": "https://api.deepseek.com",
                },
                {
                    "id": "qwen",
                    "kind": "builtin",
                    "label": "Qwen",
                    "name": "Qwen",
                    "api_key": "qwen-secret",
                    "base_url": "https://qwen.example/v1",
                    "fallback_model": "qwen-plus",
                    "api_key_configured": False,
                    "api_key_env_name": "QWEN_API_KEY",
                    "base_url_env_name": "QWEN_BASE_URL",
                    "model_env_name": "QWEN_MODEL",
                    "default_base_url": "https://qwen.example/v1",
                },
            ],
            "api_key_configured": True,
            "base_url": "https://api.deepseek.com",
            "server_dir": "/server",
            "server_detection": {"ready": True, "source": "configured"},
            "java_xmx": "4G",
            "recommended_java_xmx": "4G",
            "server_jar": "server.jar",
            "java_path": "/java",
            "java_detection": {"status": "ok", "message": "Java 21 已匹配。"},
            "rcon_configured": True,
            "locked_fields": [],
        }

    def test_ai_connection(self, provider, api_key, base_url) -> dict:
        self.connections.append((provider, api_key, base_url))
        return {"status": "ok", "message": "连接成功，发现 2 个可用模型。"}

    def save_basic_settings(self, payload) -> dict:
        self.saved.append(payload)
        return {"status": "saved", "message": "设置已保存。"}


class _JavaStub:
    def ensure_environment(self) -> dict:
        return {
            "status": "selected",
            "selected_java": {"java_path": "/detected/java"},
            "message": "已选择 Java 21。",
        }


class _ChatStub:
    def __init__(self) -> None:
        self.enabled = []

    def list_ai_models(self, include_disabled=False):
        assert include_disabled is True
        return [
            {
                "provider": "deepseek",
                "display_name": "deepseek-v4-flash",
                "selection_id": "deepseek::deepseek-v4-flash",
                "enabled": True,
            },
            {
                "provider": "qwen",
                "display_name": "qwen-max",
                "selection_id": "qwen::qwen-max",
                "enabled": False,
            },
        ]

    def set_enabled_ai_models(self, selection_ids):
        self.enabled.append(selection_ids)


def test_first_run_dialog_is_dark_and_shows_dotenv_api_keys() -> None:
    page = _PageStub()
    dialog_controller = EnvironmentSettingsDialog(
        page,
        _EnvironmentStub(),
        _JavaStub(),
        _ChatStub(),
    )

    dialog_controller.show(first_run=True)

    dialog = page.dialogs[-1]
    assert dialog.bgcolor == theme.PANEL
    assert dialog.modal is True
    assert len(dialog_controller._provider_rows) == 2
    first_provider = dialog_controller._provider_rows[0]
    assert first_provider.api_key.value == "deepseek-secret"
    assert first_provider.api_key.password is True
    assert first_provider.api_key.can_reveal_password is True
    assert first_provider.api_key_env.value == "Key: DEEPSEEK_API_KEY"
    assert dialog.actions[0].content == "稍后设置"
    assert dialog_controller._server_jar.value == "server.jar"
    assert dialog_controller._java_path.value == "/java"
    assert [checkbox.data for checkbox in dialog_controller._model_checkboxes] == [
        "deepseek::deepseek-v4-flash",
        "qwen::qwen-max",
    ]


def test_dialog_content_is_bounded_and_manual_java_action_does_not_overflow() -> None:
    page = _PageStub(width=500, height=520)
    dialog_controller = EnvironmentSettingsDialog(
        page,
        _EnvironmentStub(),
        _JavaStub(),
        _ChatStub(),
    )

    dialog_controller.show()

    dialog = page.dialogs[-1]
    assert dialog.content.width == 452
    assert dialog.content.height == 400
    assert dialog.content.content.scroll is not None

    manual_tile = dialog.content.content.controls[9]
    manual_column = manual_tile.controls[0].content
    assert manual_column.controls[0].controls[0].value == "服务端 jar"
    assert manual_column.controls[0].controls[1].controls[0] is dialog_controller._server_jar
    assert dialog_controller._server_jar.label is None
    assert dialog_controller._server_jar.expand is True
    assert manual_column.controls[1].controls[0].value == "Java 路径"
    assert manual_column.controls[1].controls[1].controls[0] is dialog_controller._java_path
    assert dialog_controller._java_path.label is None
    assert dialog_controller._java_path.expand is True
    assert manual_column.controls[2].value == "Java 21 已匹配。"
    assert manual_column.controls[3].__class__.__name__ == "Row"
    assert len(manual_column.controls[3].controls) == 1


def test_add_provider_row_uses_openai_compatible_custom_env_keys() -> None:
    page = _PageStub()
    dialog_controller = EnvironmentSettingsDialog(
        page,
        _EnvironmentStub(),
        _JavaStub(),
        _ChatStub(),
    )
    dialog_controller.show()

    dialog_controller._add_provider_row()

    custom_provider = dialog_controller._provider_rows[-1]
    assert custom_provider.provider_id == "custom_1"
    assert custom_provider.kind.value == "custom"
    assert custom_provider.name.value == "其它服务商 1"
    assert custom_provider.api_key_env.value == "Key: AI_CUSTOM_PROVIDER_1_API_KEY"
    assert custom_provider.base_url_env.value == "URL: AI_CUSTOM_PROVIDER_1_BASE_URL"
    assert custom_provider.model_env.value == "Model: AI_CUSTOM_PROVIDER_1_MODEL"


def test_save_collects_providers_models_and_notifies_host() -> None:
    page = _PageStub()
    environment = _EnvironmentStub()
    chat = _ChatStub()
    saved_results = []
    dialog_controller = EnvironmentSettingsDialog(
        page,
        environment,
        _JavaStub(),
        chat,
        on_saved=saved_results.append,
    )
    dialog_controller.show()
    dialog_controller._provider_rows[0].api_key.value = ""
    dialog_controller._model_checkboxes[1].value = True

    dialog_controller._save()

    assert environment.saved[0]["providers"][0]["api_key"] == ""
    assert environment.saved[0]["providers"][0]["base_url"] == "https://api.deepseek.com"
    assert chat.enabled == [[
        "deepseek::deepseek-v4-flash",
        "qwen::qwen-max",
    ]]
    assert saved_results == [{"status": "saved", "message": "设置已保存。"}]


def test_connection_and_java_actions_update_feedback_without_network() -> None:
    page = _PageStub()
    environment = _EnvironmentStub()
    dialog_controller = EnvironmentSettingsDialog(
        page,
        environment,
        _JavaStub(),
        _ChatStub(),
    )
    dialog_controller.show()

    row = dialog_controller._provider_rows[0]
    dialog_controller._test_connection(row)
    assert environment.connections == [
        ("deepseek", "deepseek-secret", "https://api.deepseek.com")
    ]
    assert "发现 2 个可用模型" in row.status.value
    assert row.status.color == theme.GREEN

    dialog_controller._prepare_java()
    assert dialog_controller._java_path.value == "/detected/java"
