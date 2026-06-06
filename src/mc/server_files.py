from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from src.mc.text_formatter import format_text, language_for_path


PREVIEW_EXTENSIONS = {
    ".txt",
    ".log",
    ".json",
    ".json5",
    ".mcmeta",
    ".properties",
    ".yml",
    ".yaml",
    ".toml",
    ".cfg",
    ".conf",
    ".ini",
    ".md",
    ".csv",
}

LANGUAGE_BY_EXTENSION = {
    ".cfg": "properties",
    ".conf": "config",
    ".ini": "ini",
    ".csv": "csv",
    ".json": "json",
    ".json5": "json5",
    ".mcmeta": "json",
    ".log": "log",
    ".md": "markdown",
    ".properties": "properties",
    ".toml": "toml",
    ".txt": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
}

EDITABLE_EXTENSIONS = {
    ".properties", ".json", ".json5", ".mcmeta", ".yml", ".yaml", ".toml",
    ".cfg", ".conf", ".ini", ".txt", ".md", ".csv",
}

VERSIONED_CONFIG_EXTENSIONS = {
    ".properties", ".json", ".json5", ".mcmeta", ".yml", ".yaml", ".toml",
    ".cfg", ".conf", ".ini",
}

VERSIONED_CONFIG_NAMES = {
    "eula.txt",
}

NON_EDITABLE_EXTENSIONS = {
    ".jar", ".dat", ".mca", ".ldb", ".sqlite", ".db",
    ".zip", ".png", ".jpg", ".gif", ".gz", ".tar",
}

ICON_BY_EXTENSION = {
    ".properties": "settings",
    ".json": "data_object",
    ".json5": "data_object",
    ".mcmeta": "data_object",
    ".log": "article",
    ".txt": "article",
    ".jar": "archive",
    ".yml": "article",
    ".yaml": "article",
    ".toml": "article",
    ".cfg": "article",
    ".conf": "article",
    ".ini": "article",
    ".md": "article",
    ".csv": "article",
}


@dataclass
class FileNode:
    name: str
    relative_path: str
    kind: Literal["directory", "file"]
    size_bytes: int | None
    children: list["FileNode"] | None
    modified_at: str
    icon: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "relative_path": self.relative_path,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "children": [child.to_dict() for child in self.children or []],
            "modified_at": self.modified_at,
            "icon": self.icon,
        }


@dataclass
class FilePreview:
    relative_path: str
    file_name: str
    language: str
    content: str
    truncated: bool
    size_bytes: int
    previewable: bool = True

    def to_dict(self) -> dict:
        return {
            "relative_path": self.relative_path,
            "file_name": self.file_name,
            "language": self.language,
            "content": self.content,
            "truncated": self.truncated,
            "size_bytes": self.size_bytes,
            "previewable": self.previewable,
        }


def resolve_inside_root(root: Path, requested: str | Path) -> Path:
    root_path = root.resolve(strict=False)
    requested_path = Path(requested)
    candidate = requested_path if requested_path.is_absolute() else root_path / requested_path
    resolved = candidate.resolve(strict=False)

    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"Access denied: '{requested}' escapes server directory") from exc

    return resolved


def list_directory(root: Path, relative_path: str = "") -> list[FileNode]:
    target = resolve_inside_root(root, relative_path or ".")
    if not target.is_dir():
        return []

    nodes: list[FileNode] = []
    try:
        with os.scandir(target) as entries:
            for entry in entries:
                nodes.append(_node_from_dir_entry(entry, relative_path))
    except OSError:
        return []

    return sorted(nodes, key=lambda node: (node.kind != "directory", _natural_key(node.name)))


def build_file_tree(root: Path, max_depth: int = 32) -> FileNode:
    root_path = root.resolve(strict=False)
    root_node = FileNode(
        name=root_path.name,
        relative_path="",
        kind="directory",
        size_bytes=None,
        children=[],
        modified_at=_modified_at(root_path),
        icon="folder",
    )
    root_node.children = _build_children(root_path, "", depth=0, max_depth=max_depth)
    return root_node


def read_text_preview(
    root: Path,
    relative_path: str,
    max_bytes: int = 1_048_576,
) -> FilePreview:
    file_path = resolve_inside_root(root, relative_path)
    suffix = file_path.suffix.lower()
    language = (
        language_for_path(relative_path)
        if file_path.name.lower() == "eula.txt"
        else LANGUAGE_BY_EXTENSION.get(suffix, language_for_path(relative_path))
    )

    if not file_path.is_file():
        return FilePreview(
            relative_path=relative_path,
            file_name=file_path.name,
            language=language,
            content="该路径不是可预览的文本文件。",
            truncated=False,
            size_bytes=0,
            previewable=False,
        )

    size_bytes = _file_size(file_path)
    if suffix not in PREVIEW_EXTENSIONS:
        return FilePreview(
            relative_path=relative_path,
            file_name=file_path.name,
            language=language,
            content="该文件不是安全文本文件，P3 阶段仅展示元信息。",
            truncated=False,
            size_bytes=size_bytes,
            previewable=False,
        )

    read_limit = max(0, int(max_bytes))
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        return FilePreview(
            relative_path=relative_path,
            file_name=file_path.name,
            language=language,
            content=f"无法读取该文件：{exc}",
            truncated=False,
            size_bytes=size_bytes,
            previewable=False,
        )

    truncated = len(raw) > read_limit
    chunk = raw[:read_limit] if truncated else raw
    if b"\x00" in chunk:
        return FilePreview(
            relative_path=relative_path,
            file_name=file_path.name,
            language=language,
            content="该文件包含二进制内容，P3 阶段仅展示元信息。",
            truncated=False,
            size_bytes=size_bytes,
            previewable=False,
        )

    return FilePreview(
        relative_path=relative_path,
        file_name=file_path.name,
        language=language,
        content=chunk.decode("utf-8", errors="replace").replace("\r\n", "\n"),
        truncated=truncated,
        size_bytes=size_bytes,
        previewable=True,
    )


def is_editable(relative_path: str) -> bool:
    suffix = Path(relative_path).suffix.lower()
    if suffix in EDITABLE_EXTENSIONS:
        return True
    if suffix in NON_EDITABLE_EXTENSIONS:
        return False
    return False  # Unknown extensions default to non-editable


def is_versioned_text_config(relative_path: str) -> bool:
    path = Path(relative_path)
    return (
        path.name.lower() in VERSIONED_CONFIG_NAMES
        or path.suffix.lower() in VERSIONED_CONFIG_EXTENSIONS
    )


def validate_text_file(relative_path: str, content: str) -> dict:
    suffix = Path(relative_path).suffix.lower()
    if suffix in {".json", ".mcmeta"}:
        import json
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return {
                "valid": False,
                "error": f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列 - {exc.msg}",
            }
    if suffix == ".toml":
        import tomllib
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            return {
                "valid": False,
                "error": f"TOML 格式错误：{exc}",
            }
    return {"valid": True, "error": None}


def format_text_file(relative_path: str, content: str) -> dict:
    result = format_text(relative_path, content)
    if result.error:
        return {
            "status": "failed",
            "relative_path": relative_path,
            "language": result.language,
            "error_message": result.error,
        }
    return {
        "status": "formatted",
        "relative_path": relative_path,
        "language": result.language,
        "content": result.content,
        "formatted": result.changed,
        "format_supported": result.supported,
    }


def content_hash(content: str) -> str:
    import hashlib
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def save_text_file(
    root: Path,
    relative_path: str,
    content: str,
    max_bytes: int = 1_048_576,
    create_backup: bool = True,
) -> dict:
    file_path = resolve_inside_root(root, relative_path)

    if not is_editable(relative_path):
        return {
            "status": "failed",
            "relative_path": relative_path,
            "error_message": "此文件类型不可编辑保存。",
        }

    formatting = format_text_file(relative_path, content)
    if formatting["status"] == "failed":
        return {
            "status": "failed",
            "relative_path": relative_path,
            "error_message": formatting["error_message"],
        }
    saved_content = formatting["content"]

    if len(saved_content.encode("utf-8")) > max_bytes:
        return {
            "status": "failed",
            "relative_path": relative_path,
            "error_message": f"文件内容超过最大限制 {max_bytes} 字节。",
        }

    # Validate
    validation = validate_text_file(relative_path, saved_content)
    if not validation["valid"]:
        return {
            "status": "failed",
            "relative_path": relative_path,
            "error_message": validation["error"],
        }

    size_before = _file_size(file_path)
    backup_path = None

    try:
        # Create backup if enabled and file exists
        if create_backup and file_path.is_file():
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{file_path.name}.{timestamp}.bak"
            backup_full = file_path.parent / backup_name
            backup_full.write_bytes(file_path.read_bytes())
            backup_path = str(backup_full.relative_to(root))

        # Atomic write: temp file then replace
        import tempfile
        fd, tmp_path = tempfile.mkstemp(
            dir=str(file_path.parent),
            prefix=f".{file_path.name}.",
            suffix=".tmp",
        )
        try:
            import os as _os
            _os.write(fd, saved_content.encode("utf-8"))
            _os.fsync(fd)
            _os.close(fd)
            Path(tmp_path).replace(file_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

        size_after = _file_size(file_path)

        return {
            "status": "saved",
            "relative_path": relative_path,
            "backup_path": backup_path,
            "size_before": size_before,
            "size_after": size_after,
            "formatted": formatting["formatted"],
            "formatted_content": saved_content,
            "language": formatting["language"],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "relative_path": relative_path,
            "error_message": str(exc),
            "size_before": size_before,
            "size_after": 0,
        }


def _build_children(root: Path, relative_path: str, depth: int, max_depth: int) -> list[FileNode]:
    if depth >= max_depth:
        return []

    children = list_directory(root, relative_path)
    for child in children:
        if child.kind == "directory":
            child.children = _build_children(root, child.relative_path, depth + 1, max_depth)
    return children


def _node_from_dir_entry(entry: os.DirEntry, parent_relative_path: str) -> FileNode:
    relative_path = (Path(parent_relative_path) / entry.name).as_posix() if parent_relative_path else entry.name
    try:
        is_dir = entry.is_dir(follow_symlinks=False)
        stat = entry.stat(follow_symlinks=False)
    except OSError:
        is_dir = False
        stat = None

    kind: Literal["directory", "file"] = "directory" if is_dir else "file"
    return FileNode(
        name=entry.name,
        relative_path=relative_path,
        kind=kind,
        size_bytes=None if is_dir else (stat.st_size if stat is not None else 0),
        children=[] if is_dir else None,
        modified_at=_format_timestamp(stat.st_mtime) if stat is not None else "",
        icon="folder" if is_dir else _icon_for_file(entry.name),
    )


def _natural_key(value: str) -> tuple:
    parts = re.split(r"(\d+)", value.lower())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _modified_at(path: Path) -> str:
    try:
        return _format_timestamp(path.stat().st_mtime)
    except OSError:
        return ""


def _format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1_073_741_824:
        return f"{size_bytes / 1_073_741_824:.1f} GB"
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes} B"


def _icon_for_file(name: str) -> str:
    return ICON_BY_EXTENSION.get(Path(name).suffix.lower(), "insert_drive_file")
