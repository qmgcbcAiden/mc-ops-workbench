from __future__ import annotations

from pathlib import Path

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.config_change_repository import ConfigChangeRepository
from src.repositories.config_version_repository import ConfigVersionRepository
from src.service.config_edit_service import ConfigEditService
from src.service.config_version_service import ConfigVersionService
from src.service.file_service import FileService


class FakeSettings:
    def __init__(self, server_dir: Path) -> None:
        self.mc_server_dir = server_dir
        self.mc_file_tree_max_depth = 32
        self.mc_file_preview_max_bytes = 1048576
        self.mc_editable_file_max_bytes = 1048576
        self.mc_config_backup_on_save = True


def _service(tmp_path: Path) -> tuple[ConfigEditService, Path, ConfigChangeRepository]:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "server.properties").write_text(
        "# server\nmax-players=10\npvp=true\nonline-mode=true\nrcon.password=secret\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)
    repo = ConfigChangeRepository(conn)
    return ConfigEditService(FileService(FakeSettings(server_dir)), repo), server_dir, repo


def test_config_edit_service_proposes_without_writing_then_applies(tmp_path: Path) -> None:
    service, server_dir, repo = _service(tmp_path)

    proposal = service.propose_change(
        "server.properties",
        [{"key": "max-players", "value": "20", "reason": "load test"}],
        "把最大人数改成 20",
        session_id="s1",
    )

    assert proposal["status"] == "proposal_created"
    assert "max-players=20" in proposal["diff"]
    assert "max-players=20" in proposal["preview_content"]
    assert "max-players=10" in (server_dir / "server.properties").read_text(encoding="utf-8")

    result = service.apply_proposal(proposal["proposal_id"])

    assert result["status"] == "saved"
    assert result["backup_path"]
    assert "max-players=20" in (server_dir / "server.properties").read_text(encoding="utf-8")
    stored = repo.get(proposal["proposal_id"])
    assert stored is not None
    assert stored["status"] == "applied"


def test_config_edit_service_does_not_persist_or_overwrite_secrets(tmp_path: Path) -> None:
    service, server_dir, repo = _service(tmp_path)

    proposal = service.propose_change(
        "server.properties",
        [{"key": "max-players", "value": "20", "reason": "load test"}],
        "把最大人数改成 20",
        session_id="s1",
    )

    stored = repo.get(proposal["proposal_id"])
    assert stored is not None
    persisted_text = "\n".join([
        stored["before_content"],
        stored["after_content"],
        stored["diff_text"],
    ])
    assert "secret" not in persisted_text
    assert "rcon.password=<redacted>" in persisted_text

    result = service.apply_proposal(proposal["proposal_id"])

    content = (server_dir / "server.properties").read_text(encoding="utf-8")
    assert result["status"] == "saved"
    assert "max-players=20" in content
    assert "rcon.password=secret" in content


def test_versioned_config_edits_use_git_history_without_backup_files(tmp_path: Path) -> None:
    service, server_dir, repo = _service(tmp_path)
    version_service = ConfigVersionService(
        repo_dir=tmp_path / "config_versions" / "repo",
        version_repo=ConfigVersionRepository(repo.connection),
    )
    service = ConfigEditService(
        FileService(FakeSettings(server_dir)),
        repo,
        version_service=version_service,
        source_dir=server_dir,
    )
    proposal = service.propose_change(
        "server.properties",
        [{"key": "max-players", "value": "20"}],
        "把最大人数改成 20",
    )

    result = service.apply_proposal(proposal["proposal_id"])

    assert result["status"] == "saved"
    assert result["backup_path"] is None
    assert list(server_dir.glob("server.properties.*.bak")) == []
    history = version_service.list_history("server.properties")
    assert len(history) == 2
    assert {record["message"].split(" ", 1)[0] for record in history} == {
        "baseline",
        "config(server.properties):",
    }


def test_config_edit_service_rolls_back_without_overwriting_secrets(tmp_path: Path) -> None:
    service, server_dir, _repo = _service(tmp_path)
    proposal = service.propose_change(
        "server.properties",
        [{"key": "max-players", "value": "20"}],
        "把最大人数改成 20",
    )
    applied = service.apply_proposal(proposal["proposal_id"])

    rolled_back = service.rollback_change(applied["proposal_id"])

    content = (server_dir / "server.properties").read_text(encoding="utf-8")
    assert rolled_back["status"] == "rolled_back"
    assert "max-players=10" in content
    assert "rcon.password=secret" in content


def test_config_edit_service_reads_safe_config_snapshot_without_sensitive_unknown_keys(tmp_path: Path) -> None:
    service, _server_dir, _repo = _service(tmp_path)

    snapshot = service.read_config_file("server.properties")

    assert snapshot["status"] == "ok"
    assert "max-players=10" in snapshot["visible_content"]
    assert "online-mode=true" in snapshot["visible_content"]
    assert "rcon.password" not in snapshot["visible_content"]
    assert "secret" not in snapshot["visible_content"]
    assert snapshot["hidden_entry_count"] == 1


def test_config_edit_service_rejects_unknown_key(tmp_path: Path) -> None:
    service, _server_dir, _repo = _service(tmp_path)

    proposal = service.propose_change(
        "server.properties",
        [{"key": "rcon.password", "value": "secret"}],
        "改 rcon 密码",
    )

    assert proposal["status"] == "rejected"
    assert "不在白名单" in proposal["message"]


def test_config_edit_service_detects_hash_conflict(tmp_path: Path) -> None:
    service, server_dir, _repo = _service(tmp_path)
    proposal = service.propose_change(
        "server.properties",
        [{"key": "pvp", "value": "false"}],
        "关闭 PVP",
    )
    (server_dir / "server.properties").write_text("pvp=false\n", encoding="utf-8")

    result = service.apply_proposal(proposal["proposal_id"])

    assert result["status"] == "conflict"
    assert "重新生成" in result["message"]


def test_config_edit_service_requires_high_risk_confirmation(tmp_path: Path) -> None:
    service, _server_dir, _repo = _service(tmp_path)
    proposal = service.propose_change(
        "server.properties",
        [{"key": "online-mode", "value": "false"}],
        "关闭正版验证",
    )

    result = service.apply_proposal(proposal["proposal_id"])

    assert proposal["risk_level"] == "HIGH"
    assert result["status"] == "confirmation_required"

    confirmed = service.apply_proposal(
        proposal["proposal_id"],
        high_risk_confirmed=True,
    )
    assert confirmed["status"] == "saved"


def test_config_edit_service_rejects_path_traversal(tmp_path: Path) -> None:
    service, _server_dir, _repo = _service(tmp_path)

    proposal = service.propose_change(
        "../.env",
        [{"key": "max-players", "value": "20"}],
        "改环境文件",
    )

    assert proposal["status"] == "rejected"
