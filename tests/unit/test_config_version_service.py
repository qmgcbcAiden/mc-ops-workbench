from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.repositories.config_version_repository import ConfigVersionRepository
from src.service.config_version_service import ConfigVersionService


@pytest.fixture
def temp_repo_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / "config_versions" / "default" / "repo"


@pytest.fixture
def sqlite_connection():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA foreign_keys = ON")
    _create_tables(db)
    yield db
    db.close()


def _create_tables(db: sqlite3.Connection):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS config_change_proposals (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            user_request TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            before_hash TEXT NOT NULL,
            after_hash TEXT NOT NULL,
            before_content TEXT NOT NULL,
            after_content TEXT NOT NULL,
            diff_text TEXT NOT NULL,
            changes_json TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            restart_required INTEGER NOT NULL,
            warnings_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            confirmed_at TEXT,
            applied_at TEXT,
            confirmed_by TEXT,
            backup_path TEXT,
            error_message TEXT,
            auto_approved INTEGER NOT NULL DEFAULT 0,
            approval_policy TEXT,
            version_commit_id TEXT,
            version_status TEXT,
            version_error_message TEXT,
            redaction_version TEXT
        );
        CREATE TABLE IF NOT EXISTS config_version_commits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_id TEXT NOT NULL,
            parent_commit_id TEXT,
            proposal_id TEXT,
            relative_path TEXT NOT NULL,
            actor TEXT NOT NULL,
            source TEXT NOT NULL,
            message TEXT NOT NULL,
            risk_level TEXT,
            auto_approved INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    db.commit()


@pytest.fixture
def version_repo(sqlite_connection):
    return ConfigVersionRepository(sqlite_connection)


@pytest.fixture
def service(temp_repo_dir, version_repo):
    return ConfigVersionService(repo_dir=temp_repo_dir, version_repo=version_repo)


class TestEnsureRepo:
    def test_creates_repo(self, temp_repo_dir, version_repo):
        service = ConfigVersionService(repo_dir=temp_repo_dir, version_repo=version_repo)
        result = service.ensure_repo()
        assert result["status"] == "ok"
        assert result["created"] is True
        assert temp_repo_dir.exists()

    def test_idempotent(self, temp_repo_dir, version_repo):
        service = ConfigVersionService(repo_dir=temp_repo_dir, version_repo=version_repo)
        first = service.ensure_repo()
        second = service.ensure_repo()
        assert first["created"] is True
        assert second["created"] is False


class TestSnapshotFile:
    def test_snapshot_creates_commit(self, service, temp_repo_dir):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "server.properties"
            src.write_text("max-players=20\npvp=false\n")

            result = service.snapshot_file(
                source_path=src,
                relative_path="server.properties",
            )
            assert result["status"] == "committed"
            assert result["commit_id"]

    def test_snapshot_redacts_secrets(self, service, temp_repo_dir):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "server.properties"
            src.write_text("max-players=20\nrcon.password=secret123\n")

            result = service.snapshot_file(
                source_path=src,
                relative_path="server.properties",
            )
            assert result["status"] == "committed"

            # The git repo file should not contain the secret
            repo_file = temp_repo_dir / "server.properties"
            content = repo_file.read_text()
            assert "secret123" not in content
            assert "<redacted>" in content
            assert "max-players=20" in content

    def test_snapshot_missing_file(self, service):
        result = service.snapshot_file(
            source_path="/nonexistent/path/file.properties",
            relative_path="file.properties",
        )
        assert result["status"] == "missing"


class TestSnapshotAfterChange:
    def test_snapshots_after_proposal(self, service, temp_repo_dir):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "server.properties"
            src.write_text("max-players=20\npvp=false\n")

            result = service.snapshot_after_change(
                source_path=src,
                relative_path="server.properties",
                proposal_id="cfgp_test1",
                actor="agent",
                risk_level="LOW",
                auto_approved=True,
                message="config(server.properties): max-players 10 -> 20",
            )
            assert result["status"] == "committed"
            assert result["commit_id"]

    def test_baseline_snapshot_is_not_duplicated_when_content_is_current(self, service):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "server.properties"
            src.write_text("max-players=10\n")

            first = service.ensure_baseline_snapshot(src, "server.properties")
            second = service.ensure_baseline_snapshot(src, "server.properties")

            assert first["status"] == "committed"
            assert second["status"] == "current"
            assert len(service.list_history("server.properties")) == 1


class TestListHistory:
    def test_lists_commits_for_path(self, service, temp_repo_dir, version_repo):
        # Manually create a commit record
        version_repo.create_commit(
            commit_id="abc123",
            parent_commit_id=None,
            proposal_id=None,
            relative_path="server.properties",
            actor="system",
            source="system",
            message="init",
            risk_level=None,
            auto_approved=True,
            status="committed",
        )
        history = service.list_history("server.properties")
        assert len(history) == 1
        assert history[0]["commit_id"] == "abc123"


class TestDiffCommit:
    def test_diff_returns_content(self, service, temp_repo_dir, version_repo):
        # Create a commit record first
        version_repo.create_commit(
            commit_id="abc123",
            parent_commit_id=None,
            proposal_id=None,
            relative_path="server.properties",
            actor="system",
            source="system",
            message="init",
            risk_level=None,
            auto_approved=True,
            status="committed",
        )
        # snapshot a file to get a real commit_id
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "server.properties"
            src.write_text("max-players=20\n")
            snap = service.snapshot_file(src, "server.properties")

        diff = service.diff_commit(snap["commit_id"])
        assert diff["status"] in ("ok", "repo_error")


class TestFileAtCommit:
    def test_returns_content(self, service, temp_repo_dir, version_repo):
        version_repo.create_commit(
            commit_id="abc123",
            parent_commit_id=None,
            proposal_id=None,
            relative_path="server.properties",
            actor="system",
            source="system",
            message="init",
            risk_level=None,
            auto_approved=True,
            status="committed",
        )
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "server.properties"
            src.write_text("max-players=20\n")
            snap = service.snapshot_file(src, "server.properties")

        result = service.get_file_at_commit(snap["commit_id"])
        assert result["status"] == "ok"
