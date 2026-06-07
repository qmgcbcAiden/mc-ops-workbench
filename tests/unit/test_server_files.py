from __future__ import annotations

from pathlib import Path

import pytest

from src.mc.server_files import (
    build_file_tree,
    list_directory,
    read_text_preview,
    resolve_inside_root,
)


class TestResolveInsideRoot:
    def test_relative_path_inside_root(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        result = resolve_inside_root(root, "server.properties")
        assert str(result).startswith(str(root.resolve()))

    def test_absolute_path_outside_root_is_blocked(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        with pytest.raises(ValueError, match="Access denied"):
            resolve_inside_root(root, outside)

    def test_parent_traversal_is_blocked(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        with pytest.raises(ValueError, match="Access denied"):
            resolve_inside_root(root, "../etc/passwd")

    def test_sibling_prefix_path_is_blocked(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        sibling = tmp_path / "server_backup"
        sibling.mkdir()
        target = sibling / "secret.txt"
        target.write_text("secret", encoding="utf-8")

        with pytest.raises(ValueError, match="Access denied"):
            resolve_inside_root(root, target)


class TestListDirectory:
    def test_lists_files_and_directories(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        (root / "server.properties").write_text("port=25565\n", encoding="utf-8")
        (root / "world").mkdir()

        nodes = list_directory(root)

        names = [n.name for n in nodes]
        assert "world" in names
        assert "server.properties" in names
        assert names.index("world") < names.index("server.properties")

    def test_empty_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        nodes = list_directory(root)
        assert nodes == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "nonexistent"
        nodes = list_directory(root)
        assert nodes == []


class TestBuildFileTree:
    def test_builds_tree_with_name(self, tmp_path: Path) -> None:
        root = tmp_path / "mc_server"
        root.mkdir()
        (root / "server.properties").write_text("a=b\n", encoding="utf-8")
        tree = build_file_tree(root)
        assert tree.name == "mc_server"
        assert tree.kind == "directory"
        assert len(tree.children or []) == 1

    def test_nested_children_keep_root_relative_paths(self, tmp_path: Path) -> None:
        root = tmp_path / "mc_server"
        region = root / "world" / "region"
        region.mkdir(parents=True)
        (region / "r.0.0.mca").write_bytes(b"fake")

        tree = build_file_tree(root)
        world = next(node for node in tree.children or [] if node.name == "world")
        region_node = next(node for node in world.children or [] if node.name == "region")
        chunk = next(node for node in region_node.children or [] if node.name == "r.0.0.mca")

        assert world.relative_path == "world"
        assert region_node.relative_path == "world/region"
        assert chunk.relative_path == "world/region/r.0.0.mca"


class TestReadTextPreview:
    def test_reads_text_file(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        (root / "server.properties").write_text("a=b\n", encoding="utf-8")
        preview = read_text_preview(root, "server.properties")
        assert preview.content == "a=b\n"
        assert preview.truncated is False

    def test_eula_text_uses_properties_language(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        (root / "eula.txt").write_text("eula=true\n", encoding="utf-8")

        preview = read_text_preview(root, "eula.txt")

        assert preview.language == "properties"

    def test_truncates_large_file(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        (root / "large.txt").write_text("x" * 5000, encoding="utf-8")
        preview = read_text_preview(root, "large.txt", max_bytes=100)
        assert preview.truncated is True
        assert len(preview.content) <= 100

    def test_rejects_binary_file(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        (root / "server.jar").write_bytes(b"\x00\x01\x02")
        preview = read_text_preview(root, "server.jar")
        assert "不是安全文本文件" in preview.content

    def test_previews_shell_and_batch_scripts_with_script_languages(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "server"
        root.mkdir()
        (root / "start.sh").write_text(
            "#!/usr/bin/env sh\njava -jar server.jar nogui\n",
            encoding="utf-8",
        )
        (root / "start.bat").write_text(
            "@echo off\r\njava -jar server.jar nogui\r\n",
            encoding="utf-8",
        )

        shell_preview = read_text_preview(root, "start.sh")
        batch_preview = read_text_preview(root, "start.bat")

        assert shell_preview.previewable is True
        assert shell_preview.language == "shell"
        assert batch_preview.previewable is True
        assert batch_preview.language == "batch"

    def test_path_escape_is_blocked(self, tmp_path: Path) -> None:
        root = tmp_path / "server"
        root.mkdir()
        with pytest.raises(ValueError, match="Access denied"):
            read_text_preview(root, "../secret.txt")
