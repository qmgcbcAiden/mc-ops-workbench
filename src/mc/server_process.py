from __future__ import annotations

import locale
import os
import shlex
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from src.config.settings import Settings


ServerState = Literal["stopped", "starting", "running", "stopping", "crashed"]


@dataclass(frozen=True)
class ServerStatus:
    state: ServerState
    pid: int | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "status": self.state,
            "pid": self.pid,
            "message": self.message,
            "label": _label_for_state(self.state),
        }


@dataclass(frozen=True)
class CommandResult:
    status: Literal["executed", "failed"]
    output: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "output": self.output,
            "error_message": self.error_message,
        }


PopenFactory = Callable[..., subprocess.Popen]
EULA_FILE_NAME = "eula.txt"
EULA_ACCEPTED_TEXT = "eula=true\n"
MAX_PENDING_STDOUT_EVENTS = 1000
MANAGED_START_SCRIPT_MARKER = "# Managed by MC ops dashboard"


def build_launch_args(settings: Settings) -> list[str]:
    args = [
        settings.mc_java_path,
        f"-Xms{settings.mc_java_xms}",
        f"-Xmx{settings.mc_java_xmx}",
        "-jar",
        str(settings.mc_server_jar),
    ]
    extra_args = settings.mc_extra_args.strip()
    if extra_args:
        args.extend(shlex.split(extra_args))
    return args


def build_start_script_args(settings: Settings, start_script: Path | None = None) -> list[str]:
    script_path = start_script or settings.mc_server_dir / _start_script_name()
    if _is_windows():
        return ["cmd.exe", "/d", "/c", "call", str(script_path)]
    return ["bash", str(script_path)]


def build_start_bat_args(settings: Settings, start_bat: Path | None = None) -> list[str]:
    return build_start_script_args(settings, start_bat)


def _ensure_start_script(server_dir: Path, settings: Settings) -> Path:
    script_path = server_dir / _start_script_name()
    if script_path.exists() and not is_managed_start_script(script_path):
        return script_path
    content = _render_start_script(settings)
    if script_path.exists():
        try:
            if script_path.read_text(encoding="utf-8") == content:
                return script_path
        except (OSError, UnicodeError):
            return script_path
    script_path.write_text(content, encoding="utf-8")
    if not _is_windows():
        _make_executable(script_path)
    return script_path


def _render_start_script(settings: Settings) -> str:
    args = build_launch_args(settings)
    if _is_windows():
        command = subprocess.list2cmdline(args)
        return f"@echo off\nREM {MANAGED_START_SCRIPT_MARKER}\n{command}\n"
    command = shlex.join(args)
    return (
        f"#!/usr/bin/env bash\n{MANAGED_START_SCRIPT_MARKER}\nset -e\n{command}\n"
    )


def is_managed_start_script(script_path: Path) -> bool:
    if not script_path.exists():
        return True
    try:
        content = script_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if MANAGED_START_SCRIPT_MARKER in content:
        return True
    if _is_windows():
        return content.startswith("@echo off\n") and " -jar " in content.lower()
    return (
        content.startswith("#!/usr/bin/env bash\nset -e\n")
        and " -jar " in content
    )


def _ensure_start_bat(server_dir: Path, settings: Settings) -> Path:
    return _ensure_start_script(server_dir, settings)


def _ensure_server_layout(server_dir: Path) -> None:
    server_dir.mkdir(parents=True, exist_ok=True)
    eula_path = server_dir / EULA_FILE_NAME
    if not eula_path.exists():
        eula_path.write_text(EULA_ACCEPTED_TEXT, encoding="utf-8")


class MinecraftServerProcess:
    def __init__(
        self,
        settings: Settings,
        popen_factory: PopenFactory = subprocess.Popen,
        process_tree_killer: Callable[[subprocess.Popen], None] | None = None,
    ) -> None:
        self._settings = settings
        self._popen_factory = popen_factory
        self._process_tree_killer = process_tree_killer or _kill_process_tree
        self._process: subprocess.Popen | None = None
        self._state: ServerState = "stopped"
        self._status_message = "Server is not running."
        self._stdout_ring: deque[str] = deque(maxlen=1000)
        self._stdout_events: deque[dict] = deque(maxlen=MAX_PENDING_STDOUT_EVENTS)
        self._stdout_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._events_lock = threading.Lock()
        self._lock = threading.RLock()

    def get_status(self) -> ServerStatus:
        with self._lock:
            self._refresh_process_state_locked()
            return self._status_locked()

    def start(self) -> ServerStatus:
        with self._lock:
            self._refresh_process_state_locked()
            if self._state in ("starting", "running", "stopping"):
                return self._status_locked("Server process is already active.")

            setup_error = self._prepare_server_layout()
            if setup_error is not None:
                self._state = "stopped"
                self._status_message = setup_error
                return self._status_locked()

            validation_error = self._validate_start()
            if validation_error is not None:
                self._state = "stopped"
                self._status_message = validation_error
                return self._status_locked()

            start_script = _ensure_start_script(self._settings.mc_server_dir, self._settings)
            args = build_start_script_args(self._settings, start_script)

        try:
            process = self._popen_factory(args, **self._popen_kwargs())
        except FileNotFoundError as exc:
            with self._lock:
                self._state = "stopped"
                self._process = None
                self._status_message = f"Unable to start server: executable not found ({exc.filename})."
                return self._status_locked()
        except OSError as exc:
            with self._lock:
                self._state = "stopped"
                self._process = None
                self._status_message = f"Unable to start server: {exc}"
                return self._status_locked()

        with self._lock:
            self._process = process
            self._state = "starting"
            self._status_message = "Server process is starting."
            self._clear_stdout_events_locked()

        self._stdout_thread = threading.Thread(target=self._read_stdout, args=(process,), daemon=True)
        self._stdout_thread.start()
        self._monitor_thread = threading.Thread(target=self._monitor_startup, args=(process,), daemon=True)
        self._monitor_thread.start()
        return ServerStatus(state="starting", pid=process.pid, message="Server process is starting.")

    def stop(self) -> ServerStatus:
        with self._lock:
            self._refresh_process_state_locked()
            if self._process is None or self._state not in ("starting", "running"):
                self._state = "stopped"
                self._status_message = "服务器未运行。"
                return self._status_locked()

            process = self._process
            self._state = "stopping"
            self._status_message = "已向服务器发送 stop 指令。"

        sent, error_message = self._write_stdin(process, "stop")
        if not sent:
            with self._lock:
                if self._process is process:
                    self._status_message = error_message or "stop 指令发送失败。"

        threading.Thread(target=self._monitor_stop, args=(process,), daemon=True).start()
        return self.get_status()

    def shutdown(self, force: bool = True) -> ServerStatus:
        with self._lock:
            self._refresh_process_state_locked()
            if self._process is None:
                self._state = "stopped"
                self._status_message = "服务器未运行。"
                return self._status_locked()

            process = self._process
            self._state = "stopping"
            self._status_message = "正在关闭服务器进程。"

        self._write_stdin(process, "stop")
        if force:
            self._process_tree_killer(process)
            stopped_cleanly = False
        else:
            stopped_cleanly = self._wait_or_terminate(process)

        with self._lock:
            if self._process is process:
                self._process = None
            self._state = "stopped"
            self._status_message = "服务器进程已在应用退出时关闭。" if force else (
                "服务器已正常关闭。" if stopped_cleanly else "服务器未在超时时间内响应 stop，进程已被终止。"
            )
            return self._status_locked()

    def _monitor_stop(self, process: subprocess.Popen) -> None:
        stopped_cleanly = self._wait_or_terminate(process)

        with self._lock:
            if self._process is not process:
                return
            self._process = None
            self._state = "stopped"
            self._status_message = (
                "服务器已正常关闭。" if stopped_cleanly else "服务器未在超时时间内响应 stop，进程已被终止。"
            )

    def send_command(self, command: str) -> CommandResult:
        normalized = (command or "").strip()
        if not normalized:
            return CommandResult(status="failed", error_message="空命令未执行。")

        with self._lock:
            self._refresh_process_state_locked()
            if self._state != "running" or self._process is None:
                return CommandResult(status="failed", error_message="服务器未运行。")
            process = self._process

        if process.stdin is None:
            return CommandResult(status="failed", error_message="服务端 stdin 不可用。")

        sent, error_message = self._write_stdin(process, normalized)
        if not sent:
            return CommandResult(status="failed", error_message=error_message)

        return CommandResult(status="executed")

    def _write_stdin(self, process: subprocess.Popen, command: str) -> tuple[bool, str | None]:
        if process.stdin is None:
            return False, "服务端 stdin 不可用。"
        try:
            process.stdin.write((command.rstrip() + "\n").encode("utf-8"))
            process.stdin.flush()
        except OSError:
            return False, "命令发送失败，请检查服务器连接状态后重试。"
        return True, None

    def drain_stdout_events(self) -> list[dict]:
        with self._events_lock:
            events = list(self._stdout_events)
            self._stdout_events.clear()
        return events

    def stdout_lines(self) -> list[str]:
        with self._events_lock:
            return list(self._stdout_ring)

    def _prepare_server_layout(self) -> str | None:
        try:
            _ensure_server_layout(self._settings.mc_server_dir)
        except OSError as exc:
            return f"无法初始化服务器目录: {exc}"
        return None

    def _validate_start(self) -> str | None:
        server_dir = self._settings.mc_server_dir
        if not server_dir.is_dir():
            return f"服务器目录不存在: {server_dir}"
        if (server_dir / _start_script_name()).is_file():
            return None
        if not self._settings.mc_server_jar.is_file():
            return (
                f"缺少 {_start_script_name()}，且无法生成：缺少服务端核心: "
                f"{self._settings.mc_server_jar}"
            )
        return None

    def _popen_kwargs(self) -> dict:
        kwargs = {
            "cwd": str(self._settings.mc_server_dir),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "bufsize": 0,
            "env": self._subprocess_env(),
        }
        creationflags = _hidden_console_creationflags()
        if creationflags:
            kwargs["creationflags"] = creationflags
        return kwargs

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        java_path = Path(self._settings.mc_java_path)
        if java_path.is_absolute() and java_path.is_file():
            java_bin = str(java_path.parent)
            existing_path = env.get("PATH", "")
            env["PATH"] = java_bin + (os.pathsep + existing_path if existing_path else "")
            env["JAVA_HOME"] = str(java_path.parent.parent)
        return env

    def _monitor_startup(self, process: subprocess.Popen) -> None:
        started_at = time.monotonic()
        timeout = max(0.1, float(self._settings.mc_start_timeout_seconds))
        promote_after = min(1.0, timeout)

        while time.monotonic() - started_at < timeout:
            if process.poll() is not None:
                with self._lock:
                    if self._process is process and self._state in ("starting", "running"):
                        self._state = "crashed"
                        self._status_message = f"Server process exited during startup (exit code {process.returncode})."
                return

            if time.monotonic() - started_at >= promote_after:
                with self._lock:
                    if self._process is process and self._state == "starting":
                        self._state = "running"
                        self._status_message = "Server process is running."
                return

            time.sleep(0.05)

        with self._lock:
            if self._process is process and self._state == "starting":
                if process.poll() is None:
                    self._state = "running"
                    self._status_message = "Server process is running."
                else:
                    self._state = "crashed"
                    self._status_message = f"Server process exited during startup (exit code {process.returncode})."

    def _read_stdout(self, process: subprocess.Popen) -> None:
        if process.stdout is None:
            return

        try:
            for line in iter(process.stdout.readline, b""):
                stripped = _decode_process_line(line).rstrip("\r\n")
                if stripped:
                    with self._events_lock:
                        self._stdout_ring.append(stripped)
                    self._append_stdout_event(stripped)
                    self._handle_process_control_line(process, stripped)
                if process.poll() is not None:
                    break
        finally:
            with self._lock:
                if self._process is process and self._state in ("starting", "running"):
                    self._state = "crashed"
                    self._status_message = f"Server process exited (exit code {process.returncode})."

    def _append_stdout_event(self, line: str) -> None:
        from src.service.log_service import parse_log_line

        event = parse_log_line(line)
        with self._events_lock:
            self._stdout_events.append(event)

    def _handle_process_control_line(self, process: subprocess.Popen, line: str) -> None:
        should_release_pause = False
        with self._lock:
            if self._process is not process:
                return
            if self._state == "starting" and _looks_like_start_failure(line):
                self._state = "crashed"
                self._status_message = line
                should_release_pause = True
            elif self._state == "stopping" and _looks_like_pause_prompt(line):
                should_release_pause = True

        if should_release_pause and process.stdin is not None:
            try:
                process.stdin.write(b"\r\n")
                process.stdin.flush()
            except OSError:
                pass

    def _wait_or_terminate(self, process: subprocess.Popen) -> bool:
        try:
            process.wait(timeout=self._settings.mc_stop_timeout_seconds)
            return True
        except subprocess.TimeoutExpired:
            self._process_tree_killer(process)

        try:
            process.wait(timeout=5)
            return False
        except subprocess.TimeoutExpired:
            self._process_tree_killer(process)
            return False

    def _refresh_process_state_locked(self) -> None:
        if self._process is None:
            if self._state in ("starting", "running", "stopping"):
                self._state = "stopped"
                self._status_message = "服务器未运行。"
            return

        return_code = self._process.poll()
        if return_code is None:
            return

        if self._state == "stopping":
            self._state = "stopped"
            self._status_message = "Server stopped."
        elif self._state in ("starting", "running"):
            self._state = "crashed"
            self._status_message = f"Server process exited (exit code {return_code})."
        elif self._state == "crashed":
            pass
        self._process = None

    def _status_locked(self, message: str | None = None) -> ServerStatus:
        process = self._process
        return ServerStatus(
            state=self._state,
            pid=process.pid if process is not None else None,
            message=message or self._status_message,
        )

    def _clear_stdout_events_locked(self) -> None:
        with self._events_lock:
            self._stdout_ring.clear()
            self._stdout_events.clear()


def _label_for_state(state: ServerState) -> str:
    return {
        "stopped": "未运行",
        "starting": "启动中",
        "running": "运行中",
        "stopping": "停止中",
        "crashed": "异常",
    }[state]


def _is_windows() -> bool:
    return os.name == "nt"


def _start_script_name() -> str:
    return "start.bat" if _is_windows() else "start.sh"


def start_script_name() -> str:
    return _start_script_name()


def _make_executable(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | 0o755)
    except OSError:
        pass


def _hidden_console_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _kill_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    if _is_windows():
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            _kill_single_process(process)
    else:
        _kill_single_process(process)

    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        _kill_single_process(process)


def _kill_single_process(process: subprocess.Popen) -> None:
    try:
        process.kill()
    except OSError:
        return
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return


def _decode_process_line(raw_line: bytes) -> str:
    encodings = ["utf-8-sig", locale.getpreferredencoding(False), "mbcs", "gbk", "cp936"]
    tried: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower()
        if normalized in tried:
            continue
        tried.add(normalized)
        try:
            return raw_line.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw_line.decode("utf-8", errors="replace")


def _looks_like_start_failure(line: str) -> bool:
    lowered = line.lower()
    return _looks_like_pause_prompt(line) or any(
        token in lowered
        for token in (
            "不是内部或外部命令",
            "a jni error has occurred",
            "unsupportedclassversionerror",
            "could not create the java virtual machine",
        )
    )


def _looks_like_pause_prompt(line: str) -> bool:
    lowered = line.lower()
    return "press any key to continue" in lowered or "请按任意键继续" in lowered
