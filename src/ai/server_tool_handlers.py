from __future__ import annotations

import json
from typing import Any

from src.service.command_service import CommandService
from src.service.java_environment_service import JavaEnvironmentService
from src.service.log_service import LogService
from src.service.player_service import PlayerService
from src.service.server_service import ServerService
from src.service.system_service import SystemService


class ServerToolHandlers:
    def __init__(
        self,
        player_service: PlayerService,
        log_service: LogService,
        system_service: SystemService,
        server_service: ServerService,
        command_service: CommandService,
        java_environment_service: JavaEnvironmentService | None = None,
    ) -> None:
        self._player_service = player_service
        self._log_service = log_service
        self._system_service = system_service
        self._server_service = server_service
        self._command_service = command_service
        self._java_environment = java_environment_service

    def get_online_players(self, _args: dict[str, Any]) -> str:
        data = self._player_service.get_online_players()
        names = [player["name"] for player in data["players"]]
        return _json({
            "status": "ok",
            "online_count": data["online_count"],
            "players": names,
            "message": f"在线 {data['online_count']} 人：{', '.join(names)}" if names else "当前无人在线",
        })

    def query_system_metrics(self, _args: dict[str, Any]) -> str:
        sample = self._system_service.capture_metrics()
        cpu_percent = float(sample.get("cpu_percent") or 0)
        memory_percent = float(sample.get("memory_percent") or 0)
        return _json({
            "status": "ok",
            "metrics": sample,
            "message": f"CPU {cpu_percent:.0f}%，内存 {memory_percent:.0f}%",
        })

    def query_recent_logs(self, args: dict[str, Any]) -> str:
        level = args.get("level")
        if level is not None:
            level = str(level).upper()
            if level not in {"INFO", "WARN", "ERROR"}:
                return _json({
                    "status": "error",
                    "error": "level 只能是 INFO、WARN 或 ERROR。",
                })
        limit = _bounded_int(args.get("limit", 20), default=20, minimum=1, maximum=50)
        events = (
            self._log_service.list_recent(level=level, limit=limit)
            if level
            else self._log_service.list_recent(limit=limit)
        )
        lines = [
            f"[{event.get('event_time', '?')}] [{event.get('level', '?')}] {event.get('message', '')}"
            for event in events[:10]
        ]
        return _json({
            "status": "ok",
            "level": level,
            "limit": limit,
            "count": len(events),
            "events": events[:10],
            "message": "\n".join(lines) if lines else "最近没有匹配的日志。",
        })

    def get_server_status(self, _args: dict[str, Any]) -> str:
        status = self._server_service.get_status()
        return _json({
            "status": "ok",
            "server": status,
            "message": (
                f"状态：{status.get('state', 'unknown')}，"
                f"PID：{status.get('pid') or 'N/A'}，"
                f"在线玩家：{status.get('online_players', '?')}"
            ),
        })

    def check_java_environment(self, args: dict[str, Any]) -> str:
        if self._java_environment is None:
            return _json({
                "status": "failed",
                "error_message": "Java environment service is not configured.",
                "message": "Java 环境服务未配置。",
            })
        return _json(
            self._java_environment.check_environment(
                server_version=_optional_str(args.get("server_version")),
            )
        )

    def ensure_java_environment(self, args: dict[str, Any]) -> str:
        if self._java_environment is None:
            return _json({
                "status": "failed",
                "error_message": "Java environment service is not configured.",
                "message": "Java 环境服务未配置。",
            })
        start_after_ready = bool(args.get("start_after_ready", False))
        java_result = self._java_environment.ensure_environment(
            server_version=_optional_str(args.get("server_version")),
            start_after_ready=start_after_ready,
        )
        if start_after_ready and _java_ready(java_result):
            server = self._server_service.start_server()
            completed = dict(java_result)
            completed["server"] = server
            completed["message"] = (
                f"{java_result.get('message', '')} 服务器启动请求已提交，"
                f"状态：{server.get('state', 'unknown')}。"
            )
            return _json(completed)
        return _json(java_result)

    def start_server(self, _args: dict[str, Any]) -> str:
        java_result = None
        if self._java_environment is not None:
            java_result = self._java_environment.ensure_environment(start_after_ready=True)
            if not _java_ready(java_result):
                return _json({
                    "status": java_result.get("status", "failed"),
                    "java_environment": java_result,
                    "message": java_result.get("message") or "Java 环境尚未就绪。",
                    "error_message": java_result.get("error_message"),
                })
        status = self._server_service.start_server()
        state = status.get("state") or status.get("status") or "unknown"
        pid = status.get("pid")
        message = status.get("message") or ""
        return _json({
            "status": "ok",
            "server": status,
            "java_environment": java_result,
            "message": (
                f"服务器启动请求已提交，状态：{state}，PID：{pid or 'N/A'}。"
                if state in {"starting", "running"}
                else f"服务器启动请求处理完成，状态：{state}。{message}"
            ),
        })

    def restart_server(self, _args: dict[str, Any]) -> str:
        return _json(_server_restart_confirmation_action())

    def propose_server_command(self, args: dict[str, Any]) -> str:
        command = str(args.get("command", ""))
        return _json(self._command_service.propose_command(command, requested_by="ai_tool"))

    def execute_server_command(self, args: dict[str, Any]) -> str:
        command = str(args.get("command", ""))
        return _json(
            self._command_service.submit_command(
                command,
                requested_by="ai_tool",
                user_confirmed=False,
            )
        )


def _bounded_int(
    value: Any,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _java_ready(result: dict[str, Any]) -> bool:
    return result.get("status") in {"ok", "selected", "installed"}


def _server_restart_confirmation_action() -> dict[str, Any]:
    return {
        "status": "confirmation_required",
        "action_type": "server_restart",
        "command": "restart_server",
        "normalized_command": "restart_server",
        "display_name": "重启服务器",
        "risk_level": "HIGH",
        "confirmation_required": True,
        "audit_id": None,
        "message": "重启会先停止当前 Minecraft 服务器，待停止完成后再调用 start_server 启动。",
    }
