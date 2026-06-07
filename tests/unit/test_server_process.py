from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.mc import server_process
from src.mc.server_process import (
    MAX_PENDING_STDOUT_EVENTS,
    MinecraftServerProcess,
    ServerStatus,
    _ensure_server_layout,
    _ensure_start_script,
    _decode_process_line,
    _looks_like_start_failure,
    _looks_like_pause_prompt,
    build_launch_args,
    build_start_script_args,
    discover_start_scripts,
)


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass


class _BlockingProcess:
    pid = 1234
    returncode = None

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.wait_started = threading.Event()
        self.release_wait = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_started.set()
        self.release_wait.wait(1)
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class _TimeoutProcess(_BlockingProcess):
    def wait(self, timeout: float | None = None) -> int:
        import subprocess

        self.wait_started.set()
        raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)


class _StartedProcess:
    pid = 4321
    returncode = None

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stdout = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


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


class TestMinecraftServerProcess:
    def test_initial_status_is_stopped(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        proc = MinecraftServerProcess(settings)
        status = proc.get_status()
        assert status.state == "stopped"

    def test_start_fails_when_jar_missing(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        proc = MinecraftServerProcess(settings)
        result = proc.start()
        assert "缺少服务端核心" in result.message

    def test_start_creates_server_directory_and_default_eula_before_validation(
        self,
        tmp_path: Path,
    ) -> None:
        settings = _make_settings(tmp_path)
        settings.mc_server_dir.rmdir()

        proc = MinecraftServerProcess(settings)
        result = proc.start()

        assert settings.mc_server_dir.is_dir()
        assert (settings.mc_server_dir / "eula.txt").read_text(encoding="utf-8") == "eula=true\n"
        assert "缺少服务端核心" in result.message

    def test_send_command_when_not_running_fails(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        proc = MinecraftServerProcess(settings)
        result = proc.send_command("list")
        assert result.status == "failed"
        assert "未运行" in (result.error_message or "")

    def test_pending_stdout_events_are_bounded_to_recent_output(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        proc = MinecraftServerProcess(settings)

        for index in range(MAX_PENDING_STDOUT_EVENTS + 5):
            proc._append_stdout_event(f"[12:00:00 INFO]: Line {index}")

        events = proc.drain_stdout_events()

        assert len(events) == MAX_PENDING_STDOUT_EVENTS
        assert events[0]["message"] == "Line 5"
        assert events[-1]["message"] == f"Line {MAX_PENDING_STDOUT_EVENTS + 4}"

    def test_stop_when_not_running_returns_stopped(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        proc = MinecraftServerProcess(settings)
        result = proc.stop()
        assert result.state == "stopped"

    def test_stop_sends_stop_command_without_blocking(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        proc = MinecraftServerProcess(settings)
        fake_process = _BlockingProcess()
        proc._process = fake_process
        proc._state = "running"

        result = proc.stop()

        assert result.state in {"stopping", "stopped"}
        assert fake_process.stdin.writes == [b"stop\n"]
        assert fake_process.wait_started.wait(0.2)

        fake_process.release_wait.set()

    def test_pause_prompt_is_released_while_stopping(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        proc = MinecraftServerProcess(settings)
        fake_process = _BlockingProcess()
        proc._process = fake_process
        proc._state = "stopping"

        proc._handle_process_control_line(fake_process, "请按任意键继续. . .")

        assert fake_process.stdin.writes == [b"\r\n"]

    def test_shutdown_sends_stop_and_kills_process_tree(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        killed: list[int] = []
        proc = MinecraftServerProcess(settings, process_tree_killer=lambda process: killed.append(process.pid))
        fake_process = _BlockingProcess()
        proc._process = fake_process
        proc._state = "running"

        result = proc.shutdown(force=True)

        assert result.state == "stopped"
        assert fake_process.stdin.writes == [b"stop\n"]
        assert killed == [fake_process.pid]
        assert proc.get_status().state == "stopped"

    def test_stop_timeout_kills_process_tree(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        killed: list[int] = []
        proc = MinecraftServerProcess(settings, process_tree_killer=lambda process: killed.append(process.pid))
        fake_process = _TimeoutProcess()
        proc._process = fake_process
        proc._state = "running"

        result = proc.stop()

        assert result.state in {"stopping", "stopped"}
        assert fake_process.stdin.writes == [b"stop\n"]
        assert fake_process.wait_started.wait(0.2)
        for _ in range(20):
            if killed:
                break
            time.sleep(0.01)
        assert killed

    def test_ensure_server_layout_creates_nested_directory_and_default_eula(
        self,
        tmp_path: Path,
    ) -> None:
        server_dir = tmp_path / "nested" / "mc_server"

        _ensure_server_layout(server_dir)

        assert server_dir.is_dir()
        assert (server_dir / "eula.txt").read_text(encoding="utf-8") == "eula=true\n"

    def test_ensure_server_layout_preserves_existing_eula(
        self,
        tmp_path: Path,
    ) -> None:
        server_dir = tmp_path / "mc_server"
        server_dir.mkdir()
        eula_path = server_dir / "eula.txt"
        eula_path.write_text("eula=false\n", encoding="utf-8")

        _ensure_server_layout(server_dir)

        assert eula_path.read_text(encoding="utf-8") == "eula=false\n"

    @pytest.mark.parametrize(
        ("is_windows", "script_name", "expected_header"),
        [
            (True, "start.bat", "@echo off"),
            (False, "start.sh", "#!/usr/bin/env bash"),
        ],
    )
    def test_ensure_start_script_creates_platform_template(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        is_windows: bool,
        script_name: str,
        expected_header: str,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: is_windows)
        settings = _make_settings(tmp_path)
        script_path = _ensure_start_script(settings.mc_server_dir, settings)
        assert script_path == settings.mc_server_dir / script_name
        assert script_path.exists()
        content = script_path.read_text(encoding="utf-8", errors="replace")
        assert expected_header in content
        assert "java" in content
        assert "server.jar" in content
        assert "-Xms1G" in content
        assert "-Xmx2G" in content

    def test_ensure_start_script_preserves_existing_platform_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: False)
        settings = _make_settings(tmp_path)
        script_path = settings.mc_server_dir / "start.sh"
        script_path.write_text("#!/usr/bin/env bash\necho custom\n", encoding="utf-8")

        result = _ensure_start_script(settings.mc_server_dir, settings)

        assert result == settings.mc_server_dir / "dashboard_start.sh"
        assert script_path.read_text(encoding="utf-8") == "#!/usr/bin/env bash\necho custom\n"
        assert result.exists()

    def test_launch_args_always_include_exactly_one_nogui(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)

        empty_args = build_launch_args(replace(settings, mc_extra_args=""))
        duplicate_args = build_launch_args(
            replace(settings, mc_extra_args="--demo nogui NOGUI")
        )

        assert empty_args[-1] == "nogui"
        assert empty_args.count("nogui") == 1
        assert duplicate_args[-1] == "nogui"
        assert [arg.lower() for arg in duplicate_args].count("nogui") == 1

    def test_discovers_common_posix_and_neoforge_start_scripts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: False)
        settings = _make_settings(tmp_path)
        (settings.mc_server_dir / "run.sh").write_text(
            "#!/usr/bin/env sh\njava -jar server.jar \"$@\"\n",
            encoding="utf-8",
        )
        (settings.mc_server_dir / "launch.sh").write_text(
            "#!/usr/bin/env sh\n"
            "java @user_jvm_args.txt @libraries/net/neoforged/neoforge/unix_args.txt \"$@\"\n",
            encoding="utf-8",
        )
        (settings.mc_server_dir / "server.bat").write_text(
            "java -jar server.jar %*\r\n",
            encoding="utf-8",
        )

        scripts = discover_start_scripts(settings.mc_server_dir)

        assert [script.name for script in scripts] == ["run.sh", "launch.sh"]

    def test_discovers_windows_batch_before_cmd_scripts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: True)
        settings = _make_settings(tmp_path)
        (settings.mc_server_dir / "start_server.cmd").write_text(
            "java -jar server.jar %*\r\n",
            encoding="utf-8",
        )
        (settings.mc_server_dir / "server.bat").write_text(
            "java -jar server.jar %*\r\n",
            encoding="utf-8",
        )

        scripts = discover_start_scripts(settings.mc_server_dir)

        assert [script.name for script in scripts] == ["server.bat", "start_server.cmd"]

    def test_custom_script_that_forwards_args_receives_runtime_nogui(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: False)
        settings = _make_settings(tmp_path)
        script_path = settings.mc_server_dir / "run.sh"
        original = "#!/usr/bin/env sh\njava -jar server.jar \"$@\"\n"
        script_path.write_text(original, encoding="utf-8")

        selected = _ensure_start_script(settings.mc_server_dir, settings)
        args = build_start_script_args(settings, selected)

        assert selected == script_path
        assert args == ["bash", str(script_path), "nogui"]
        assert script_path.read_text(encoding="utf-8") == original
        assert not (settings.mc_server_dir / ".dashboard-backups").exists()

    def test_custom_shell_script_without_arg_forwarding_is_backed_up_and_rewritten(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: False)
        settings = _make_settings(tmp_path)
        script_path = settings.mc_server_dir / "run.sh"
        original = "#!/usr/bin/env sh\n# add nogui here if needed\njava -jar server.jar\n"
        script_path.write_text(original, encoding="utf-8")

        selected = _ensure_start_script(settings.mc_server_dir, settings)

        assert selected == script_path
        assert "java -jar server.jar nogui\n" in script_path.read_text(encoding="utf-8")
        backups = list((settings.mc_server_dir / ".dashboard-backups").glob("run.sh.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == original.encode("utf-8")

    def test_custom_batch_script_without_arg_forwarding_is_backed_up_and_rewritten(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: True)
        settings = _make_settings(tmp_path)
        script_path = settings.mc_server_dir / "server.bat"
        original = b"@echo off\r\njava -jar server.jar\r\npause\r\n"
        script_path.write_bytes(original)

        selected = _ensure_start_script(settings.mc_server_dir, settings)

        assert selected == script_path
        assert b"java -jar server.jar nogui\r\n" in script_path.read_bytes()
        backups = list((settings.mc_server_dir / ".dashboard-backups").glob("server.bat.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == original

    def test_start_refuses_complex_script_when_nogui_cannot_be_guaranteed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: False)
        settings = _make_settings(tmp_path)
        settings.mc_server_jar.write_bytes(b"fake jar")
        (settings.mc_server_dir / "start.sh").write_text(
            "#!/usr/bin/env sh\njava \\\n  -jar server.jar\n",
            encoding="utf-8",
        )
        launched: list[list[str]] = []

        def popen_factory(args, **_kwargs):
            launched.append(args)
            return _StartedProcess()

        proc = MinecraftServerProcess(settings, popen_factory=popen_factory)
        result = proc.start()

        assert result.state == "stopped"
        assert "nogui" in result.message
        assert launched == []

    @pytest.mark.parametrize(
        ("is_windows", "script_name", "expected_args_prefix"),
        [
            (True, "start.bat", ["cmd.exe", "/d", "/c", "call"]),
            (False, "start.sh", ["bash"]),
        ],
    )
    def test_start_auto_creates_missing_platform_script(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        is_windows: bool,
        script_name: str,
        expected_args_prefix: list[str],
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: is_windows)
        settings = _make_settings(tmp_path)
        settings.mc_server_jar.write_bytes(b"fake jar")
        launched: list[list[str]] = []

        def popen_factory(args, **_kwargs):
            launched.append(args)
            return _StartedProcess()

        proc = MinecraftServerProcess(settings, popen_factory=popen_factory)
        result = proc.start()

        script_path = settings.mc_server_dir / script_name
        assert result.state == "starting"
        assert script_path.is_file()
        assert launched
        assert launched[0][: len(expected_args_prefix)] == expected_args_prefix
        assert launched[0][-1] == str(script_path)

    def test_start_script_args_run_batch_file_on_windows(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: True)
        settings = _make_settings(tmp_path)
        args = build_start_script_args(settings)
        assert args[:4] == ["cmd.exe", "/d", "/c", "call"]
        assert args[-1] == str(settings.mc_server_dir / "start.bat")

    def test_start_script_args_run_shell_file_on_posix(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_process, "_is_windows", lambda: False)
        settings = _make_settings(tmp_path)
        args = build_start_script_args(settings)
        assert args == ["bash", str(settings.mc_server_dir / "start.sh")]

    def test_decodes_gbk_process_output(self, tmp_path: Path) -> None:
        assert "不是内部或外部命令" in _decode_process_line(
            "不是内部或外部命令".encode("gbk")
        )

    def test_detects_start_failure_lines(self, tmp_path: Path) -> None:
        assert _looks_like_start_failure("Error: A JNI error has occurred")
        assert _looks_like_start_failure("请按任意键继续. . .")
        assert not _looks_like_start_failure("[Server thread/INFO]: Done")

    def test_detects_pause_prompt(self, tmp_path: Path) -> None:
        assert _looks_like_pause_prompt("请按任意键继续. . .")
        assert _looks_like_pause_prompt("Press any key to continue . . .")
        assert not _looks_like_pause_prompt("[Server thread/INFO]: Done")

    def test_launch_args_are_built_from_settings(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        args = build_launch_args(settings)
        assert args[:4] == ["java", "-Xms1G", "-Xmx2G", "-jar"]
        assert args[4] == str(settings.mc_server_jar)
        assert args[-1] == "nogui"

    def test_status_to_dict(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        proc = MinecraftServerProcess(settings)
        status = proc.get_status().to_dict()
        assert "state" in status
        assert status["state"] == "stopped"
        assert "label" in status
