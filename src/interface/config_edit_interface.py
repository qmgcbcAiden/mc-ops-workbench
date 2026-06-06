from __future__ import annotations

from typing import Any

from src.service.config_edit_service import ConfigEditService


class ConfigEditInterface:
    def __init__(self, config_service: ConfigEditService) -> None:
        self._service = config_service

    def list_capabilities(self) -> dict[str, Any]:
        return self._service.list_capabilities()

    def read_config_file(self, relative_path: str) -> dict[str, Any]:
        return self._service.read_config_file(relative_path)

    def get_values(self, relative_path: str, keys: list[str]) -> dict[str, Any]:
        return self._service.get_values(relative_path, keys)

    def propose_change(
        self,
        relative_path: str,
        changes: list[dict[str, Any]],
        user_request: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._service.propose_change(
            relative_path=relative_path,
            changes=changes,
            user_request=user_request,
            session_id=session_id,
        )

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return self._service.get_proposal(proposal_id)

    def list_recent_proposals(
        self,
        limit: int = 50,
        relative_path: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._service.list_recent_proposals(
            limit=limit,
            relative_path=relative_path,
        )

    def reject_proposal(
        self,
        proposal_id: str,
        confirmed_by: str = "local_user",
    ) -> dict[str, Any]:
        return self._service.reject_proposal(
            proposal_id=proposal_id,
            confirmed_by=confirmed_by,
        )

    def apply_proposal(
        self,
        proposal_id: str,
        confirmed_by: str = "local_user",
        high_risk_confirmed: bool = False,
    ) -> dict[str, Any]:
        return self._service.apply_proposal(
            proposal_id=proposal_id,
            confirmed_by=confirmed_by,
            high_risk_confirmed=high_risk_confirmed,
        )

    def rollback_change(
        self,
        proposal_id: str,
        confirmed_by: str = "local_user",
    ) -> dict[str, Any]:
        return self._service.rollback_change(
            proposal_id=proposal_id,
            confirmed_by=confirmed_by,
        )
