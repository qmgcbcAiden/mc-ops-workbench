from __future__ import annotations

import hashlib

from src.config.settings import Settings
from src.mc.server_files import (
    build_file_tree,
    content_hash,
    format_text_file as _format_text_file,
    is_editable,
    is_versioned_text_config,
    list_directory,
    read_text_preview,
    save_text_file as _save_text_file,
    validate_text_file as _validate_text_file,
)
from src.repositories.file_edit_repository import FileEditAuditRepository
from src.service.config_version_service import ConfigVersionService


class FileService:
    def __init__(
        self,
        settings: Settings,
        edit_repo: FileEditAuditRepository | None = None,
        version_service: ConfigVersionService | None = None,
    ) -> None:
        self._root = settings.mc_server_dir
        self._max_depth = settings.mc_file_tree_max_depth
        self._max_preview_bytes = settings.mc_file_preview_max_bytes
        self._max_edit_bytes = settings.mc_editable_file_max_bytes
        self._backup_on_save = settings.mc_config_backup_on_save
        self._edit_repo = edit_repo
        self._version_service = version_service

    def get_tree(self) -> dict:
        tree = build_file_tree(self._root, self._max_depth)
        return tree.to_dict()

    def list_directory(self, relative_path: str = "") -> list[dict]:
        nodes = list_directory(self._root, relative_path)
        return [node.to_dict() for node in nodes]

    def preview_file(self, relative_path: str) -> dict:
        preview = read_text_preview(self._root, relative_path, self._max_preview_bytes)
        result = preview.to_dict()
        result["editable"] = is_editable(relative_path)
        return result

    def save_text_file(
        self,
        relative_path: str,
        content: str,
        create_backup: bool | None = None,
        track_version: bool = True,
    ) -> dict:
        if not is_editable(relative_path):
            return {
                "status": "failed",
                "relative_path": relative_path,
                "error_message": "此文件类型不可编辑保存。",
            }

        size_before = 0
        try:
            file_path = self._root / relative_path
            if file_path.is_file():
                size_before = file_path.stat().st_size
        except OSError:
            pass

        versioned = bool(
            track_version
            and self._version_service
            and is_versioned_text_config(relative_path)
        )
        version_ready = False
        baseline_result: dict | None = None
        if versioned:
            baseline_result = self._prepare_version_baseline(relative_path)
            version_ready = baseline_result.get("status") in {"committed", "current", "missing"}

        backup_requested = self._backup_on_save if create_backup is None else create_backup
        result = _save_text_file(
            self._root,
            relative_path,
            content,
            max_bytes=self._max_edit_bytes,
            create_backup=backup_requested and not version_ready,
        )

        if result["status"] == "saved" and version_ready:
            version_result = self._snapshot_text_edit(relative_path)
            result["version_status"] = version_result.get("status")
            result["version_commit_id"] = version_result.get("commit_id")
            result["version_error_message"] = version_result.get("error")
        elif versioned and baseline_result:
            result["version_status"] = baseline_result.get("status")
            result["version_commit_id"] = baseline_result.get("commit_id")
            result["version_error_message"] = baseline_result.get("error")

        # Audit
        if self._edit_repo:
            self._edit_repo.create(
                relative_path=relative_path,
                size_before=result.get("size_before", size_before),
                size_after=result.get("size_after", 0),
                status=result["status"],
                backup_path=result.get("backup_path"),
                error_message=result.get("error_message"),
            )

        return result

    def _prepare_version_baseline(self, relative_path: str) -> dict:
        try:
            return self._version_service.ensure_baseline_snapshot(
                self._root / relative_path,
                relative_path,
            )
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def _snapshot_text_edit(self, relative_path: str) -> dict:
        try:
            return self._version_service.snapshot_file(
                source_path=self._root / relative_path,
                relative_path=relative_path,
                actor="user",
                source="user",
                message=f"edit({relative_path}): saved",
            )
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def validate_text_file(self, relative_path: str, content: str) -> dict:
        return _validate_text_file(relative_path, content)

    def format_text_file(self, relative_path: str, content: str) -> dict:
        return _format_text_file(relative_path, content)

    def file_hash(self, relative_path: str) -> str | None:
        try:
            return content_hash(
                read_text_preview(self._root, relative_path, self._max_preview_bytes).content
            )
        except Exception:
            return None

    def list_edit_audits(self, relative_path: str, limit: int = 20) -> list[dict]:
        if not self._edit_repo:
            return []
        return self._edit_repo.list_for_path(relative_path, limit=limit)
