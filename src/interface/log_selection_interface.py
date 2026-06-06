from __future__ import annotations

from src.service.log_selection_service import LogSelection, LogSelectionService


class LogSelectionInterface:
    def __init__(self, selection_service: LogSelectionService):
        self._service = selection_service

    def create_attachment(self, session_id: str, selection: LogSelection) -> dict:
        return self._service.create_attachment(
            session_id=session_id,
            selection=selection,
        )

    def analyze_attachment(self, attachment_id: str) -> dict | None:
        return self._service.analyze_attachment(attachment_id)
