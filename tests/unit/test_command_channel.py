from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import Settings
from src.mc.command_channel import StdinCommandChannel
from src.mc.server_process import MinecraftServerProcess


def _make_settings(tmp_path: Path) -> Settings:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
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
        mc_rcon_password="",
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


class TestStdinCommandChannel:
    def test_send_when_not_running_fails(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        proc = MinecraftServerProcess(settings)
        channel = StdinCommandChannel(proc)
        result = channel.send("list")
        assert result.status == "failed"
        assert "未运行" in (result.error_message or "")
