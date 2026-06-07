from __future__ import annotations

from typing import Any, Callable

import flet as ft

from src.interface.file_interface import FileInterface
from src.ui import theme

try:
    import flet_code_editor as _code_editor_module
except ImportError:
    _code_editor_module = None


_TEXT_SIZE = 13
_LINE_HEIGHT = 1.38
_CODE_GUTTER_WIDTH = 74
_CODE_GUTTER_MARGIN = 12

_LANGUAGE_CANDIDATES = {
    "batch": ("BATCH", "DOS", "POWERSHELL", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "config": ("INI", "PROPERTIES", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "csv": ("CSV", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "ini": ("INI", "PROPERTIES", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "json": ("JSON", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "json5": ("JSON", "JSON5", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "log": ("ACCESSLOG", "LOG", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "markdown": ("MARKDOWN", "MD", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "properties": ("PROPERTIES", "INI", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "shell": ("SHELL", "BASH", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "text": ("PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "toml": ("TOML", "INI", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
    "yaml": ("YAML", "YML", "PLAINTEXT", "PLAIN_TEXT", "TEXT"),
}


class FileEditor:
    def __init__(
        self,
        file_interface: FileInterface,
        page: ft.Page,
        relative_path: str,
        file_name: str,
        original_content: str,
        language: str = "text",
        on_saved: Callable[[dict], None] | None = None,
        on_state_change: Callable[["FileEditor"], None] | None = None,
    ):
        self._interface = file_interface
        self._page = page
        self.relative_path = relative_path
        self.file_name = file_name
        self.language = language
        self._original_content = original_content
        self._on_saved = on_saved
        self._on_state_change = on_state_change
        self._dirty = False
        self._saving = False
        self._status_text = ""
        self._message = ""
        self._message_color = theme.MUTED

        code_editor = _build_code_editor(
            value=original_content,
            language=language,
            on_change=self._on_text_change,
        )
        self._using_code_editor = code_editor is not None
        self._editor = code_editor if code_editor is not None else self._build_text_field(original_content)
        self._update_status()

    @property
    def uses_code_editor(self) -> bool:
        return self._using_code_editor

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def saving(self) -> bool:
        return self._saving

    @property
    def content(self) -> str:
        return str(getattr(self._editor, "value", "") or "")

    @property
    def status_text(self) -> str:
        return self._status_text

    @property
    def message(self) -> str:
        return self._message

    @property
    def message_color(self) -> str:
        return self._message_color

    @property
    def line_count(self) -> int:
        content = self.content
        return content.count("\n") + 1 if content else 1

    @property
    def byte_count(self) -> int:
        return len(self.content.encode("utf-8"))

    def _on_text_change(self, event) -> None:
        event_value = _event_value(event)
        if event_value is not None:
            self._set_content(event_value)
        self._dirty = self.content != self._original_content
        self._message = ""
        self._update_status()
        self._notify_state_change()
        self._page.update()

    def _update_status(self) -> None:
        self._status_text = "保存中" if self._saving else "未保存" if self._dirty else "已同步"

    def save(self) -> None:
        if self._saving:
            return
        content = self.content
        validation = self._interface.validate_text_file(self.relative_path, content)
        if not validation.get("valid", True):
            self._message = validation.get("error", "校验失败")
            self._message_color = theme.RED
            self._notify_state_change()
            self._page.update()
            return

        self._saving = True
        self._message = "保存中..."
        self._message_color = theme.MUTED
        self._update_status()
        self._notify_state_change()
        self._page.update()
        try:
            result = self._interface.save_text_file(self.relative_path, content)
            if result.get("status") == "saved":
                formatted_content = result.get("formatted_content", content)
                self._set_content(formatted_content)
                self._original_content = formatted_content
                self._dirty = False
                history_label = (
                    "版本与审计已记录"
                    if result.get("version_status") == "committed"
                    else "备份与审计已记录"
                    if result.get("backup_path")
                    else "审计已记录"
                )
                prefix = "已自动格式化并保存" if result.get("formatted") else "已保存"
                self._message = f"{prefix}，{history_label}。"
                self._message_color = theme.GREEN
                if self._on_saved:
                    self._on_saved(result)
            else:
                self._message = result.get("error_message", "保存失败")
                self._message_color = theme.RED
        except Exception as exc:
            self._message = f"保存失败：{exc}"
            self._message_color = theme.RED
        finally:
            self._saving = False
            self._update_status()
            self._notify_state_change()
            self._page.update()

    def format_document(self) -> None:
        result = self._interface.format_text_file(
            self.relative_path,
            self.content,
        )
        if result.get("status") == "failed":
            self._message = result.get("error_message", "格式化失败")
            self._message_color = theme.RED
            self._notify_state_change()
            self._page.update()
            return
        self._set_content(result.get("content", self.content))
        self._dirty = self.content != self._original_content
        self._message = "已格式化，保存后写入文件。"
        self._message_color = theme.GREEN
        self._update_status()
        self._notify_state_change()
        self._page.update()

    def reload(self) -> None:
        preview = self._interface.preview_file(self.relative_path)
        content = preview.get("content", "")
        self._set_content(content)
        self._original_content = content
        self._dirty = False
        self._message = "已重新加载。"
        self._message_color = theme.MUTED
        self._update_status()
        self._notify_state_change()
        self._page.update()

    def build(self) -> ft.Control:
        return ft.Container(
            content=ft.Container(
                content=self._editor,
                expand=True,
                bgcolor=theme.PANEL,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            ),
            bgcolor=theme.PANEL,
            expand=True,
        )

    def _notify_state_change(self) -> None:
        if self._on_state_change:
            self._on_state_change(self)

    def _set_content(self, content: str) -> None:
        self._editor.value = content

    def _build_text_field(self, value: str) -> ft.TextField:
        return ft.TextField(
            value=value,
            multiline=True,
            min_lines=None,
            max_lines=None,
            expand=True,
            fit_parent_size=True,
            text_size=_TEXT_SIZE,
            bgcolor=theme.PANEL,
            border=ft.InputBorder.NONE,
            border_color=theme.PANEL,
            focused_border_color=theme.PANEL,
            focused_bgcolor=theme.PANEL,
            filled=True,
            fill_color=theme.PANEL,
            focus_color=theme.PANEL,
            hover_color=theme.PANEL,
            color=theme.TEXT,
            focused_color=theme.TEXT,
            text_style=_editor_text_style(),
            strut_style=ft.StrutStyle(
                font_family="monospace",
                size=_TEXT_SIZE,
                height=_LINE_HEIGHT,
                force_strut_height=True,
            ),
            cursor_color=theme.BLUE,
            cursor_width=1.4,
            cursor_height=None,
            selection_color=theme.BLUE_SOFT,
            autocorrect=False,
            enable_suggestions=False,
            smart_dashes_type=False,
            smart_quotes_type=False,
            dense=True,
            scroll_padding=ft.Padding.all(12),
            content_padding=ft.Padding.only(left=16, top=12, right=16, bottom=12),
            on_change=self._on_text_change,
        )


def _build_code_editor(
    value: str,
    language: str,
    on_change: Callable[[Any], None],
) -> ft.Control | None:
    if _code_editor_module is None:
        return None

    code_editor_cls = getattr(_code_editor_module, "CodeEditor", None)
    if code_editor_cls is None:
        return None

    kwargs: dict[str, Any] = {
        "value": value,
        "language": _code_language_for(language),
        "on_change": on_change,
        "expand": True,
        "padding": ft.Padding.only(left=0, top=9, right=14, bottom=12),
        "text_style": _editor_text_style(),
        "gutter_style": _gutter_style(),
        "code_theme": _code_theme(),
    }
    kwargs = {key: item for key, item in kwargs.items() if item is not None}
    optional_keys = ("padding", "gutter_style", "code_theme", "text_style", "language")
    for key_count in range(0, len(optional_keys) + 1):
        attempt = {
            key: item
            for key, item in kwargs.items()
            if key not in optional_keys[:key_count]
        }
        try:
            return code_editor_cls(**attempt)
        except TypeError:
            continue
    return None


def _code_language_for(language: str) -> Any:
    language_enum = getattr(_code_editor_module, "CodeLanguage", None)
    if language_enum is None:
        return None
    normalized = (language or "text").strip().lower()
    for candidate in _LANGUAGE_CANDIDATES.get(normalized, _LANGUAGE_CANDIDATES["text"]):
        value = getattr(language_enum, candidate, None)
        if value is not None:
            return value
    return None


def _code_theme() -> Any:
    custom_theme_cls = getattr(ft, "MarkdownCustomCodeTheme", None)
    if custom_theme_cls is not None:
        base = _editor_text_style()
        return custom_theme_cls(
            root=ft.TextStyle(bgcolor=theme.PANEL, color=theme.TEXT),
            code=base,
            comment=ft.TextStyle(color=theme.SOFT_TEXT, italic=True),
            keyword=ft.TextStyle(color=theme.GREEN),
            literal=ft.TextStyle(color=theme.GREEN),
            name=ft.TextStyle(color="#79c0ff"),
            number=ft.TextStyle(color=theme.AMBER),
            operator=ft.TextStyle(color=theme.TEXT),
            string=ft.TextStyle(color=theme.GREEN),
            symbol=ft.TextStyle(color=theme.VIOLET),
            title=ft.TextStyle(color="#79c0ff"),
            variable=ft.TextStyle(color="#79c0ff"),
        )

    code_theme = getattr(_code_editor_module, "CodeTheme", None)
    if code_theme is None:
        return None
    return getattr(code_theme, "ATOM_ONE_DARK", None)


def _gutter_style() -> Any:
    gutter_style_cls = getattr(_code_editor_module, "GutterStyle", None)
    if gutter_style_cls is None:
        return None
    kwargs = {
        "show_line_numbers": True,
        "show_errors": False,
        "show_folding_handles": False,
        "width": _CODE_GUTTER_WIDTH,
        "margin": _CODE_GUTTER_MARGIN,
        "background_color": theme.PANEL,
        "text_style": ft.TextStyle(
            color=theme.MUTED,
            font_family="monospace",
            size=_TEXT_SIZE,
            letter_spacing=0,
        ),
    }
    optional_keys = ("background_color", "text_style", "margin", "width", "show_errors")
    for key_count in range(0, len(optional_keys) + 1):
        attempt = {
            key: item
            for key, item in kwargs.items()
            if key not in optional_keys[:key_count]
        }
        try:
            return gutter_style_cls(**attempt)
        except TypeError:
            continue
    return None


def _editor_text_style() -> ft.TextStyle:
    return ft.TextStyle(
        color=theme.TEXT,
        font_family="monospace",
        size=_TEXT_SIZE,
        height=_LINE_HEIGHT,
        letter_spacing=0,
    )


def _event_value(event: Any) -> str | None:
    data = getattr(event, "data", None)
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        value = data.get("value")
        if isinstance(value, str):
            return value
    control = getattr(event, "control", None)
    value = getattr(control, "value", None)
    return value if isinstance(value, str) else None
