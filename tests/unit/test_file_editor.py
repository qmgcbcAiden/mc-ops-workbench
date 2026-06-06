from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from src.ui import theme
from src.ui.components import file_editor as file_editor_module
from src.ui.components.file_editor import FileEditor


class PageStub:
    def update(self) -> None:
        return None


class FileInterfaceStub:
    def validate_text_file(self, _relative_path: str, _content: str) -> dict:
        return {"valid": True}

    def save_text_file(self, _relative_path: str, content: str) -> dict:
        return {
            "status": "saved",
            "formatted_content": content,
            "version_status": "committed",
        }

    def format_text_file(self, _relative_path: str, content: str) -> dict:
        return {"status": "formatted", "content": content, "formatted": False}

    def preview_file(self, _relative_path: str) -> dict:
        return {"content": "[]\n"}


def test_file_editor_uses_single_full_panel_without_inner_status_row() -> None:
    editor = FileEditor(
        FileInterfaceStub(),
        PageStub(),
        "banned-players.json",
        "banned-players.json",
        "[]\n",
        language="json",
    )

    control = editor.build()

    assert control.bgcolor == theme.PANEL
    assert control.content.content is editor._editor
    assert control.content.expand is True
    assert editor.status_text == "已同步"


def test_file_editor_uses_native_text_field_for_stable_editing() -> None:
    editor = FileEditor(
        FileInterfaceStub(),
        PageStub(),
        "ops.json",
        "ops.json",
        '[{"name":"Aiden233","level":4}]\n',
        language="json",
    )

    editor.build()

    if editor.uses_code_editor:
        assert editor._editor.__class__.__name__ == "CodeEditor"
    else:
        assert isinstance(editor._editor, ft.TextField)
        assert editor._editor.color == theme.TEXT
        assert editor._editor.focused_color == theme.TEXT
        assert editor._editor.cursor_height is None
        assert editor._editor.hover_color == theme.PANEL
        assert editor._editor.focused_bgcolor == theme.PANEL
        assert editor._editor.max_lines is None
        assert editor._editor.fit_parent_size is True
    assert not hasattr(editor, "_highlight_column")
    assert not hasattr(editor, "_code_scroll")


def test_file_editor_reports_save_feedback_to_workbench_state() -> None:
    states = []
    editor = FileEditor(
        FileInterfaceStub(),
        PageStub(),
        "ops.json",
        "ops.json",
        "[]\n",
        language="json",
        on_state_change=lambda item: states.append((item.status_text, item.message)),
    )

    editor._editor.value = '[{"name":"Aiden233"}]\n'
    editor._on_text_change(None)
    editor.save()

    assert ("未保存", "") in states
    assert editor.status_text == "已同步"
    assert "已保存" in editor.message


def test_file_editor_avoids_custom_horizontal_scroll_for_long_lines() -> None:
    long_line = (
        '[{"name":"Aiden233","uuid":"9662b76d-3df3-4aa5-b68f-422e8c2d54ab",'
        '"expiresOn":"2026-05-25 22:59:27 +0800"}]\n'
    )
    editor = FileEditor(
        FileInterfaceStub(),
        PageStub(),
        "usercache.json",
        "usercache.json",
        long_line,
        language="json",
    )

    editor.build()

    if not editor.uses_code_editor:
        assert editor._editor.hover_color == theme.PANEL
        assert editor._editor.focused_bgcolor == theme.PANEL
        assert editor._editor.max_lines is None
    assert editor._editor.expand is True
    assert editor._editor.width is None
    assert not hasattr(editor, "_code_canvas")
    assert not hasattr(editor, "_code_scroll")


def test_file_editor_uses_flet_code_editor_when_extension_is_available(monkeypatch) -> None:
    json_language = object()

    class FakeCodeEditor:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.value = kwargs["value"]
            self.expand = kwargs.get("expand")
            self.width = kwargs.get("width")

    class FakeGutterStyle:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            for key, value in kwargs.items():
                setattr(self, key, value)

    fake_module = SimpleNamespace(
        CodeEditor=FakeCodeEditor,
        CodeLanguage=SimpleNamespace(JSON=json_language, PLAINTEXT=object()),
        CodeTheme=SimpleNamespace(ATOM_ONE_DARK=object()),
        GutterStyle=FakeGutterStyle,
    )
    monkeypatch.setattr(file_editor_module, "_code_editor_module", fake_module)

    editor = FileEditor(
        FileInterfaceStub(),
        PageStub(),
        "ops.json",
        "ops.json",
        "[]\n",
        language="json",
    )

    assert editor.uses_code_editor is True
    assert isinstance(editor._editor, FakeCodeEditor)
    assert editor._editor.kwargs["language"] is json_language
    assert editor._editor.kwargs["gutter_style"].show_errors is False
    assert editor._editor.kwargs["gutter_style"].show_folding_handles is False
    assert editor._editor.kwargs["gutter_style"].width >= 70
    assert editor._editor.kwargs["gutter_style"].margin >= 10
    assert editor._editor.kwargs["gutter_style"].background_color == theme.PANEL
    assert editor._editor.kwargs["code_theme"].root.bgcolor == theme.PANEL
    editor._on_text_change(SimpleNamespace(data='[{"name":"Aiden233"}]\n'))
    assert editor.content == '[{"name":"Aiden233"}]\n'
    assert editor.dirty is True


def test_code_editor_language_mapping_covers_minecraft_configs(monkeypatch) -> None:
    values = SimpleNamespace(
        ACCESSLOG=object(),
        INI=object(),
        JSON=object(),
        MARKDOWN=object(),
        PLAINTEXT=object(),
        PROPERTIES=object(),
        TOML=object(),
        YAML=object(),
    )
    fake_module = SimpleNamespace(CodeLanguage=values)
    monkeypatch.setattr(file_editor_module, "_code_editor_module", fake_module)

    assert file_editor_module._code_language_for("properties") is values.PROPERTIES
    assert file_editor_module._code_language_for("json5") is values.JSON
    assert file_editor_module._code_language_for("yaml") is values.YAML
    assert file_editor_module._code_language_for("toml") is values.TOML
    assert file_editor_module._code_language_for("config") is values.INI
    assert file_editor_module._code_language_for("log") is values.ACCESSLOG
    assert file_editor_module._code_language_for("markdown") is values.MARKDOWN
    assert file_editor_module._code_language_for("unknown") is values.PLAINTEXT
