from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Callable

from src.mc.command_policy import BLOCKED, HIGH, classify_command, normalize_command
from src.repositories.command_repository import CommandRepository

if TYPE_CHECKING:
    from src.service.server_service import ServerService


class CommandService:
    def __init__(
        self,
        command_repository: CommandRepository,
        server_service: "ServerService | None" = None,
        offline_player_command_fallback: Callable[[str], dict] | None = None,
    ):
        self.command_repository = command_repository
        self._server_service = server_service
        self._offline_player_command_fallback = offline_player_command_fallback

    def classify(self, command: str) -> dict:
        risk = classify_command(command)
        return {
            "normalized_command": risk.normalized_command,
            "risk_level": risk.risk_level,
            "confirmation_required": risk.confirmation_required,
            "message": risk.message,
        }

    def propose_command(
        self,
        command: str,
        requested_by: str = "ai_tool",
    ) -> dict:
        command = (command or "").strip()
        risk = classify_command(command)
        if not command:
            return {
                "status": "empty",
                "command": "",
                "normalized_command": "",
                "risk_level": risk.risk_level,
                "confirmation_required": False,
                "message": risk.message,
                "audit_id": None,
            }

        status = "reviewed"
        if risk.risk_level == BLOCKED:
            status = "blocked"
        elif risk.confirmation_required:
            status = "confirmation_required"

        audit_id = self.command_repository.create_audit(
            command=command,
            normalized_command=risk.normalized_command,
            risk_level=risk.risk_level,
            requested_by=requested_by,
            confirmation_required=risk.confirmation_required,
            status=status,
        )
        return {
            "status": status,
            "audit_id": audit_id,
            "command": command,
            "normalized_command": risk.normalized_command,
            "risk_level": risk.risk_level,
            "confirmation_required": risk.confirmation_required,
            "message": risk.message,
        }

    def submit_command(
        self,
        command: str,
        requested_by: str = "ui",
        user_confirmed: bool = False,
    ) -> dict:
        command = (command or "").strip()
        if not command:
            risk = classify_command(command)
            return {
                "status": "empty",
                "command": "",
                "normalized_command": "",
                "risk_level": risk.risk_level,
                "confirmation_required": False,
                "message": risk.message,
                "output": None,
                "error_message": None,
                "audit_id": None,
            }

        normalized = normalize_command(command)
        risk = classify_command(command)
        audit_id = self.command_repository.create_audit(
            command=command,
            normalized_command=risk.normalized_command,
            risk_level=risk.risk_level,
            requested_by=requested_by,
            confirmation_required=risk.confirmation_required,
            status="requested",
        )

        if risk.risk_level == BLOCKED:
            self.command_repository.mark_executed(
                audit_id,
                status="blocked",
                error_message=risk.message,
            )
            return _result(
                audit_id=audit_id,
                command=command,
                normalized_command=risk.normalized_command,
                status="blocked",
                risk_level=risk.risk_level,
                confirmation_required=False,
                message=risk.message,
                error_message=risk.message,
            )

        if risk.risk_level == HIGH and not user_confirmed:
            self.command_repository.mark_status(
                audit_id,
                status="confirmation_required",
            )
            return _result(
                audit_id=audit_id,
                command=command,
                normalized_command=risk.normalized_command,
                status="confirmation_required",
                risk_level=risk.risk_level,
                confirmation_required=True,
                message=risk.message,
            )

        return self._execute_recorded_command(
            audit_id=audit_id,
            command=command,
            normalized_command=normalized,
            risk_level=risk.risk_level,
            confirmation_required=risk.confirmation_required,
            requested_by=requested_by,
        )

    def complete_confirmation(
        self,
        audit_id: int,
        command: str,
        approved: bool,
    ) -> dict:
        command = (command or "").strip()
        risk = classify_command(command)
        normalized = normalize_command(command)
        pending = self.command_repository.get_audit(audit_id)
        invalid_confirmation = (
            pending is None
            or pending.get("status") != "confirmation_required"
            or pending.get("normalized_command") != risk.normalized_command
            or risk.risk_level != HIGH
        )
        if invalid_confirmation:
            return _stale_confirmation_result(audit_id, command, normalized, risk.risk_level)

        if not approved:
            message = "用户已取消执行该 Minecraft 命令，未发送到服务器控制台。"
            transitioned = self.command_repository.transition_status(
                audit_id,
                expected_status="confirmation_required",
                status="cancelled",
                set_executed_at=True,
            )
            if not transitioned:
                return _stale_confirmation_result(audit_id, command, normalized, risk.risk_level)
            return _result(
                audit_id=audit_id,
                command=command,
                normalized_command=normalized,
                status="cancelled",
                risk_level=risk.risk_level,
                confirmation_required=True,
                message=message,
            )

        claimed = self.command_repository.transition_status(
            audit_id,
            expected_status="confirmation_required",
            status="executing",
        )
        if not claimed:
            return _stale_confirmation_result(audit_id, command, normalized, risk.risk_level)

        return self._execute_recorded_command(
            audit_id=audit_id,
            command=command,
            normalized_command=normalized,
            risk_level=risk.risk_level,
            confirmation_required=True,
            requested_by=str(pending.get("requested_by") or "ui"),
        )

    def _execute_recorded_command(
        self,
        audit_id: int,
        command: str,
        normalized_command: str,
        risk_level: str,
        confirmation_required: bool,
        requested_by: str = "ui",
    ) -> dict:
        if self._server_service is None:
            error_message = "服务器命令服务未配置。"
            self.command_repository.mark_executed(
                audit_id,
                status="failed",
                error_message=error_message,
            )
            return _result(
                audit_id=audit_id,
                command=command,
                normalized_command=normalized_command,
                status="failed",
                risk_level=risk_level,
                confirmation_required=confirmation_required,
                message=error_message,
                error_message=error_message,
            )

        send_result = self._server_service.send_command(command)
        status = send_result.get("status", "failed")
        output = send_result.get("output")
        error_message = send_result.get("error_message")

        if status == "executed":
            self.command_repository.mark_executed(
                audit_id,
                status="executed",
                output=output,
            )
            return _result(
                audit_id=audit_id,
                command=command,
                normalized_command=normalized_command,
                status="executed",
                risk_level=risk_level,
                confirmation_required=confirmation_required,
                message="命令已发送到 Minecraft 控制台。",
                output=output,
            )

        fallback_result = self._try_offline_player_config_fallback(
            command=command,
            requested_by=requested_by,
            error_message=error_message,
        )
        if fallback_result is not None:
            fallback_status = fallback_result.get("status", "failed")
            if fallback_status in {"file_updated", "no_change"}:
                message = fallback_result.get("message") or "已通过配置文件完成离线兜底。"
                self.command_repository.mark_executed(
                    audit_id,
                    status=fallback_status,
                    output=message,
                )
                return _result(
                    audit_id=audit_id,
                    command=command,
                    normalized_command=normalized_command,
                    status=fallback_status,
                    risk_level=risk_level,
                    confirmation_required=confirmation_required,
                    message=message,
                    output=message,
                    fallback=fallback_result.get("fallback"),
                    relative_path=fallback_result.get("relative_path"),
                )

            fallback_error = (
                fallback_result.get("error_message")
                or fallback_result.get("message")
                or error_message
            )
            self.command_repository.mark_executed(
                audit_id,
                status="failed",
                error_message=fallback_error,
            )
            return _result(
                audit_id=audit_id,
                command=command,
                normalized_command=normalized_command,
                status="failed",
                risk_level=risk_level,
                confirmation_required=confirmation_required,
                message=fallback_error or "离线配置兜底失败。",
                error_message=fallback_error,
                fallback=fallback_result.get("fallback"),
                relative_path=fallback_result.get("relative_path"),
            )

        self.command_repository.mark_executed(
            audit_id,
            status="failed",
            error_message=error_message,
        )
        return _result(
            audit_id=audit_id,
            command=command,
            normalized_command=normalized_command,
            status="failed",
            risk_level=risk_level,
            confirmation_required=confirmation_required,
            message=error_message or "命令发送失败。",
            error_message=error_message,
        )

    def _try_offline_player_config_fallback(
        self,
        command: str,
        requested_by: str,
        error_message: str | None,
    ) -> dict | None:
        if requested_by != "player_menu":
            return None
        if error_message != "服务器未运行。":
            return None
        if self._offline_player_command_fallback is None:
            return None
        return self._offline_player_command_fallback(command)

    def list_recent_audits(self, limit: int = 5) -> list[dict]:
        return self.command_repository.list_recent(limit=limit)


def _result(
    audit_id: int,
    command: str,
    normalized_command: str,
    status: str,
    risk_level: str,
    confirmation_required: bool,
    message: str,
    output: str | None = None,
    error_message: str | None = None,
    fallback: str | None = None,
    relative_path: object = None,
) -> dict:
    return {
        "audit_id": audit_id,
        "command": command,
        "normalized_command": normalized_command,
        "status": status,
        "risk_level": risk_level,
        "confirmation_required": confirmation_required,
        "message": message,
        "output": output,
        "error_message": error_message,
        "fallback": fallback,
        "relative_path": relative_path,
    }


def _stale_confirmation_result(
    audit_id: int,
    command: str,
    normalized_command: str,
    risk_level: str,
) -> dict:
    error_message = "待确认的命令审计不存在、已处理或与当前命令不一致，请重新发起命令。"
    return _result(
        audit_id=audit_id,
        command=command,
        normalized_command=normalized_command,
        status="failed",
        risk_level=risk_level,
        confirmation_required=False,
        message=error_message,
        error_message=error_message,
    )
