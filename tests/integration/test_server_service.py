from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tests.fixtures.fake_mc_server import create_fake_mc_server
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.runtime_repository import ServerRuntimeRepository
from src.service.server_service import ServerService


def _make_settings(tmp_path: Path, server_dir: Path):
    from src.config.settings import Settings
    return Settings(
        app_env="test",
        db_path=tmp_path / "data/app.db",
        flet_run_view="desktop",
        flet_server_host="127.0.0.1",
        flet_server_port=8550,
        qwen_api_key="",
        qwen_base_url="",
        qwen_model="qwen-plus",
        qwen_timeout_seconds=30,
        qwen_max_tokens=800,
        qwen_temperature=0.2,
        mc_server_dir=server_dir,
        mc_server_jar=server_dir / "server.jar",
        mc_java_path="java",
        mc_java_xms="1G",
        mc_java_xmx="2G",
        mc_extra_args="nogui",
        mc_log_path=server_dir / "logs/latest.log",
        mc_command_mode="stdin",
        mc_rcon_host="127.0.0.1",
        mc_rcon_port=25575,
        mc_rcon_password="configured-secret",
        mc_start_timeout_seconds=1,
        mc_stop_timeout_seconds=1,
        qwen_log_model="",
        ai_context_max_chars=24000,
        ai_recent_messages_limit=16,
        ai_summary_trigger_messages=24,
        ai_summary_target_chars=3000,
        ai_log_raw_max_chars=60000,
        ai_log_compressed_max_chars=8000,
        ai_stream_enabled=True,
        mc_file_preview_max_bytes=1048576,
        mc_file_tree_max_depth=32,
        mc_editable_file_max_bytes=1048576,
        mc_config_backup_on_save=True,
    )


class TestServerService:
    def test_initial_status_is_stopped(self, tmp_path: Path) -> None:
        server_dir = tmp_path / "mc_server"
        server_dir.mkdir()
        create_fake_mc_server(server_dir)

        db_path = tmp_path / "app.db"
        run_migrations(str(db_path))
        conn = get_connection(str(db_path))
        try:
            repo = ServerRuntimeRepository(conn)
            settings = _make_settings(tmp_path, server_dir)
            service = ServerService(repo, settings)
            status = service.get_status()
            assert status["state"] == "stopped"
        finally:
            conn.close()

    def test_start_without_jar_reports_error(self, tmp_path: Path) -> None:
        server_dir = tmp_path / "mc_server"
        server_dir.mkdir()

        db_path = tmp_path / "app.db"
        run_migrations(str(db_path))
        conn = get_connection(str(db_path))
        try:
            repo = ServerRuntimeRepository(conn)
            settings = _make_settings(tmp_path, server_dir)
            service = ServerService(repo, settings)
            result = service.start_server()
            assert "缺少服务端核心" in result["message"]
            properties = (server_dir / "server.properties").read_text(encoding="utf-8")
            assert "enable-rcon=true" in properties
            assert "rcon.password=configured-secret" in properties
        finally:
            conn.close()

    def test_start_with_missing_rcon_password_fails_before_writing_server_files(
        self,
        tmp_path: Path,
    ) -> None:
        server_dir = tmp_path / "mc_server"
        server_dir.mkdir()
        db_path = tmp_path / "app.db"
        run_migrations(str(db_path))
        conn = get_connection(str(db_path))
        try:
            repo = ServerRuntimeRepository(conn)
            settings = replace(
                _make_settings(tmp_path, server_dir),
                mc_rcon_password="",
            )
            result = ServerService(repo, settings).start_server()

            assert result["status"] == "failed"
            assert "MC_RCON_PASSWORD" in result["message"]
            assert not (server_dir / "server.properties").exists()
        finally:
            conn.close()

    def test_runtime_events_recorded_on_start_attempt(self, tmp_path: Path) -> None:
        server_dir = tmp_path / "mc_server"
        server_dir.mkdir()

        db_path = tmp_path / "app.db"
        run_migrations(str(db_path))
        conn = get_connection(str(db_path))
        try:
            repo = ServerRuntimeRepository(conn)
            settings = _make_settings(tmp_path, server_dir)
            service = ServerService(repo, settings)
            service.start_server()
            events = repo.list_recent(limit=5)
            assert len(events) >= 1
            assert events[0]["event_type"] == "start"
        finally:
            conn.close()

    def test_restart_when_stopped_attempts_start_and_reports_failure(self, tmp_path: Path) -> None:
        server_dir = tmp_path / "mc_server"
        server_dir.mkdir()

        db_path = tmp_path / "app.db"
        run_migrations(str(db_path))
        conn = get_connection(str(db_path))
        try:
            repo = ServerRuntimeRepository(conn)
            settings = _make_settings(tmp_path, server_dir)
            service = ServerService(repo, settings)
            result = service.restart_server()
            assert result["operation"] == "restart_server"
            assert result["status"] == "failed"
            assert result["stop"]["state"] == "stopped"
            assert result["start"]["state"] == "stopped"
            assert "缺少服务端核心" in result["message"]
            events = repo.list_recent(limit=5)
            assert events[0]["event_type"] == "start"
        finally:
            conn.close()

    def test_send_command_when_not_running_fails(self, tmp_path: Path) -> None:
        server_dir = tmp_path / "mc_server"
        server_dir.mkdir()
        create_fake_mc_server(server_dir)

        db_path = tmp_path / "app.db"
        run_migrations(str(db_path))
        conn = get_connection(str(db_path))
        try:
            repo = ServerRuntimeRepository(conn)
            settings = _make_settings(tmp_path, server_dir)
            service = ServerService(repo, settings)
            result = service.send_command("list")
            assert result["status"] == "failed"
        finally:
            conn.close()

    def test_stop_when_not_running(self, tmp_path: Path) -> None:
        server_dir = tmp_path / "mc_server"
        server_dir.mkdir()
        create_fake_mc_server(server_dir)

        db_path = tmp_path / "app.db"
        run_migrations(str(db_path))
        conn = get_connection(str(db_path))
        try:
            repo = ServerRuntimeRepository(conn)
            settings = _make_settings(tmp_path, server_dir)
            service = ServerService(repo, settings)
            result = service.stop_server()
            assert result["state"] == "stopped"
        finally:
            conn.close()
