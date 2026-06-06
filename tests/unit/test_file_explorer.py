from __future__ import annotations

from src.ui.components.file_explorer import FileExplorer


class _FileInterfaceStub:
    def __init__(self) -> None:
        self.root_items = [
            {"name": "config", "relative_path": "config", "kind": "directory"},
        ]
        self.config_items = [
            {"name": "server.properties", "relative_path": "config/server.properties", "kind": "file"},
        ]
        self.calls: list[str] = []

    def list_directory(self, relative_path: str = "") -> list[dict]:
        self.calls.append(relative_path)
        if relative_path == "config":
            return list(self.config_items)
        return list(self.root_items)


def test_refresh_tree_reloads_expanded_directory_cache() -> None:
    interface = _FileInterfaceStub()
    explorer = FileExplorer(interface)
    explorer.load_tree()
    explorer._toggle_directory("config")
    assert [node["name"] for node in explorer._dir_cache["config"]] == ["server.properties"]

    interface.config_items = [
        {"name": "server.properties", "relative_path": "config/server.properties", "kind": "file"},
        {"name": "whitelist.json", "relative_path": "config/whitelist.json", "kind": "file"},
    ]

    explorer.refresh_tree()

    assert [node["name"] for node in explorer._dir_cache["config"]] == [
        "server.properties",
        "whitelist.json",
    ]
    assert interface.calls[-2:] == ["", "config"]
