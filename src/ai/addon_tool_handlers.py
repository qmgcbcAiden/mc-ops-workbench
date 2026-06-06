from __future__ import annotations

import json
from typing import Any

from src.service.addon_diagnostic_service import AddonDiagnosticService


class AddonToolHandlers:
    def __init__(self, addon_service: AddonDiagnosticService) -> None:
        self._service = addon_service

    def scan_server_addons(self, args: dict[str, Any]) -> str:
        # AI-initiated scans stay local and deterministic. Explicit online refresh
        # is routed by ChatService/UI so a model cannot accidentally start slow
        # network metadata lookups during ordinary log analysis.
        return _json(self._service.scan_addons(refresh_online=False))

    def get_addon_diagnostics(self, args: dict[str, Any]) -> str:
        severity = _optional_str(args.get("severity"))
        return _json({
            "status": "ok",
            "diagnostics": self._service.list_addon_diagnostics(severity=severity),
            "report": self._service.get_latest_addon_report(),
        })

    def propose_addon_remediation(self, args: dict[str, Any]) -> str:
        diagnostic_ids = _string_list(args.get("diagnostic_ids"))
        return _json(self._service.create_addon_remediation_plan(diagnostic_ids))


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
