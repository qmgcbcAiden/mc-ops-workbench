from __future__ import annotations

from pathlib import Path

from src.config.settings import load_settings
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.mc.addon_scanner import AddonAsset
from src.repositories.addon_diagnostic_repository import AddonDiagnosticRepository
from src.service.addon_diagnostic_service import AddonKnowledgeService


def test_knowledge_service_skips_network_when_providers_disabled(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    run_migrations(db_path)
    connection = get_connection(db_path)
    try:
        settings = load_settings(
            environ={
                "APP_DB_PATH": str(db_path),
                "MC_SERVER_DIR": str(server_dir),
                "MC_SERVER_JAR": "server.jar",
                "MC_LOG_PATH": str(server_dir / "logs/latest.log"),
                "MODRINTH_ENABLED": "false",
                "CURSEFORGE_API_KEY": "",
            },
            project_root=tmp_path,
        )
        called = []

        def opener(*_args, **_kwargs):
            called.append(True)
            raise AssertionError("network should not be called")

        service = AddonKnowledgeService(
            settings,
            AddonDiagnosticRepository(connection),
            opener=opener,
        )
        assets = [
            AddonAsset(
                relative_path="mods/example.jar",
                file_name="example.jar",
                folder="mods",
                kind="mod",
                addon_id="example",
                name="example",
                version="1.0.0",
                loader="fabric",
                environment=None,
                sha1="a" * 40,
                sha512="b" * 128,
            )
        ]

        diagnostics = service.enrich_assets(assets)

        assert diagnostics == []
        assert called == []
    finally:
        connection.close()
