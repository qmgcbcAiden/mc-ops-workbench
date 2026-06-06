from __future__ import annotations

from src.service.addon_diagnostic_service import AddonDiagnosticService


class AddonDiagnosticInterface:
    def __init__(self, addon_service: AddonDiagnosticService) -> None:
        self._service = addon_service

    def scan_addons(self, refresh_online: bool = False) -> dict:
        return self._service.scan_addons(refresh_online=refresh_online)

    def get_latest_addon_report(self) -> dict:
        return self._service.get_latest_addon_report()

    def list_addon_assets(self) -> list[dict]:
        return self._service.list_addon_assets()

    def list_addon_diagnostics(self, severity: str | None = None) -> list[dict]:
        return self._service.list_addon_diagnostics(severity=severity)

    def create_addon_remediation_plan(self, diagnostic_ids: list[str]) -> dict:
        return self._service.create_addon_remediation_plan(diagnostic_ids)

    def create_runtime_validation_plan(self) -> dict:
        return self._service.create_runtime_validation_plan()
