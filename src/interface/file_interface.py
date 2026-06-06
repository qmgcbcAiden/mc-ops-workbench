from __future__ import annotations

from src.service.file_service import FileService


class FileInterface:
    def __init__(self, file_service: FileService) -> None:
        self._service = file_service

    def get_tree(self) -> dict:
        return self._service.get_tree()

    def list_directory(self, relative_path: str = "") -> list[dict]:
        return self._service.list_directory(relative_path)

    def preview_file(self, relative_path: str) -> dict:
        return self._service.preview_file(relative_path)

    def save_text_file(self, relative_path: str, content: str) -> dict:
        return self._service.save_text_file(relative_path, content)

    def validate_text_file(self, relative_path: str, content: str) -> dict:
        return self._service.validate_text_file(relative_path, content)

    def format_text_file(self, relative_path: str, content: str) -> dict:
        return self._service.format_text_file(relative_path, content)

    def file_hash(self, relative_path: str) -> str | None:
        return self._service.file_hash(relative_path)

    def list_edit_audits(self, relative_path: str, limit: int = 20) -> list[dict]:
        return self._service.list_edit_audits(relative_path, limit=limit)
