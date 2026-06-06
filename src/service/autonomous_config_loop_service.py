from __future__ import annotations

import json
from typing import Any

from src.ai.assistant_service import AssistantService
from src.ai.context_manager import ContextManager
from src.ai.prompts import SYSTEM_PROMPT_AUTONOMOUS_CONFIG_LOOP
from src.config.settings import Settings
from src.repositories.autonomous_task_repository import AutonomousTaskRepository
from src.service.config_edit_service import ConfigEditService
from src.service.log_service import LogService
from src.service.player_service import PlayerService
from src.service.server_service import ServerService
from src.service.system_service import SystemService


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "max_rounds_reached"}
_RISK_ORDER: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "BLOCKED": 3}


class AutonomousConfigLoopService:
    def __init__(
        self,
        task_repo: AutonomousTaskRepository,
        assistant_service: AssistantService,
        context_manager: ContextManager,
        config_edit_service: ConfigEditService,
        log_service: LogService,
        system_service: SystemService,
        server_service: ServerService,
        player_service: PlayerService,
        settings: Settings,
    ) -> None:
        self._repo = task_repo
        self._assistant = assistant_service
        self._context = context_manager
        self._config_edit = config_edit_service
        self._log_service = log_service
        self._system_service = system_service
        self._server_service = server_service
        self._player_service = player_service
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self._settings.autonomous_config_loop_enabled)

    def create_task(self, session_id: str, turn_id: str, user_goal: str) -> dict[str, Any]:
        if not self.enabled:
            return {
                "status": "disabled",
                "message": "自主配置修改循环未启用。",
            }
        task_id = self._repo.create_task(
            session_id=session_id,
            initial_turn_id=turn_id,
            kind="minecraft_config",
            user_goal=user_goal,
            max_rounds=self._settings.autonomous_config_loop_max_rounds,
            max_llm_calls=self._settings.autonomous_config_loop_max_llm_calls,
            max_tool_calls=self._settings.autonomous_config_loop_max_tool_calls,
            metadata={
                "runtime_verify_enabled": self._settings.autonomous_config_loop_verify_runtime,
                "auto_apply_max_risk": self._settings.autonomous_config_loop_auto_apply_max_risk,
            },
        )
        return self.run_until_pause_or_done(task_id)

    def run_until_pause_or_done(self, task_id: str) -> dict[str, Any]:
        task = self._repo.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id, "message": "未找到自主配置任务。"}
        if task["status"] in {"awaiting_user_confirmation", "needs_user_input"}:
            return self.get_task_status(task_id)
        if task["status"] in TERMINAL_STATUSES:
            return self.get_task_status(task_id)

        while int(task["current_round"]) < int(task["max_rounds"]):
            if int(task["llm_call_count"]) >= int(task["max_llm_calls"]):
                return self._stop(task_id, "failed", "达到最大 LLM 调用次数。")
            if int(task["tool_call_count"]) >= int(task["max_tool_calls"]):
                return self._stop(task_id, "failed", "达到最大工具调用次数。")

            round_index = self._repo.increment_round(task_id)
            self._repo.update_status(task_id, "planning")
            context = self._collect_context(task_id, round_index)

            self._repo.update_status(task_id, "proposing")
            proposal_result = self._propose_next_change(
                task_id=task_id,
                round_index=round_index,
                task=self._repo.get_task(task_id) or task,
                context=context,
            )
            status = proposal_result.get("status")
            if status == "proposal_created":
                proposal_id = str(proposal_result["proposal_id"])
                self._repo.add_artifact(
                    task_id,
                    round_index,
                    "config_proposal",
                    artifact_text_id=proposal_id,
                    metadata={
                        "risk_level": proposal_result.get("risk_level"),
                        "restart_required": proposal_result.get("restart_required"),
                    },
                )
                if self._requires_confirmation(proposal_result):
                    self._repo.create_step(
                        task_id,
                        round_index,
                        "wait_confirmation",
                        input_json={"proposal_id": proposal_id},
                        status="completed",
                    )
                    self._repo.update_status(
                        task_id,
                        "awaiting_user_confirmation",
                        stopped_reason="等待用户确认配置草案。",
                    )
                    return self.get_task_status(task_id)
                return self._apply_and_verify(task_id, proposal_id, high_risk_confirmed=False)

            if status == "no_change":
                return self._stop(
                    task_id,
                    "completed",
                    proposal_result.get("message") or "目标配置已经符合要求。",
                    final_summary=proposal_result.get("message") or "目标配置已经符合要求。",
                )
            return self._stop(
                task_id,
                "needs_user_input",
                proposal_result.get("message") or "无法生成安全配置草案。",
            )

        return self._stop(task_id, "max_rounds_reached", "达到最大自主循环轮数。")

    def continue_after_confirmation(
        self,
        task_id: str,
        proposal_id: str,
        approved: bool,
    ) -> dict[str, Any]:
        task = self._repo.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id, "message": "未找到自主配置任务。"}
        if task["status"] != "awaiting_user_confirmation":
            return self._stop(task_id, "failed", "任务不处于等待确认状态。")

        pending = self._repo.find_pending_proposal(task_id)
        if pending is None or pending["proposal_id"] != proposal_id:
            return self._stop(task_id, "failed", "待确认草案不存在或不属于该任务。")

        if not approved:
            reject_result = self._config_edit.reject_proposal(
                proposal_id,
                confirmed_by="autonomous_loop_rejected",
            )
            self._repo.create_step(
                task_id,
                int(pending["round_index"]),
                "wait_confirmation",
                input_json={"proposal_id": proposal_id, "approved": False},
                status="completed",
            )
            return self._stop(
                task_id,
                "cancelled",
                reject_result.get("message") or "用户拒绝草案，任务已停止。",
            )

        result = self._apply_and_verify(
            task_id,
            proposal_id,
            high_risk_confirmed=True,
            round_index=int(pending["round_index"]),
        )
        if result.get("status") == "completed":
            return result
        if result.get("status") == "failed":
            return result
        return self.run_until_pause_or_done(task_id)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self._repo.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id, "message": "未找到自主配置任务。"}
        pending = self._repo.find_pending_proposal(task_id)
        if pending is not None:
            self._config_edit.reject_proposal(
                pending["proposal_id"],
                confirmed_by="autonomous_loop_cancelled",
            )
        return self._stop(task_id, "cancelled", "用户取消自主配置任务。")

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        task = self._repo.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id, "message": "未找到自主配置任务。"}
        pending = self._repo.find_pending_proposal(task_id)
        proposal = (
            self._config_edit.get_proposal(pending["proposal_id"])
            if pending is not None
            else None
        )
        return {
            "status": task["status"],
            "task_id": task_id,
            "task": task,
            "current_proposal": proposal,
            "message": task.get("final_summary") or task.get("stopped_reason") or _status_message(task),
        }

    def _collect_context(self, task_id: str, round_index: int) -> dict[str, Any]:
        step_id = self._repo.create_step(task_id, round_index, "collect_context")
        context: dict[str, Any] = {
            "config_capabilities": self._safe_call(self._config_edit.list_capabilities),
            "server_properties": self._safe_call(
                self._config_edit.read_config_file,
                "server.properties",
            ),
            "runtime_feedback": {
                "server": self._safe_call(self._server_service.get_status),
                "metrics": self._safe_call(self._system_service.capture_metrics),
                "online_players": self._safe_call(self._player_service.get_online_players),
                "recent_logs": self._safe_call(self._log_service.list_recent, limit=8),
            },
            "runtime_verify_enabled": self._settings.autonomous_config_loop_verify_runtime,
        }
        self._repo.finish_step(step_id, "completed", output_json=context)
        return context

    def _propose_next_change(
        self,
        task_id: str,
        round_index: int,
        task: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        step_id = self._repo.create_step(
            task_id,
            round_index,
            "propose_change",
            input_json={"user_goal": task["user_goal"]},
        )
        prompt = {
            "task_id": task_id,
            "session_id": task["session_id"],
            "round_index": round_index,
            "user_goal": task["user_goal"],
            "context": context,
            "constraints": {
                "max_rounds": task["max_rounds"],
                "auto_apply_max_risk": self._settings.autonomous_config_loop_auto_apply_max_risk,
                "runtime_verify_enabled": self._settings.autonomous_config_loop_verify_runtime,
            },
            "instruction": (
                "如果需要修改配置，请调用 propose_config_change；"
                "只允许生成 server.properties 白名单配置草案。"
            ),
        }
        built_context = self._context.build_context(
            session_id=task["session_id"],
            turn_id=task["initial_turn_id"],
            user_message=json.dumps(prompt, ensure_ascii=False, default=str),
            model=getattr(self._assistant, "_llm", None).model if getattr(self._assistant, "_llm", None) else None,
            purpose="autonomous_config_loop",
            include_current_turn_messages=True,
            system_prompt=SYSTEM_PROMPT_AUTONOMOUS_CONFIG_LOOP,
        )
        result = self._assistant.reply(
            built_context.messages,
            session_id=task["session_id"],
            turn_id=task["initial_turn_id"],
            context_snapshot_id=built_context.snapshot_id,
        )
        trace = result.get("trace") or {}
        llm_ids = [int(item) for item in trace.get("llm_call_ids", [])]
        tool_ids = [int(item) for item in trace.get("tool_call_ids", [])]
        for llm_id in llm_ids:
            self._repo.add_artifact(task_id, round_index, "llm_call", artifact_int_id=llm_id)
        for tool_id in tool_ids:
            self._repo.add_artifact(task_id, round_index, "tool_call", artifact_int_id=tool_id)
        if llm_ids:
            self._repo.increment_llm_call_count(task_id, len(llm_ids))
        if tool_ids:
            self._repo.increment_tool_call_count(task_id, len(tool_ids))

        proposal = _extract_config_proposal(result.get("tool_results", []))
        if proposal is None:
            output = {
                "status": "needs_user_input",
                "message": result.get("content") or "模型未生成配置修改草案。",
            }
            self._repo.finish_step(step_id, "failed", output_json=output)
            return output
        self._repo.finish_step(step_id, "completed", output_json=proposal)
        return proposal

    def _apply_and_verify(
        self,
        task_id: str,
        proposal_id: str,
        high_risk_confirmed: bool,
        round_index: int | None = None,
    ) -> dict[str, Any]:
        task = self._repo.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id, "message": "未找到自主配置任务。"}
        round_index = round_index or int(task["current_round"])
        self._repo.update_status(task_id, "applying")
        apply_step = self._repo.create_step(
            task_id,
            round_index,
            "apply_change",
            input_json={"proposal_id": proposal_id},
        )
        apply_result = self._config_edit.apply_proposal(
            proposal_id,
            confirmed_by="autonomous_loop",
            high_risk_confirmed=high_risk_confirmed,
        )
        self._repo.finish_step(
            apply_step,
            "completed" if apply_result.get("status") == "saved" else "failed",
            output_json=apply_result,
            error_message=apply_result.get("message") if apply_result.get("status") != "saved" else None,
        )
        if apply_result.get("version_commit_id"):
            self._repo.add_artifact(
                task_id,
                round_index,
                "version_commit",
                artifact_text_id=apply_result.get("version_commit_id"),
            )
        if apply_result.get("status") == "conflict":
            return self._stop(task_id, "failed", apply_result.get("message") or "配置文件发生冲突。")
        if apply_result.get("status") != "saved":
            return self._stop(task_id, "failed", apply_result.get("message") or "配置应用失败。")

        verification = self._verify_file(task_id, round_index, proposal_id, apply_result)
        if verification.get("status") == "matched":
            restart_note = (
                "运行时效果需要重启服务器后验证。"
                if verification.get("restart_required")
                else "无需重启即可完成文件级目标。"
            )
            return self._stop(
                task_id,
                "completed",
                f"{verification.get('message', '文件级目标已达成')} {restart_note}",
                final_summary=f"{verification.get('message', '文件级目标已达成')} {restart_note}",
            )
        return self._stop(task_id, "failed", verification.get("message") or "文件级验证失败。")

    def _verify_file(
        self,
        task_id: str,
        round_index: int,
        proposal_id: str,
        apply_result: dict[str, Any],
    ) -> dict[str, Any]:
        self._repo.update_status(task_id, "verifying")
        step_id = self._repo.create_step(
            task_id,
            round_index,
            "verify_file",
            input_json={"proposal_id": proposal_id},
        )
        proposal = self._config_edit.get_proposal(proposal_id)
        if proposal is None:
            result = {"status": "failed", "message": "无法读取已应用的配置草案。"}
            self._repo.finish_step(step_id, "failed", output_json=result, error_message=result["message"])
            return result
        expected = {
            str(change["key"]): str(change["new_value"])
            for change in proposal.get("changes", [])
            if change.get("key") is not None
        }
        values_result = self._config_edit.get_values(
            proposal["relative_path"],
            list(expected.keys()),
        )
        actual = {
            item["key"]: str(item.get("value"))
            for item in values_result.get("values", [])
            if item.get("allowed")
        }
        mismatches = [
            {"key": key, "expected": value, "actual": actual.get(key)}
            for key, value in expected.items()
            if actual.get(key) != value
        ]
        status = "matched" if not mismatches and values_result.get("status") == "ok" else "mismatched"
        result = {
            "status": status,
            "relative_path": proposal["relative_path"],
            "expected": expected,
            "actual": actual,
            "mismatches": mismatches,
            "restart_required": bool(apply_result.get("restart_required") or proposal.get("restart_required")),
            "message": "文件级目标已达成。" if status == "matched" else "文件已保存但目标配置项不一致。",
        }
        self._repo.finish_step(
            step_id,
            "completed" if status == "matched" else "failed",
            output_json=result,
            error_message=None if status == "matched" else result["message"],
        )
        return result

    def _requires_confirmation(self, proposal: dict[str, Any]) -> bool:
        risk = str(proposal.get("risk_level") or "HIGH").upper()
        if risk in {"HIGH", "BLOCKED"}:
            return True
        if proposal.get("confirmation_required"):
            return True
        allowed = self._settings.autonomous_config_loop_auto_apply_max_risk.upper()
        if allowed in {"", "NONE", "DISABLED", "FALSE"}:
            return True
        return _RISK_ORDER.get(risk, 99) > _RISK_ORDER.get(allowed, -1)

    def _stop(
        self,
        task_id: str,
        status: str,
        reason: str,
        final_summary: str | None = None,
    ) -> dict[str, Any]:
        self._repo.update_status(
            task_id,
            status,
            stopped_reason=reason,
            final_summary=final_summary,
            completed=status in TERMINAL_STATUSES,
        )
        return self.get_task_status(task_id)

    def _safe_call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            return {
                "status": "failed",
                "error_message": str(exc),
            }


def _extract_config_proposal(tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(tool_results):
        if item.get("tool_name") != "propose_config_change":
            continue
        try:
            result = json.loads(item.get("result") or "{}")
        except json.JSONDecodeError:
            continue
        if result.get("status") in {"proposal_created", "no_change", "rejected"}:
            return result
    return None


def _status_message(task: dict[str, Any]) -> str:
    return f"自主配置任务状态：{task.get('status', 'unknown')}。"
