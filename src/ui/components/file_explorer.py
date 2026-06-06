from __future__ import annotations

import flet as ft

from src.interface.file_interface import FileInterface
from src.ui import theme


class FileExplorer:
    def __init__(self, file_interface: FileInterface, on_file_selected=None) -> None:
        self._interface = file_interface
        self._on_file_selected = on_file_selected
        self._expanded: set[str] = set()
        self._selected_path: str | None = None
        self._dir_cache: dict[str, list[dict]] = {}
        self._tree_list = ft.ListView(expand=True, spacing=0, padding=0)
        self._control = ft.Container(
            content=self._tree_list,
            height=270,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

    @property
    def control(self) -> ft.Control:
        return self._control

    def load_tree(self) -> None:
        self.refresh_tree()

    def refresh_tree(self) -> None:
        cached_paths = ["", *sorted(self._expanded)]
        refreshed_cache: dict[str, list[dict]] = {}
        for path in cached_paths:
            try:
                refreshed_cache[path] = self._interface.list_directory(path)
            except Exception:
                refreshed_cache[path] = []
        self._dir_cache = refreshed_cache
        self._tree_list.controls = self._build_children("", depth=0)

    def _build_children(self, parent_path: str, depth: int) -> list[ft.Control]:
        children = self._dir_cache.get(parent_path, [])
        controls: list[ft.Control] = []
        for node in children:
            path = node.get("relative_path", "")
            is_dir = node.get("kind") == "directory"
            controls.append(self._build_node(node, depth))
            if is_dir and path in self._expanded and path in self._dir_cache:
                controls.extend(self._build_children(path, depth + 1))
        return controls

    def _build_node(self, node: dict, depth: int) -> ft.Control:
        path = node.get("relative_path", "")
        kind = node.get("kind", "file")
        name = node.get("name", "")
        is_dir = kind == "directory"
        is_expanded = path in self._expanded
        is_selected = path == self._selected_path
        indent = 7 + depth * 14

        icon_map = {
            ".properties": ft.Icons.SETTINGS,
            ".json": ft.Icons.DATA_OBJECT,
            ".json5": ft.Icons.DATA_OBJECT,
            ".mcmeta": ft.Icons.DATA_OBJECT,
            ".log": ft.Icons.ARTICLE,
            ".txt": ft.Icons.ARTICLE,
            ".jar": ft.Icons.ARCHIVE,
        }
        suffix = name[name.rfind("."):].lower() if "." in name else ""
        icon = icon_map.get(suffix, ft.Icons.FOLDER if is_dir else ft.Icons.INSERT_DRIVE_FILE)

        row_controls: list[ft.Control] = []
        if is_dir:
            row_controls.append(
                ft.Icon(
                    ft.Icons.KEYBOARD_ARROW_DOWN if is_expanded else ft.Icons.KEYBOARD_ARROW_RIGHT,
                    size=13, color=theme.MUTED,
                )
            )
        else:
            row_controls.append(ft.Container(width=13))

        row_controls.append(ft.Icon(icon, size=13, color=theme.AMBER if is_dir else theme.MUTED))
        row_controls.append(
            ft.Text(name, size=12, color=theme.TEXT if is_selected else theme.MUTED, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        )

        return ft.Container(
            content=ft.Row(controls=row_controls, spacing=6),
            padding=ft.Padding.only(left=indent, right=7, top=4, bottom=4),
            border_radius=7,
            bgcolor=theme.PANEL_RAISED if is_selected else None,
            on_click=lambda _: self._on_node_click(path, is_dir),
        )

    def _rebuild_tree(self) -> None:
        self._tree_list.controls = self._build_children("", depth=0)

    def _toggle_directory(self, path: str) -> None:
        if path in self._expanded:
            self._expanded.discard(path)
        else:
            if path not in self._dir_cache:
                children = self._interface.list_directory(path)
                self._dir_cache[path] = children
            self._expanded.add(path)
        self._rebuild_tree()

    def _on_node_click(self, path: str, is_dir: bool) -> None:
        if is_dir:
            self._toggle_directory(path)
        else:
            self._selected_path = path
            self._rebuild_tree()
            if self._on_file_selected:
                self._on_file_selected(path)
