from __future__ import annotations

import json
import zipfile
from pathlib import Path

from src.config.settings import load_settings
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.addon_diagnostic_repository import AddonDiagnosticRepository
from src.service.addon_diagnostic_service import AddonDiagnosticService


def test_scan_persists_assets_diagnostics_and_remediation(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    mods_dir = server_dir / "mods"
    mods_dir.mkdir(parents=True)
    (server_dir / "fabric-1.20.1.jar").write_bytes(b"server")
    _jar(
        mods_dir / "ClientOnly.jar",
        {
            "fabric.mod.json": json.dumps({
                "id": "clientonly",
                "version": "1.0.0",
                "environment": "client",
                "depends": {"minecraft": "1.20.1"},
            }),
        },
    )
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    try:
        settings = load_settings(
            environ={
                "APP_DB_PATH": str(db_path),
                "MC_SERVER_DIR": str(server_dir),
                "MC_SERVER_JAR": "fabric-1.20.1.jar",
                "MC_LOG_PATH": str(server_dir / "logs/latest.log"),
                "QWEN_BASE_URL": "",
                "DEEPSEEK_API_KEY": "",
                "ADDON_KNOWLEDGE_ONLINE_ENABLED": "false",
            },
            project_root=tmp_path,
        )
        service = AddonDiagnosticService(
            settings=settings,
            repository=AddonDiagnosticRepository(connection),
        )

        report = service.scan_addons()
        diagnostics = report["diagnostics"]
        proposal = service.create_addon_remediation_plan([diagnostics[0]["id"]])

        assert report["status"] == "completed"
        assert report["summary"]["blockers"] >= 1
        assert connection.execute("SELECT COUNT(*) FROM addon_assets").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM addon_diagnostics").fetchone()[0] >= 1
        assert proposal["status"] == "proposal_created"
        assert proposal["confirmation_required"] is True
        assert connection.execute("SELECT COUNT(*) FROM addon_remediation_proposals").fetchone()[0] == 1
    finally:
        connection.close()


def _jar(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
