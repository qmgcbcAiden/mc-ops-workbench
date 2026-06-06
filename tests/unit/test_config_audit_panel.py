from __future__ import annotations

from src.ui.components.config_audit_panel import ConfigAuditPanel


def test_config_audit_panel_renders_pending_record() -> None:
    panel = ConfigAuditPanel()

    panel.refresh([
        {
            "proposal_id": "cfgp_test",
            "created_at": "2026-05-25T12:00:00Z",
            "status": "pending",
            "risk_level": "HIGH",
            "relative_path": "server.properties",
            "diff": "--- server.properties\n+++ server.properties\n",
            "changes": [
                {
                    "key": "max-players",
                    "old_value": "10",
                    "new_value": "20",
                }
            ],
            "restart_required": True,
            "warnings": ["需要重启后生效"],
        }
    ])

    assert len(panel.build().content.controls) == 1


def test_config_audit_panel_renders_file_edit_audit() -> None:
    panel = ConfigAuditPanel()

    panel.refresh([
        {
            "audit_kind": "file_edit",
            "id": 7,
            "created_at": "2026-05-25T12:00:00Z",
            "status": "saved",
            "relative_path": "ops.json",
            "size_before": 2,
            "size_after": 136,
            "backup_path": None,
        }
    ])

    assert len(panel.build().content.controls) == 1
