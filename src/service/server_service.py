from __future__ import annotations

import threading
import time
from pathlib import Path

from src.config.settings import Settings
from src.mc.server_process import MinecraftServerProcess
from src.repositories.event_repository import EventRepository
from src.repositories.app_settings_repository import AppSettingsRepository
from src.repositories.runtime_repository import ServerRuntimeRepository
from src.service.java_environment_service import settings_with_java_overrides
from src.service.startup_configuration_service import (
    StartupConfigurationError,
    StartupConfigurationService,
)


_PROCESS_REGISTRY: dict[Path, MinecraftServerProcess] = {}
_PROCESS_SIGNATURES: dict[Path, tuple] = {}
_PROCESS_REGISTRY_LOCK = threading.Lock()


class ServerService:
    def __init__(
        self,
        runtime_repository: ServerRuntimeRepository,
        settings: Settings,
        event_repository: EventRepository | None = None,
        app_settings_repository: AppSettingsRepository | None = None,
    ) -> None:
        self._runtime_repo = runtime_repository
        self._event_repo = event_repository
        self._settings = settings
        self._app_settings = app_settings_repository

    def get_status(self) -> dict:
        return self._get_process().get_status().to_dict()

    def start_server(self) -> dict:
        try:
            StartupConfigurationService(
                self._effective_settings()
            ).ensure_rcon_configuration()
        except StartupConfigurationError as exc:
            result = {
                "state": "stopped",
                "status": "failed",
                "pid": None,
                "message": str(exc),
                "label": "未运行",
            }
            self._runtime_repo.create_event(
                event_type="start",
                status="failed",
                pid=None,
                message=str(exc),
            )
            return result
        status = self._get_process().start()
        self._runtime_repo.create_event(
            event_type="start",
            status=status.state,
            pid=status.pid,
            message=status.message,
        )
        return status.to_dict()

    def stop_server(self) -> dict:
        status = self._get_process().stop()
        self._runtime_repo.create_event(
            event_type="stop",
            status=status.state,
            pid=status.pid,
            message=status.message,
        )
        return status.to_dict()

    def restart_server(self) -> dict:
        current_status = self.get_status()
        stop_status = current_status
        stopped_status = current_status

        if current_status.get("state") in {"starting", "running", "stopping"}:
            stop_status = self.stop_server()
            stopped_status = self._wait_for_state(
                target_states={"stopped", "crashed"},
                timeout_seconds=max(0.1, float(self._settings.mc_stop_timeout_seconds) + 0.5),
            )
            if stopped_status.get("state") not in {"stopped", "crashed"}:
                message = "服务器未在超时时间内停止，已取消自动启动。"
                return {
                    "operation": "restart_server",
                    "status": "failed",
                    "stop": stop_status,
                    "stopped": stopped_status,
                    "start": None,
                    "message": message,
                    "error_message": message,
                }

        start_status = self.start_server()
        start_state = start_status.get("state")
        success = start_state in {"starting", "running"}
        return {
            "operation": "restart_server",
            "status": "executed" if success else "failed",
            "stop": stop_status,
            "stopped": stopped_status,
            "start": start_status,
            "message": (
                f"服务器已停止并提交启动请求，当前状态：{start_state}。"
                if success
                else start_status.get("message") or "服务器重启失败。"
            ),
            "error_message": None if success else start_status.get("message"),
        }

    def shutdown_server(self, force: bool = True) -> dict:
        status = self._get_process().shutdown(force=force)
        self._runtime_repo.create_event(
            event_type="shutdown",
            status=status.state,
            pid=status.pid,
            message=status.message,
        )
        return status.to_dict()

    def send_command(self, command: str) -> dict:
        result = self._get_process().send_command(command)
        return result.to_dict()

    def get_stdout_lines(self) -> list[str]:
        return self._get_process().stdout_lines()

    def drain_stdout_events(self, persist: bool = True) -> list[dict]:
        events = self._get_process().drain_stdout_events()
        if not events or self._event_repo is None or not persist:
            return events

        if not self._settings.mc_log_path.is_file():
            for event in events:
                self._event_repo.insert_event(event)

        return events

    def list_runtime_events(self, limit: int = 20) -> list[dict]:
        return self._runtime_repo.list_recent(limit=limit)

    def _wait_for_state(self, target_states: set[str], timeout_seconds: float) -> dict:
        deadline = time.monotonic() + timeout_seconds
        status = self.get_status()
        while status.get("state") not in target_states and time.monotonic() < deadline:
            time.sleep(0.05)
            status = self.get_status()
        return status

    def _get_process(self) -> MinecraftServerProcess:
        settings = self._effective_settings()
        key = settings.mc_server_dir.resolve(strict=False)
        signature = _launch_signature(settings)
        with _PROCESS_REGISTRY_LOCK:
            process = _PROCESS_REGISTRY.get(key)
            if process is not None and _PROCESS_SIGNATURES.get(key) != signature:
                status = process.get_status()
                if status.state not in {"starting", "running", "stopping"}:
                    process = None
            if process is None:
                process = MinecraftServerProcess(settings)
                _PROCESS_REGISTRY[key] = process
                _PROCESS_SIGNATURES[key] = signature
            return process

    def _effective_settings(self) -> Settings:
        return settings_with_java_overrides(self._settings, self._app_settings)


def _launch_signature(settings: Settings) -> tuple:
    return (
        settings.mc_java_path,
        settings.mc_java_xms,
        settings.mc_java_xmx,
        settings.mc_server_jar,
        settings.mc_extra_args,
        settings.mc_server_dir,
    )
