from __future__ import annotations

from pathlib import Path

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.interface.config_edit_interface import ConfigEditInterface
from src.repositories.config_change_repository import ConfigChangeRepository
from src.service.config_edit_service import ConfigEditService
from src.service.file_service import FileService
from tests.fixtures.fake_mc_server import create_fake_mc_server


class FakeSettings:
    def __init__(self, server_dir: Path) -> None:
        self.mc_server_dir = server_dir
        self.mc_file_tree_max_depth = 32
        self.mc_file_preview_max_bytes = 1048576
        self.mc_editable_file_max_bytes = 1048576
        self.mc_config_backup_on_save = True


def test_config_edit_interface_generates_and_applies_proposal(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    create_fake_mc_server(server_dir)
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    interface = ConfigEditInterface(
        ConfigEditService(
            FileService(FakeSettings(server_dir)),
            ConfigChangeRepository(conn),
        )
    )

    proposal = interface.propose_change(
        "server.properties",
        [{"key": "max-players", "value": "20"}],
        "把最大人数改成 20",
    )
    applied = interface.apply_proposal(proposal["proposal_id"])

    assert proposal["status"] == "proposal_created"
    assert applied["status"] == "saved"
    assert "max-players=20" in (server_dir / "server.properties").read_text(encoding="utf-8")
