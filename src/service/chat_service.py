from __future__ import annotations

import json
import logging
import re
from typing import Iterator

from src.ai.assistant_service import AssistantService
from src.ai.context_manager import ContextManager
from src.ai.llm_client import LlmClient
from src.ai.stream_events import ChatStreamEvent, StreamEventType
from src.repositories.chat_attachment_repository import ChatAttachmentRepository
from src.repositories.chat_repository import ChatRepository
from src.repositories.chat_summary_repository import ChatSummaryRepository
from src.service.ai_model_service import AiModelService
from src.service.addon_diagnostic_service import AddonDiagnosticService
from src.service.config_edit_service import ConfigEditService
from src.service.autonomous_config_loop_service import AutonomousConfigLoopService
from src.service.command_service import CommandService
from src.service.java_environment_service import JavaEnvironmentService
from src.service.log_service import LogService
from src.service.player_service import PlayerService
from src.service.server_service import ServerService
from src.service.system_service import SystemService


logger = logging.getLogger(__name__)


class ChatService:
    def __init__(
        self,
        chat_repository: ChatRepository,
        player_service: PlayerService,
        log_service: LogService,
        system_service: SystemService,
        llm_client: LlmClient | None = None,
        assistant_service: AssistantService | None = None,
        context_manager: ContextManager | None = None,
        config_edit_service: ConfigEditService | None = None,
        command_service: CommandService | None = None,
        server_service: ServerService | None = None,
        java_environment_service: JavaEnvironmentService | None = None,
        autonomous_config_loop_service: AutonomousConfigLoopService | None = None,
        attachment_repo: ChatAttachmentRepository | None = None,
        summary_repo: ChatSummaryRepository | None = None,
        ai_model_service: AiModelService | None = None,
        addon_service: AddonDiagnosticService | None = None,
    ):
        self.chat_repository = chat_repository
        self.player_service = player_service
        self.log_service = log_service
        self.system_service = system_service
        self._llm = llm_client
        self._assistant = assistant_service
        self._context = context_manager
        self._config_edit = config_edit_service
        self._command_service = command_service
        self._server_service = server_service
        self._java_environment = java_environment_service
        self._autonomous_loop = autonomous_config_loop_service
        self._attachment_repo = attachment_repo
        self._summary_repo = summary_repo
        self._ai_models = ai_model_service
        self._addon_service = addon_service

    @property
    def has_ai(self) -> bool:
        configured = (
            True
            if self._ai_models is None
            else self._ai_models.is_selected_model_configured()
        )
        return (
            self._llm is not None
            and self._assistant is not None
            and self._context is not None
            and configured
        )

    def list_ai_models(self) -> list[dict]:
        if self._ai_models is None:
            return []
        return self._ai_models.list_models()

    def get_selected_ai_model(self) -> dict:
        if self._ai_models is None:
            model = getattr(self._llm, "model", "")
            return {
                "id": model,
                "display_name": model,
                "short_name": model,
                "provider": "",
                "selected": True,
                "configured": self.has_ai,
            }
        return self._ai_models.get_selected_model()

    def select_ai_model(self, model_id: str) -> dict:
        if self._ai_models is None:
            raise RuntimeError("AI model service is not configured")
        return self._ai_models.select_model(model_id)

    def create_session(self, title: str | None = "server_ops") -> str:
        return self.chat_repository.create_session(title)

    def list_sessions(self, limit: int = 20) -> list[dict]:
        return self.chat_repository.list_sessions(limit=limit)

    def get_session_view(self, session_id: str, limit_turns: int = 50) -> dict:
        view = self.chat_repository.get_session_view(
            session_id=session_id,
            limit_turns=limit_turns,
        )
        if self._attachment_repo is not None:
            for turn in view.get("turns", []):
                turn["attachments"] = [
                    _stored_attachment_display(attachment)
                    for attachment in self._attachment_repo.list_for_turn(turn.get("id", ""))
                ]
        return view

    def list_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        return self.chat_repository.list_messages(session_id=session_id, limit=limit)

    def attach_log_selection(self, session_id: str, selection: dict) -> dict:
        if self._attachment_repo is None:
            raise RuntimeError("Attachment repository not configured")
        attachment_id = self._attachment_repo.create(
            session_id=session_id,
            kind="log_selection",
            label=selection.get("label", "日志片段"),
            content=selection.get("raw_text", ""),
            metadata={
                "source": selection.get("source", ""),
                "line_count": selection.get("line_count", 0),
                "time_range": selection.get("time_range", ""),
                "event_ids": selection.get("event_ids", []),
            },
        )
        return {
            "attachment_id": attachment_id,
            "label": selection.get("label", "日志片段"),
            "line_count": selection.get("line_count", 0),
            "time_range": selection.get("time_range", ""),
        }

    def confirm_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id)
        recorded = self.execute_confirmed_command_action(
            session_id,
            command,
            audit_id=audit_id,
            turn_id=turn_id,
        )
        return self.complete_command_action_feedback(
            session_id,
            recorded,
            action_kind="command_confirmation",
            turn_id=turn_id,
        )

    def execute_confirmed_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id)
        if _is_server_restart_action(command):
            return self.execute_confirmed_server_restart_action(session_id, turn_id=turn_id)

        log_cursor = self._capture_server_log_cursor()
        if self._command_service is None:
            result = {
                "status": "failed",
                "command": command,
                "message": "Command service is not configured.",
                "error_message": "Command service is not configured.",
            }
        elif audit_id is not None:
            result = self._command_service.complete_confirmation(
                audit_id=audit_id,
                command=command,
                approved=True,
            )
        else:
            result = self._command_service.submit_command(
                command=command,
                requested_by="ui_confirmed",
                user_confirmed=True,
            )
        immediate_result = dict(result)
        immediate_result["_server_log_cursor"] = log_cursor
        immediate_result["turn_id"] = turn_id
        return immediate_result

    def execute_confirmed_server_restart_action(
        self,
        session_id: str,
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id)
        del session_id
        log_cursor = self._capture_server_log_cursor()
        if self._server_service is None:
            result = _server_restart_result({
                "operation": "restart_server",
                "status": "failed",
                "message": "Server service is not configured.",
                "error_message": "Server service is not configured.",
                "stop": None,
                "stopped": None,
                "start": None,
            })
        else:
            result = _server_restart_result(self._server_service.restart_server())
        immediate_result = dict(result)
        immediate_result["_server_log_cursor"] = log_cursor
        immediate_result["turn_id"] = turn_id
        return immediate_result

    def cancel_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id)
        recorded = self.record_cancelled_command_action(
            session_id,
            command,
            audit_id=audit_id,
            turn_id=turn_id,
        )
        return self.complete_command_action_feedback(
            session_id,
            recorded,
            action_kind="command_cancelled",
            turn_id=turn_id,
        )

    def record_cancelled_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id)
        if _is_server_restart_action(command):
            del session_id, audit_id
            result = _server_restart_result({
                "operation": "restart_server",
                "status": "cancelled",
                "message": "用户已取消重启服务器，未停止或启动服务器。",
                "stop": None,
                "stopped": None,
                "start": None,
            })
            result["turn_id"] = turn_id
            return result

        log_cursor = self._capture_server_log_cursor()
        if self._command_service is not None and audit_id is not None:
            result = self._command_service.complete_confirmation(
                audit_id=audit_id,
                command=command,
                approved=False,
            )
        else:
            result = {
                "status": "cancelled",
                "command": command,
                "message": "用户已取消执行该 Minecraft 命令，未发送到服务器控制台。",
            }
        immediate_result = dict(result)
        immediate_result["_server_log_cursor"] = log_cursor
        immediate_result["turn_id"] = turn_id
        return immediate_result

    def complete_command_action_feedback(
        self,
        session_id: str,
        recorded_result: dict,
        action_kind: str = "command_confirmation",
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id or recorded_result.get("turn_id"))
        is_restart = _is_server_restart_result(recorded_result)
        tool_name = "restart_server" if is_restart else "execute_server_command"
        if is_restart and action_kind == "command_confirmation":
            action_kind = "server_restart"
        elif is_restart and action_kind == "command_cancelled":
            action_kind = "server_restart_cancelled"
        result_for_feedback = recorded_result
        if recorded_result.get("tool_message_id") is None:
            log_cursor = recorded_result.get("_server_log_cursor")
            recent_logs = self._operation_server_log_lines(log_cursor)
            current_online_players = self._current_online_player_snapshot()
            persisted_result = {
                key: value
                for key, value in recorded_result.items()
                if key != "_server_log_cursor"
            }
            result_for_feedback = self._record_interactive_tool_result(
                session_id=session_id,
                turn_id=turn_id,
                tool_name=tool_name,
                action_kind=action_kind,
                result=persisted_result,
                recent_server_logs=recent_logs,
                current_online_players=current_online_players,
            )
        return self._complete_interactive_action(
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            action_kind=action_kind,
            result=result_for_feedback,
            recent_server_logs=result_for_feedback.get("recent_server_logs", []),
            current_online_players=result_for_feedback.get("current_online_players"),
        )

    def apply_config_proposal(
        self,
        session_id: str,
        proposal_id: str,
        high_risk_confirmed: bool = False,
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id)
        recorded = self.execute_config_proposal_action(
            session_id,
            proposal_id,
            high_risk_confirmed=high_risk_confirmed,
            turn_id=turn_id,
        )
        return self.complete_config_action_feedback(
            session_id,
            recorded,
            action_kind="config_apply",
            turn_id=turn_id,
        )

    def execute_config_proposal_action(
        self,
        session_id: str,
        proposal_id: str,
        high_risk_confirmed: bool = False,
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id)
        log_cursor = self._capture_server_log_cursor()
        if self._config_edit is None:
            result = {
                "status": "failed",
                "proposal_id": proposal_id,
                "message": "Config edit service is not configured.",
                "error_message": "Config edit service is not configured.",
            }
        else:
            result = self._config_edit.apply_proposal(
                proposal_id=proposal_id,
                confirmed_by="ui_confirmed",
                high_risk_confirmed=high_risk_confirmed,
            )
        recent_logs = self._operation_server_log_lines(log_cursor)
        recorded = self._record_interactive_tool_result(
            session_id=session_id,
            turn_id=turn_id,
            tool_name="apply_config_proposal",
            action_kind="config_apply",
            result=result,
            recent_server_logs=recent_logs,
        )
        return recorded

    def reject_config_proposal(
        self,
        session_id: str,
        proposal_id: str,
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id)
        recorded = self.record_rejected_config_proposal_action(
            session_id,
            proposal_id,
            turn_id=turn_id,
        )
        return self.complete_config_action_feedback(
            session_id,
            recorded,
            action_kind="config_reject",
            turn_id=turn_id,
        )

    def record_rejected_config_proposal_action(
        self,
        session_id: str,
        proposal_id: str,
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id)
        if self._config_edit is None:
            result = {
                "status": "failed",
                "proposal_id": proposal_id,
                "message": "Config edit service is not configured.",
                "error_message": "Config edit service is not configured.",
            }
        else:
            result = self._config_edit.reject_proposal(
                proposal_id=proposal_id,
                confirmed_by="ui_rejected",
            )
        return self._record_interactive_tool_result(
            session_id=session_id,
            turn_id=turn_id,
            tool_name="reject_config_proposal",
            action_kind="config_reject",
            result=result,
            recent_server_logs=[],
        )

    def complete_config_action_feedback(
        self,
        session_id: str,
        recorded_result: dict,
        action_kind: str = "config_apply",
        turn_id: str | None = None,
    ) -> dict:
        turn_id = self._resolve_turn_id(session_id, turn_id or recorded_result.get("turn_id"))
        tool_name = (
            "reject_config_proposal"
            if action_kind == "config_reject"
            else "apply_config_proposal"
        )
        result_for_feedback = recorded_result
        if recorded_result.get("tool_message_id") is None:
            result_for_feedback = self._record_interactive_tool_result(
                session_id=session_id,
                turn_id=turn_id,
                tool_name=tool_name,
                action_kind=action_kind,
                result=recorded_result,
                recent_server_logs=[],
            )
        return self._complete_interactive_action(
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            action_kind=action_kind,
            result=result_for_feedback,
            recent_server_logs=result_for_feedback.get("recent_server_logs", []),
        )

    def send_message(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> dict:
        return self.send_turn(session_id, user_message, attachment_ids, prompt_parts)

    def send_turn(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> dict:
        turn_id = self._begin_turn(session_id, user_message, attachment_ids, prompt_parts)
        if self._should_start_autonomous_config_loop(user_message):
            return self._start_autonomous_config_loop_turn(
                session_id=session_id,
                turn_id=turn_id,
                user_message=user_message,
            )
        prefer_ai_config_flow = self.has_ai and _looks_like_config_change_request(user_message)
        prefer_ai_command_flow = self.has_ai and _looks_like_command_request(user_message)
        local = (
            None
            if attachment_ids or prefer_ai_config_flow or prefer_ai_command_flow
            else self._try_answer_with_local_tools(user_message)
        )
        if local and local["matched"]:
            tool_id = None
            if local["tool_result"]:
                tool_id = self.chat_repository.add_message(
                    session_id,
                    "tool",
                    local["tool_result"],
                    tool_name=local["tool_name"],
                    turn_id=turn_id,
                    visibility="context_only",
                )
            local = self._with_turn_in_actions(local, turn_id)
            assistant_id = self.chat_repository.add_message(
                session_id,
                "assistant",
                local["assistant"],
                turn_id=turn_id,
            )
            self.chat_repository.update_turn(turn_id, status="completed", source="local", completed=True)
            self._maybe_summarize(session_id)
            return {
                "turn_id": turn_id,
                "assistant_message_id": assistant_id,
                "tool_message_id": tool_id,
                "assistant": local["assistant"],
                "tool_name": local["tool_name"],
                "tool_result": local["tool_result"],
                "config_proposal": local.get("config_proposal"),
                "command_action": local.get("command_action"),
                "command_actions": local.get("command_actions"),
                "server_action": local.get("server_action"),
                "java_environment": local.get("java_environment"),
                "addon_report": local.get("addon_report"),
                "source": "local",
            }

        if not self.has_ai:
            text = "AI 服务未配置。当前仍可使用本地工具查询玩家、指标和最近 ERROR 日志。"
            assistant_id = self.chat_repository.add_message(
                session_id,
                "assistant",
                text,
                turn_id=turn_id,
            )
            self.chat_repository.update_turn(
                turn_id,
                status="completed",
                source="local_fallback",
                completed=True,
            )
            self._maybe_summarize(session_id)
            return {
                "turn_id": turn_id,
                "assistant_message_id": assistant_id,
                "assistant": text,
                "source": "local_fallback",
            }

        return self._ai_reply(session_id, turn_id, user_message, attachment_ids)

    def stream_message(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> Iterator[ChatStreamEvent]:
        return self.stream_turn(session_id, user_message, attachment_ids, prompt_parts)

    def stream_turn(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> Iterator[ChatStreamEvent]:
        turn_id = self._begin_turn(session_id, user_message, attachment_ids, prompt_parts)
        if self._should_start_autonomous_config_loop(user_message):
            result = self._start_autonomous_config_loop_turn(
                session_id=session_id,
                turn_id=turn_id,
                user_message=user_message,
            )
            yield ChatStreamEvent(event_type="message_start")
            yield ChatStreamEvent(event_type="delta", text=result.get("assistant", ""))
            yield ChatStreamEvent(event_type="message_end")
            return
        prefer_ai_config_flow = self.has_ai and _looks_like_config_change_request(user_message)
        prefer_ai_command_flow = self.has_ai and _looks_like_command_request(user_message)
        local = (
            None
            if attachment_ids or prefer_ai_config_flow or prefer_ai_command_flow
            else self._try_answer_with_local_tools(user_message)
        )
        if local and local["matched"]:
            if local["tool_result"]:
                self.chat_repository.add_message(
                    session_id,
                    "tool",
                    local["tool_result"],
                    tool_name=local["tool_name"],
                    turn_id=turn_id,
                    visibility="context_only",
                )
            local = self._with_turn_in_actions(local, turn_id)
            yield ChatStreamEvent(event_type="message_start")
            yield ChatStreamEvent(event_type="delta", text=local["assistant"])
            if local.get("command_action"):
                yield ChatStreamEvent(
                    event_type=StreamEventType.COMMAND_ACTION,
                    text=local["assistant"],
                    command_action=local["command_action"],
                )
            if local.get("server_action"):
                yield ChatStreamEvent(
                    event_type=StreamEventType.SERVER_ACTION,
                    server_action=local["server_action"],
                )
            yield ChatStreamEvent(event_type="message_end")
            self.chat_repository.add_message(
                session_id,
                "assistant",
                local["assistant"],
                turn_id=turn_id,
            )
            self.chat_repository.update_turn(turn_id, status="completed", source="local", completed=True)
            self._maybe_summarize(session_id)
            return

        if not self.has_ai or self._assistant is None or self._context is None:
            text = "AI 服务未配置。当前仍可使用本地工具查询玩家、指标和最近 ERROR 日志。"
            yield ChatStreamEvent(event_type="message_start")
            yield ChatStreamEvent(event_type="delta", text=text)
            yield ChatStreamEvent(event_type="message_end")
            self.chat_repository.add_message(session_id, "assistant", text, turn_id=turn_id)
            self.chat_repository.update_turn(
                turn_id,
                status="completed",
                source="local_fallback",
                completed=True,
            )
            self._maybe_summarize(session_id)
            return

        built_context = self._context.build_context(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            attachment_ids=attachment_ids,
            model=getattr(self._llm, "model", None),
            purpose="chat_stream",
        )
        full_content = ""
        command_action: dict | None = None
        config_proposal: dict | None = None
        tool_results: list[dict] = []
        for event in self._assistant.stream_reply(
            built_context.messages,
            session_id=session_id,
            turn_id=turn_id,
            context_snapshot_id=built_context.snapshot_id,
        ):
            if (
                event.event_type == StreamEventType.DELTA
                and command_action is None
                and config_proposal is None
            ):
                full_content += event.text
            elif event.event_type == StreamEventType.DELTA:
                continue
            yield event
            if event.event_type == StreamEventType.TOOL_RESULT:
                tool_results.append({
                    "tool_name": event.tool_name,
                    "arguments": event.tool_arguments or {},
                    "result": event.tool_result,
                    "provider_tool_call_id": (event.tool_arguments or {}).get("_provider_tool_call_id"),
                })
                self._record_ai_tool_result_message(session_id, turn_id, tool_results[-1])
                command_action = _extract_command_action([{
                    "tool_name": event.tool_name,
                    "result": event.tool_result,
                }])
                if command_action is not None:
                    command_action["turn_id"] = turn_id
                    full_content = _format_command_action_answer(command_action)
                    yield ChatStreamEvent(
                        event_type=StreamEventType.COMMAND_ACTION,
                        text=full_content,
                        command_action=command_action,
                    )
                extracted_proposal = _extract_config_proposal([{
                    "tool_name": event.tool_name,
                    "result": event.tool_result,
                }])
                if extracted_proposal is not None:
                    config_proposal = _with_turn_id(extracted_proposal, turn_id)
                    full_content = _format_config_proposal_answer(config_proposal)
                    yield ChatStreamEvent(
                        event_type=StreamEventType.CONFIG_PROPOSAL,
                        text=full_content,
                        config_proposal=config_proposal,
                    )
                server_action = _extract_server_action([{
                    "tool_name": event.tool_name,
                    "result": event.tool_result,
                }])
                if server_action is not None:
                    yield ChatStreamEvent(
                        event_type=StreamEventType.SERVER_ACTION,
                        server_action=server_action,
                    )
        if full_content:
            self.chat_repository.add_message(
                session_id,
                "assistant",
                full_content,
                turn_id=turn_id,
            )
        self.chat_repository.update_turn(turn_id, status="completed", source="ai", completed=True)
        self._maybe_summarize(session_id)

    def _ai_reply(
        self,
        session_id: str,
        turn_id: str,
        user_message: str,
        attachment_ids: list[str] | None,
    ) -> dict:
        if self._assistant is None or self._context is None:
            return {"assistant": "AI 服务未配置。", "source": "error"}
        built_context = self._context.build_context(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            attachment_ids=attachment_ids,
            model=getattr(self._llm, "model", None),
            purpose="chat",
        )
        result = self._assistant.reply(
            built_context.messages,
            session_id=session_id,
            turn_id=turn_id,
            context_snapshot_id=built_context.snapshot_id,
        )
        self._record_ai_tool_result_messages(
            session_id,
            turn_id,
            result.get("tool_results", []),
        )
        config_proposal = _extract_config_proposal(result.get("tool_results", []))
        if config_proposal is not None:
            config_proposal = _with_turn_id(config_proposal, turn_id)
        command_actions = _extract_command_actions(result.get("tool_results", []))
        server_action = _extract_server_action(result.get("tool_results", []))
        java_environment = _extract_java_environment(result.get("tool_results", []))
        if _looks_like_command_request(user_message):
            command_actions = self._ensure_requested_command_actions(
                user_message,
                command_actions,
            )
        command_actions = [_with_turn_id(action, turn_id) for action in command_actions]
        command_action = command_actions[0] if command_actions else None
        assistant_text = (
            _format_command_actions_answer(command_actions)
            if command_actions
            else _format_config_proposal_answer(config_proposal)
            if config_proposal
            else _format_server_start_with_java_answer({
                "java_environment": java_environment,
                "server": server_action.get("server") if server_action else None,
                "message": server_action.get("message") if server_action else None,
            })
            if java_environment and server_action
            else _format_java_environment_answer(java_environment)
            if java_environment
            else result["content"]
        )
        assistant_id = self.chat_repository.add_message(
            session_id,
            "assistant",
            assistant_text,
            turn_id=turn_id,
        )
        self.chat_repository.update_turn(turn_id, status="completed", source="ai", completed=True)
        self._maybe_summarize(session_id)
        return {
            "turn_id": turn_id,
            "assistant_message_id": assistant_id,
            "assistant": assistant_text,
            "model": result.get("model", ""),
            "config_proposal": config_proposal,
            "command_action": command_action,
            "command_actions": command_actions,
            "server_action": server_action,
            "java_environment": java_environment,
            "source": "ai",
        }

    def _begin_turn(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None,
        prompt_parts: list[dict] | None = None,
    ) -> str:
        turn_id = self.chat_repository.create_turn(session_id, source="pending")
        for attachment_id in attachment_ids or []:
            if self._attachment_repo is not None:
                attachment = self._attachment_repo.get(attachment_id)
                if attachment is not None:
                    self._attachment_repo.bind_to_turn(attachment_id, turn_id)
        self.chat_repository.add_message(
            session_id,
            "user",
            user_message,
            turn_id=turn_id,
            metadata=_message_metadata(prompt_parts),
        )
        return turn_id

    def create_autonomous_config_task(self, session_id: str, user_goal: str) -> dict:
        turn_id = self._begin_turn(session_id, user_goal, attachment_ids=None)
        return self._start_autonomous_config_loop_turn(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_goal,
        )

    def continue_autonomous_task(
        self,
        task_id: str,
        proposal_id: str,
        approved: bool,
    ) -> dict:
        if self._autonomous_loop is None:
            return {
                "status": "failed",
                "task_id": task_id,
                "proposal_id": proposal_id,
                "assistant": "自主配置循环服务未配置。",
            }
        result = self._autonomous_loop.continue_after_confirmation(
            task_id=task_id,
            proposal_id=proposal_id,
            approved=approved,
        )
        return self._record_autonomous_loop_feedback(
            result,
            action_kind="autonomous_config_continue",
        )

    def cancel_autonomous_task(self, task_id: str) -> dict:
        if self._autonomous_loop is None:
            return {
                "status": "failed",
                "task_id": task_id,
                "assistant": "自主配置循环服务未配置。",
            }
        result = self._autonomous_loop.cancel_task(task_id)
        return self._record_autonomous_loop_feedback(
            result,
            action_kind="autonomous_config_cancel",
        )

    def get_autonomous_task_status(self, task_id: str) -> dict:
        if self._autonomous_loop is None:
            return {
                "status": "failed",
                "task_id": task_id,
                "message": "自主配置循环服务未配置。",
            }
        return self._autonomous_loop.get_task_status(task_id)

    def _should_start_autonomous_config_loop(self, text: str) -> bool:
        return (
            self._autonomous_loop is not None
            and self._autonomous_loop.enabled
            and self.has_ai
            and _looks_like_autonomous_config_loop_request(text)
        )

    def _start_autonomous_config_loop_turn(
        self,
        session_id: str,
        turn_id: str,
        user_message: str,
    ) -> dict:
        if self._autonomous_loop is None:
            text = "自主配置循环服务未配置。"
            assistant_id = self.chat_repository.add_message(
                session_id,
                "assistant",
                text,
                turn_id=turn_id,
            )
            self.chat_repository.update_turn(
                turn_id,
                status="completed",
                source="autonomous_config_loop",
                completed=True,
            )
            return {
                "turn_id": turn_id,
                "assistant_message_id": assistant_id,
                "assistant": text,
                "source": "autonomous_config_loop",
            }
        result = self._autonomous_loop.create_task(session_id, turn_id, user_message)
        self._record_autonomous_tool_result(
            session_id=session_id,
            turn_id=turn_id,
            action_kind="autonomous_config_start",
            result=result,
        )
        assistant_text = _format_autonomous_task_answer(result)
        assistant_id = self.chat_repository.add_message(
            session_id,
            "assistant",
            assistant_text,
            turn_id=turn_id,
        )
        self.chat_repository.update_turn(
            turn_id,
            status="completed",
            source="autonomous_config_loop",
            completed=True,
        )
        self._maybe_summarize(session_id)
        return {
            "turn_id": turn_id,
            "assistant_message_id": assistant_id,
            "assistant": assistant_text,
            "autonomous_task": result,
            "config_proposal": result.get("current_proposal"),
            "source": "autonomous_config_loop",
        }

    def _record_autonomous_loop_feedback(
        self,
        result: dict,
        action_kind: str,
    ) -> dict:
        task = result.get("task") or {}
        session_id = task.get("session_id")
        turn_id = task.get("initial_turn_id")
        if not session_id or not turn_id:
            completed = dict(result)
            completed["assistant"] = _format_autonomous_task_answer(result)
            return completed
        self._record_autonomous_tool_result(
            session_id=session_id,
            turn_id=turn_id,
            action_kind=action_kind,
            result=result,
        )
        assistant_text = _format_autonomous_task_answer(result)
        assistant_id = self.chat_repository.add_message(
            session_id,
            "assistant",
            assistant_text,
            turn_id=turn_id,
        )
        self.chat_repository.update_turn(
            turn_id,
            status="completed",
            source="autonomous_config_loop",
            completed=True,
        )
        self._maybe_summarize(session_id)
        completed = dict(result)
        completed["assistant"] = assistant_text
        completed["assistant_message_id"] = assistant_id
        completed["autonomous_task"] = result
        completed["config_proposal"] = result.get("current_proposal")
        return completed

    def _record_autonomous_tool_result(
        self,
        session_id: str,
        turn_id: str,
        action_kind: str,
        result: dict,
    ) -> str:
        payload = {
            "interactive_action": action_kind,
            "result": result,
        }
        return self.chat_repository.add_message(
            session_id=session_id,
            turn_id=turn_id,
            role="tool",
            visibility="context_only",
            content_type="tool_result",
            content=json.dumps(payload, ensure_ascii=False, default=str),
            tool_name="autonomous_config_loop",
        )

    def _resolve_turn_id(self, session_id: str, turn_id: str | None) -> str:
        if turn_id:
            return turn_id
        latest = self.chat_repository.get_latest_turn(session_id)
        if latest is not None:
            return str(latest["id"])
        return self.chat_repository.create_turn(session_id, source="interactive")

    def _with_turn_in_actions(self, local: dict, turn_id: str) -> dict:
        updated = dict(local)
        if updated.get("config_proposal"):
            updated["config_proposal"] = _with_turn_id(updated["config_proposal"], turn_id)
        if updated.get("command_action"):
            updated["command_action"] = _with_turn_id(updated["command_action"], turn_id)
        if updated.get("command_actions"):
            updated["command_actions"] = [
                _with_turn_id(action, turn_id)
                for action in updated["command_actions"]
            ]
        return updated

    def _record_ai_tool_result_messages(
        self,
        session_id: str,
        turn_id: str,
        tool_results: list[dict],
    ) -> None:
        for tool_result in tool_results:
            self._record_ai_tool_result_message(session_id, turn_id, tool_result)

    def _record_ai_tool_result_message(
        self,
        session_id: str,
        turn_id: str,
        tool_result: dict,
    ) -> str:
        payload = {
            "tool_name": tool_result.get("tool_name"),
            "arguments": tool_result.get("arguments") or {},
            "result": tool_result.get("result"),
            "ok": tool_result.get("ok", True),
            "error_message": tool_result.get("error_message"),
        }
        return self.chat_repository.add_message(
            session_id=session_id,
            turn_id=turn_id,
            role="tool",
            visibility="context_only",
            content=json.dumps(payload, ensure_ascii=False, default=str),
            tool_name=tool_result.get("tool_name"),
            tool_call_id=tool_result.get("provider_tool_call_id"),
            content_type="tool_result",
        )

    def _try_answer_with_local_tools(self, text: str) -> dict:
        lowered = text.lower()
        restart_local = self._try_server_restart_local(text)
        if restart_local:
            return restart_local

        command_local = self._try_command_local(text)
        if command_local:
            return command_local

        java_local = self._try_java_environment_local(text)
        if java_local:
            return java_local

        start_local = self._try_server_start_local(text)
        if start_local:
            return start_local

        config_local = self._try_config_change_local(text)
        if config_local:
            return config_local

        addon_local = self._try_addon_diagnostics_local(text)
        if addon_local:
            return addon_local

        if _looks_like_online_player_query(text):
            data = self.player_service.get_online_players()
            names = [player["name"] for player in data["players"]]
            name_text = "、".join(names) if names else "无"
            return {
                "matched": True,
                "tool_name": "get_online_players",
                "tool_result": "get_online_players: " + ", ".join(names),
                "assistant": f"当前在线 {data['online_count']} 人：{name_text}。",
            }

        if "cpu" in lowered or "内存" in text or "卡" in text:
            sample = self.system_service.capture_metrics()
            cpu = float(sample.get("cpu_percent") or 0)
            mem = float(sample.get("memory_percent") or 0)
            return {
                "matched": True,
                "tool_name": "query_system_metrics",
                "tool_result": f"query_system_metrics: cpu={cpu:.0f}%, memory={mem:.0f}%",
                "assistant": (
                    f"当前 CPU {cpu:.0f}%，内存 {mem:.0f}%。"
                    "如果持续出现 Can't keep up 日志，建议先观察插件任务和区块加载。"
                ),
            }

        if "报错" in text or "error" in lowered or "日志" in text:
            errors = self.log_service.list_recent(level="ERROR", limit=3)
            answer = (
                "最近有 ERROR 级别日志，建议先定位发生时间附近的玩家行为和区块加载。"
                if errors
                else "最近没有 ERROR 级别日志。"
            )
            return {
                "matched": True,
                "tool_name": "query_recent_logs",
                "tool_result": f"query_recent_logs(level=ERROR): {len(errors)} 条",
                "assistant": answer,
            }

        return {
            "matched": False,
            "tool_name": None,
            "tool_result": None,
            "assistant": "请补充时间范围、玩家 ID 或具体现象。",
        }

    def _try_server_restart_local(self, text: str) -> dict | None:
        if not _looks_like_restart_request(text):
            return None

        action = _server_restart_confirmation_action()
        return {
            "matched": True,
            "tool_name": "restart_server",
            "tool_result": json.dumps(action, ensure_ascii=False),
            "command_action": action,
            "command_actions": [action],
            "assistant": _format_command_action_answer(action),
        }

    def _try_command_local(self, text: str) -> dict | None:
        if self._command_service is None:
            return None

        commands = _extract_command_requests(text)
        if not commands:
            return None

        results = [
            self._command_service.submit_command(
                command,
                requested_by="ui_chat",
                user_confirmed=False,
            )
            for command in commands
        ]
        return _command_local_result(results)

    def _try_java_environment_local(self, text: str) -> dict | None:
        if self._java_environment is None or not _looks_like_java_environment_request(text):
            return None
        server_version = _extract_minecraft_version(text)
        ensure = _looks_like_java_environment_fix_request(text)
        result = (
            self._java_environment.ensure_environment(server_version=server_version)
            if ensure
            else self._java_environment.check_environment(server_version=server_version)
        )
        tool_name = "ensure_java_environment" if ensure else "check_java_environment"
        return {
            "matched": True,
            "tool_name": tool_name,
            "tool_result": json.dumps(result, ensure_ascii=False, default=str),
            "java_environment": result,
            "assistant": _format_java_environment_answer(result),
        }

    def _try_server_start_local(self, text: str) -> dict | None:
        if (
            self._java_environment is None
            or self._server_service is None
            or not _looks_like_start_request(text)
        ):
            return None
        java_result = self._java_environment.ensure_environment(
            server_version=_extract_minecraft_version(text),
            start_after_ready=True,
        )
        if java_result.get("status") not in {"ok", "selected", "installed"}:
            return {
                "matched": True,
                "tool_name": "ensure_java_environment",
                "tool_result": json.dumps(java_result, ensure_ascii=False, default=str),
                "java_environment": java_result,
                "assistant": _format_java_environment_answer(java_result),
            }

        server = self._server_service.start_server()
        result = {
            "status": "ok",
            "java_environment": java_result,
            "server": server,
            "message": (
                f"{java_result.get('message', '')} 服务器启动请求已提交，"
                f"状态：{server.get('state', 'unknown')}。"
            ),
        }
        return {
            "matched": True,
            "tool_name": "start_server",
            "tool_result": json.dumps(result, ensure_ascii=False, default=str),
            "java_environment": java_result,
            "server_action": _server_start_action_from_result(result),
            "assistant": _format_server_start_with_java_answer(result),
        }

    def _try_config_change_local(self, text: str) -> dict | None:
        if self._config_edit is None:
            return None

        changes: list[dict] = []
        max_players = _extract_max_players_change(text)
        if max_players is not None:
            changes.append({
                "key": "max-players",
                "value": str(max_players),
                "reason": f"用户要求最大人数改成 {max_players}",
            })

        pvp_value = _extract_pvp_change(text)
        if pvp_value is not None:
            changes.append({
                "key": "pvp",
                "value": "true" if pvp_value else "false",
                "reason": "用户要求开启 PVP" if pvp_value else "用户要求关闭 PVP",
            })

        online_mode = _extract_online_mode_change(text)
        if online_mode is not None:
            changes.append({
                "key": "online-mode",
                "value": "true" if online_mode else "false",
                "reason": "用户要求开启正版验证" if online_mode else "用户希望允许非正版玩家进入",
            })

        if not changes:
            return None

        proposal = self._config_edit.propose_change(
            relative_path="server.properties",
            changes=changes,
            user_request=text,
        )
        tool_result = json.dumps(proposal, ensure_ascii=False)
        return {
            "matched": True,
            "tool_name": "propose_config_change",
            "tool_result": tool_result,
            "config_proposal": proposal if proposal.get("status") == "proposal_created" else None,
            "assistant": _format_config_proposal_answer(proposal),
        }

    def _try_addon_diagnostics_local(self, text: str) -> dict | None:
        if self._addon_service is None or not _looks_like_addon_diagnostic_request(text):
            return None
        refresh_online = _looks_like_addon_online_refresh_request(text)
        report = self._addon_service.scan_addons(refresh_online=refresh_online)
        return {
            "matched": True,
            "tool_name": "scan_server_addons",
            "tool_result": json.dumps(report, ensure_ascii=False, default=str),
            "addon_report": report,
            "assistant": _format_addon_report_answer(report),
        }

    def _maybe_summarize(self, session_id: str) -> None:
        if (
            self._context is None
            or self._summary_repo is None
            or self._assistant is None
            or self._llm is None
        ):
            return
        try:
            if not self._context.needs_summary(session_id):
                return
            target_turn = self._context.summary_target_turn_index(session_id)
            if target_turn is None:
                return
            latest_summary = self._summary_repo.get_latest(session_id)
            covered_turn = (
                int(latest_summary["covered_through_turn_index"])
                if latest_summary
                else 0
            )
            covered_messages = self.chat_repository.list_messages_for_summary(
                session_id=session_id,
                after_turn_index=covered_turn,
                through_turn_index=target_turn,
            )
            if not covered_messages:
                return
            prompt = self._context.build_summary_prompt(
                session_id,
                through_turn_index=target_turn,
            )
            response = self._assistant.reply_without_tools(
                [
                    {"role": "system", "content": "你是对话摘要生成器，请用中文输出结构化摘要。"},
                    {"role": "user", "content": prompt},
                ],
                purpose="summary",
                session_id=session_id,
            )
            content = response.get("content") or ""
            if content:
                self._summary_repo.create(
                    session_id=session_id,
                    summary=content,
                    previous_summary_id=latest_summary["id"] if latest_summary else None,
                    covered_through_turn_index=target_turn,
                    covered_through_message_index=max(
                        int(message["message_index"])
                        for message in covered_messages
                        if int(message["turn_index"]) == target_turn
                    ),
                    source_llm_call_id=response.get("llm_call_id"),
                )
        except Exception:
            logger.exception("Failed to generate session summary for %s", session_id)

    def _record_interactive_tool_result(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        action_kind: str,
        result: dict,
        recent_server_logs: list[str] | None = None,
        current_online_players: dict | None = None,
    ) -> dict:
        payload = {
            "interactive_action": action_kind,
            "result": result,
            "recent_server_logs": recent_server_logs or [],
            "current_online_players": current_online_players,
        }
        message_id = self.chat_repository.add_message(
            session_id=session_id,
            turn_id=turn_id,
            role="tool",
            visibility="context_only",
            content_type="tool_result",
            content=json.dumps(payload, ensure_ascii=False, default=str),
            tool_name=tool_name,
        )
        recorded = dict(result)
        recorded["turn_id"] = turn_id
        recorded["tool_message_id"] = message_id
        recorded["recent_server_logs"] = recent_server_logs or []
        if current_online_players is not None:
            recorded["current_online_players"] = current_online_players
        return recorded

    def _complete_interactive_action(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        action_kind: str,
        result: dict,
        recent_server_logs: list[str] | None = None,
        current_online_players: dict | None = None,
    ) -> dict:
        assistant_text = self._generate_interactive_followup(
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            action_kind=action_kind,
            result=result,
            recent_server_logs=recent_server_logs or [],
            current_online_players=current_online_players,
        )
        assistant_id = self.chat_repository.add_message(
            session_id,
            "assistant",
            assistant_text,
            turn_id=turn_id,
        )
        self.chat_repository.update_turn(turn_id, status="completed", source="interactive", completed=True)
        self._maybe_summarize(session_id)
        completed = dict(result)
        completed["turn_id"] = turn_id
        completed["assistant"] = assistant_text
        completed["assistant_message_id"] = assistant_id
        return completed

    def _generate_interactive_followup(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        action_kind: str,
        result: dict,
        recent_server_logs: list[str],
        current_online_players: dict | None = None,
    ) -> str:
        fallback = _format_interactive_action_answer(tool_name, result)
        if not self.has_ai or self._assistant is None or self._context is None:
            return fallback

        prompt = (
            "用户刚刚在界面对一项高风险或需确认的运维操作作出了选择，"
            "受控本地服务已经完成处理，并把工具结果写入了历史工具结果。\n"
            "请只基于下面的执行结果向用户反馈最终状态；不要再次调用工具、"
            "不要声称执行了结果中没有体现的动作。\n"
            "若涉及玩家当前是否在线，只能以当前在线玩家快照为准；"
            "服务器日志末尾是历史事件记录，不表示玩家当前仍保持在线。\n\n"
            f"操作类型：{action_kind}\n"
            f"工具：{tool_name}\n"
            "执行结果："
            f"{json.dumps(result, ensure_ascii=False, default=str)}\n\n"
            "当前在线玩家快照（实时状态依据）：\n"
            f"{_format_current_online_players_for_prompt(current_online_players)}\n\n"
            "服务器日志末尾（历史事件，仅供执行回显和排障）：\n"
            f"{_format_recent_logs_for_prompt(recent_server_logs)}"
        )
        built_context = self._context.build_context(
            session_id=session_id,
            turn_id=turn_id,
            user_message=prompt,
            model=getattr(self._llm, "model", None),
            purpose="interactive_followup",
            include_current_turn_messages=True,
        )
        response = self._assistant.reply_without_tools(
            built_context.messages,
            purpose="interactive_followup",
            session_id=session_id,
            turn_id=turn_id,
            context_snapshot_id=built_context.snapshot_id,
        )
        content = (response.get("content") or "").strip()
        if response.get("error") or not content:
            return fallback
        return content

    def _ensure_requested_command_actions(
        self,
        user_message: str,
        actions: list[dict],
    ) -> list[dict]:
        expected_commands = _extract_command_requests(user_message)
        if not expected_commands or self._command_service is None:
            return actions

        arranged: list[dict] = []
        remaining = list(actions)
        for command in expected_commands:
            identity = _command_identity(command)
            matching_index = next(
                (
                    index
                    for index, action in enumerate(remaining)
                    if _command_identity(
                        action.get("normalized_command") or action.get("command", "")
                    ) == identity
                ),
                None,
            )
            if matching_index is not None:
                arranged.append(remaining.pop(matching_index))
                continue
            generated = self._command_service.submit_command(
                command,
                requested_by="ai_guardrail",
                user_confirmed=False,
            )
            generated["guardrail_generated"] = True
            arranged.append(generated)
        return arranged + remaining

    def _capture_server_log_cursor(self) -> int | None:
        capture_cursor = getattr(self.log_service, "capture_cursor", None)
        if not callable(capture_cursor):
            return None
        try:
            return int(capture_cursor())
        except Exception:
            logger.exception("Failed to establish server log cursor for interactive follow-up")
            return None

    def _operation_server_log_lines(self, cursor: int | None, limit: int = 8) -> list[str]:
        list_since = getattr(self.log_service, "list_since", None)
        if cursor is None or not callable(list_since):
            return []
        try:
            events = list_since(cursor, limit=limit)
        except Exception:
            logger.exception("Failed to load operation server logs for interactive follow-up")
            return []
        lines = [_format_recent_log_event(event) for event in events]
        return [line for line in lines if line]

    def _current_online_player_snapshot(self) -> dict:
        try:
            data = self.player_service.get_online_players()
        except Exception:
            logger.exception("Failed to load current online players for interactive follow-up")
            return {
                "available": False,
                "message": "实时在线玩家状态查询失败，不得根据历史日志推断当前在线状态。",
            }
        names = [
            str(player["name"])
            for player in data.get("players", [])
            if player.get("name")
        ]
        return {
            "available": True,
            "online_count": data.get("online_count", len(names)),
            "players": names,
            "captured_at": data.get("captured_at"),
            "server_state": data.get("server_state"),
        }


def _extract_max_players_change(text: str) -> int | None:
    if not any(token in text.lower() for token in ("max-players", "max players")) and not any(
        token in text for token in ("最大人数", "玩家上限", "最多玩家")
    ):
        return None
    numbers = re.findall(r"\d{1,3}", text)
    if not numbers:
        return None
    if len(numbers) == 1:
        return int(numbers[0])

    # 多个数字时，根据语义判断哪个是目标值
    # "从X改成Y" / "从X调到Y" → Y 是目标；"改成Z" / "设置为Z" → Z 是目标
    for n in numbers:
        # 查找数字前是否紧邻目标指示词（改成/改为/设置为/调到/调整到/变成/换成/为/成/到）
        pattern = re.compile(rf"((?:改[成为]?|设置[成为]?|调[整]?到|变[成为]?|换[成为]?|[为到成])\s*){{1,3}}{n}")
        if pattern.search(text):
            return int(n)

    # 如果目标指示词匹配失败，排除 "从X" 中的数字，取另一个
    from_match = re.search(rf"从\s*({numbers[0]})", text)
    if from_match and numbers[0] != numbers[1]:
        return int(numbers[1])
    if re.search(rf"从\s*({numbers[1]})", text):
        return int(numbers[0])

    return None


def _extract_pvp_change(text: str) -> bool | None:
    lowered = text.lower()
    compact = re.sub(r"\s+", "", lowered)
    if "pvp" not in compact and "玩家互" not in text and "互打" not in text:
        return None
    if any(token in text for token in ("关闭", "禁止", "不允许", "关掉", "别让")) or "off" in lowered:
        return False
    if any(token in text for token in ("开启", "打开", "允许", "启用")) or "on" in lowered:
        return True
    return None


def _extract_online_mode_change(text: str) -> bool | None:
    lowered = text.lower()
    mentions_online_mode = any(
        token in lowered
        for token in ("online-mode", "online mode")
    ) or any(
        token in text
        for token in ("正版验证", "离线模式", "盗版", "盗版登录")
    )
    if not mentions_online_mode:
        return None

    if any(token in text for token in ("开启正版", "打开正版", "启用正版", "只允许正版", "禁止盗版")):
        return True
    if any(token in text for token in ("关闭正版", "关掉正版", "离线模式", "允许盗版", "盗版玩家进不去", "盗版登录")):
        return False
    if "盗版" in text and any(token in text for token in ("进不去", "进不了", "无法进入", "不能进入", "登录不了", "帮我改", "改一下")):
        return False
    if "online-mode" in lowered:
        if any(token in lowered for token in ("false", "off", "disable")):
            return False
        if any(token in lowered for token in ("true", "on", "enable")):
            return True
    return None


def _looks_like_config_change_request(text: str) -> bool:
    lowered = text.lower()
    if _extract_max_players_change(text) is not None:
        return True
    if _extract_pvp_change(text) is not None:
        return True
    if _extract_online_mode_change(text) is not None:
        return True
    return any(
        token in lowered
        for token in (
            "server.properties",
            "max-players",
            "pvp",
            "online-mode",
            "enable-command-block",
            "view-distance",
            "simulation-distance",
            "server-port",
            "gamemode",
            "difficulty",
            "motd",
        )
    ) or any(
        token in text
        for token in (
            "配置",
            "正版验证",
            "离线模式",
            "盗版",
            "命令方块",
            "视距",
            "模拟距离",
            "端口",
            "难度",
            "默认模式",
            "白名单",
            "出生点保护",
            "允许飞行",
        )
    )


def _looks_like_addon_diagnostic_request(text: str) -> bool:
    lowered = text.lower()
    addon_tokens = (
        "mod",
        "mods",
        "plugin",
        "plugins",
        "插件",
        "模组",
        "客户端mod",
        "组件",
    )
    diagnostic_tokens = (
        "冲突",
        "检查",
        "检测",
        "诊断",
        "扫描",
        "不兼容",
        "依赖",
        "client-only",
        "client only",
        "客户端",
    )
    return (
        any(token in lowered for token in addon_tokens)
        or any(token in text for token in addon_tokens)
    ) and (
        any(token in lowered for token in diagnostic_tokens)
        or any(token in text for token in diagnostic_tokens)
    )


def _looks_like_addon_online_refresh_request(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in text
        for token in ("联网", "网页", "刷新元数据", "百科", "在线查询")
    ) or any(
        token in lowered
        for token in ("online", "modrinth", "curseforge", "refresh metadata", "wiki")
    )


def _looks_like_autonomous_config_loop_request(text: str) -> bool:
    lowered = text.lower()
    has_loop_intent = any(
        token in text
        for token in ("自主循环", "直到达成", "直到完成", "如果没生效", "继续调整", "自动帮我改到")
    ) or any(
        token in lowered
        for token in ("autonomous", "until it works", "keep adjusting")
    ) or ("直到" in text and any(token in text for token in ("生效", "确认", "可用", "能用")))
    if not has_loop_intent:
        return False
    return _looks_like_config_change_request(text) or any(
        token in text
        for token in ("卡", "延迟", "性能", "进不去", "进不了", "登录不了")
    )


def _looks_like_online_player_query(text: str) -> bool:
    lowered = text.lower()
    if "who" in lowered:
        return True
    return any(
        token in text
        for token in ("在线", "谁在", "哪些玩家", "玩家列表", "当前玩家")
    )


def _looks_like_command_request(text: str) -> bool:
    return bool(_extract_command_requests(text))


def _looks_like_restart_request(text: str) -> bool:
    lowered = text.lower()
    compact = re.sub(r"\s+", "", lowered)
    has_restart = any(
        token in compact
        for token in (
            "重启",
            "重新启动",
            "重开",
            "restart",
            "reboot",
        )
    )
    if not has_restart:
        return False
    return (
        compact in {"重启", "重启一下", "restart", "reboot"}
        or any(token in compact for token in ("服务器", "server", "minecraft", "mc", "开服", "服"))
    )


def _looks_like_start_request(text: str) -> bool:
    lowered = text.lower()
    compact = re.sub(r"\s+", "", lowered)
    if _looks_like_restart_request(text):
        return False
    has_start = any(
        token in compact
        for token in (
            "启动",
            "开服",
            "开启服务器",
            "startserver",
            "startminecraft",
            "startmc",
        )
    ) or compact in {"start", "启动一下", "开一下"}
    if not has_start:
        return False
    return any(
        token in compact
        for token in ("服务器", "server", "minecraft", "mc", "开服", "服")
    )


def _looks_like_java_environment_request(text: str) -> bool:
    lowered = text.lower()
    mentions_java = any(
        token in lowered
        for token in ("java", "jdk", "jre")
    )
    if not mentions_java:
        return False
    return any(
        token in text
        for token in ("检查", "检测", "安装", "修复", "配置", "切换", "环境", "版本")
    ) or any(
        token in lowered
        for token in ("check", "detect", "install", "ensure", "fix", "setup", "version")
    )


def _looks_like_java_environment_fix_request(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in text
        for token in ("安装", "修复", "配置", "切换", "准备", "自动")
    ) or any(token in lowered for token in ("install", "ensure", "fix", "setup"))


def _extract_minecraft_version(text: str) -> str | None:
    match = re.search(r"(?<!\d)(1\.\d+(?:\.\d+)?)(?!\d)", text)
    return match.group(1) if match else None


def _extract_command_request(text: str) -> str | None:
    commands = _extract_command_requests(text)
    return commands[0] if commands else None


def _extract_command_requests(text: str) -> list[str]:
    stripped = text.strip()

    explicit = re.search(
        r"(?:执行|发送|运行|提交)(?:服务器|控制台|mc|minecraft)?命令[：:\s]+/?(.+)$",
        stripped,
        flags=re.IGNORECASE,
    )
    if explicit:
        explicit_text = explicit.group(1).strip()
        parsed_explicit = _extract_command_requests(explicit_text)
        return parsed_explicit or [explicit_text]

    direct = stripped[1:] if stripped.startswith("/") else stripped
    first = direct.split(maxsplit=1)[0].lower() if direct.split() else ""
    known_commands = {
        "list",
        "say",
        "time",
        "weather",
        "tp",
        "give",
        "gamemode",
        "difficulty",
        "kick",
        "stop",
        "op",
        "deop",
        "ban",
        "pardon",
        "whitelist",
        "save-off",
        "save-on",
    }
    if first in known_commands and not any(
        token in direct for token in ("然后", "并且", "同时", "之后", "再")
    ):
        return [direct.strip()]

    candidates: list[tuple[int, int, str]] = []

    def append_match(match: re.Match, command: str) -> None:
        if any(match.start() < end and match.end() > start for start, end, _ in candidates):
            return
        candidates.append((match.start(), match.end(), command))

    deop_pattern = re.compile(
        r"(?:"
        r"(?:取消|移除|撤销|删除)\s*(?:玩家\s*)?(?P<before>[A-Za-z0-9_]{3,16})\s*(?:的)?\s*(?:管理员|op|操作员)(?:权限)?"
        r"|(?:把|将)?\s*(?:玩家\s*)?(?P<after>[A-Za-z0-9_]{3,16})\s*(?:的)?\s*(?:管理员|op|操作员)(?:权限)?\s*(?:取消|移除|撤销|删除)"
        r")",
        flags=re.IGNORECASE,
    )
    for match in deop_pattern.finditer(stripped):
        name = match.group("before") or match.group("after")
        append_match(match, f"deop {name}")

    op_pattern = re.compile(
        r"(?:把|将|让)?\s*(?:玩家\s*)?(?P<name>[A-Za-z0-9_]{3,16})\s*"
        r"(?:设为|设置为|设成|变成|加为|成为)\s*(?:管理员|op|操作员)",
        flags=re.IGNORECASE,
    )
    for match in op_pattern.finditer(stripped):
        append_match(match, f"op {match.group('name')}")

    for command_name, pattern in (
        ("ban", r"(?:封禁|ban)\s*(?P<name>[A-Za-z0-9_]{3,16})"),
        ("kick", r"(?:踢出|kick)\s*(?P<name>[A-Za-z0-9_]{3,16})"),
    ):
        for match in re.finditer(pattern, stripped, flags=re.IGNORECASE):
            append_match(match, f"{command_name} {match.group('name')}")

    shutdown_pattern = re.compile(
        r"(?:关闭|停止|关掉|停掉|关停)\s*(?:一下)?\s*(?:这个)?\s*(?:mc|minecraft)?\s*服务器"
        r"|(?:把|将)\s*(?:这个)?\s*(?:mc|minecraft)?\s*服务器\s*(?:关闭|停止|关掉|停掉|关停)(?:一下)?"
        r"|关服|停服",
        flags=re.IGNORECASE,
    )
    for match in shutdown_pattern.finditer(stripped):
        append_match(match, "stop")

    if candidates:
        return [command for _start, _end, command in sorted(candidates)]

    if "在线玩家" in stripped and any(token in stripped for token in ("列出", "查看", "查询")):
        return ["list"]

    return []


def _command_local_result(results: list[dict]) -> dict:
    tool_result = json.dumps(results, ensure_ascii=False)
    return {
        "matched": True,
        "tool_name": "execute_server_command",
        "tool_result": tool_result,
        "command_action": results[0] if results else None,
        "command_actions": results,
        "assistant": _format_command_actions_answer(results),
    }


def _with_turn_id(payload: dict, turn_id: str) -> dict:
    updated = dict(payload)
    updated["turn_id"] = turn_id
    return updated


def _format_interactive_action_answer(tool_name: str, result: dict) -> str:
    status = result.get("status", "")
    message = result.get("message") or result.get("error_message") or ""
    if tool_name == "restart_server" or _is_server_restart_result(result):
        if status == "executed":
            start = result.get("start") or {}
            pid = start.get("pid")
            pid_text = f"，PID：{pid}" if pid else ""
            return f"服务器重启流程已执行，启动状态：{start.get('state', 'unknown')}{pid_text}。"
        if status == "cancelled":
            return "已取消重启服务器，未停止或启动服务器。"
        return f"重启服务器处理结果：{message or status}"

    if tool_name == "execute_server_command":
        command = result.get("command") or result.get("normalized_command") or ""
        if status == "executed":
            output = result.get("output")
            suffix = f"\n输出：{output}" if output else ""
            return f"命令 `{command}` 已发送到 Minecraft 控制台。{suffix}"
        return f"命令 `{command}` 处理结果：{message or status}"

    if tool_name in {"apply_config_proposal", "reject_config_proposal"}:
        proposal_id = result.get("proposal_id", "")
        return f"配置草案 `{proposal_id}` 处理结果：{message or status}"

    return message or status or "操作已处理。"


def _format_config_proposal_answer(proposal: dict) -> str:
    status = proposal.get("status")
    if status == "saved":
        return proposal.get("message") or "配置已保存。"
    if status != "proposal_created":
        return proposal.get("message") or "配置修改草案生成失败。"

    lines = [
        "已生成配置修改草案，等待你在配置编辑器中采纳或拒绝后才会写入文件。",
        f"文件：{proposal.get('relative_path', 'server.properties')}",
        f"风险：{proposal.get('risk_level', 'LOW')}",
        f"需要重启：{'是' if proposal.get('restart_required') else '否'}",
        "",
        "变更：",
    ]
    for change in proposal.get("changes", []):
        lines.append(
            f"- {change.get('key')}: {change.get('old_value')} -> {change.get('new_value')}"
        )
    if proposal.get("warnings"):
        lines.append("")
        lines.append("注意：")
        for warning in proposal["warnings"]:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _format_addon_report_answer(report: dict) -> str:
    status = report.get("status")
    if status == "failed":
        return report.get("message") or report.get("error_message") or "组件诊断扫描失败。"
    if status == "no_scan":
        return "尚未执行组件诊断扫描。"

    summary = report.get("summary") or {}
    assets = report.get("assets") or []
    diagnostics = report.get("diagnostics") or []
    lines = [
        report.get("message") or f"已扫描 {len(assets)} 个组件。",
        (
            "严重度："
            f"BLOCKER {summary.get('blockers', 0)} / "
            f"HIGH {summary.get('high', 0)} / "
            f"MEDIUM {summary.get('medium', 0)} / "
            f"LOW {summary.get('low', 0)}"
        ),
        "注意：模型知识不会作为冲突判定依据；每条诊断都带 evidence_type。",
    ]
    if diagnostics:
        lines.append("")
        lines.append("优先处理：")
        for diagnostic in diagnostics[:5]:
            files = ", ".join(diagnostic.get("affected_files") or [])
            file_text = f"（{files}）" if files else ""
            lines.append(
                f"- [{diagnostic.get('severity')}] {diagnostic.get('message')} "
                f"证据：{diagnostic.get('evidence_type')}{file_text}"
            )
    else:
        lines.append("未发现硬证据冲突；如仍无法启动，可进行一次确认后的启动日志验证。")
    return "\n".join(lines)


def _format_java_environment_answer(result: dict | None) -> str:
    if not result:
        return "Java 环境检查没有返回可用结果。"
    status = result.get("status")
    message = result.get("message") or ""
    minecraft_version = result.get("minecraft_version") or "未知"
    required = result.get("required_java_major")
    selected = result.get("selected_java") or {}
    java_path = selected.get("java_path")

    if status == "needs_input":
        return (
            "我还不能确定这个服务端需要哪个 Java 版本。\n"
            "请告诉我 Minecraft 服务端版本，例如 `1.20.1`，我会继续检查并自动选择或安装匹配的 Java。"
        )

    header = (
        f"Minecraft 版本：{minecraft_version}\n"
        f"需要 Java：{required or '未知'}"
    )
    if status == "ok":
        return (
            f"{header}\n"
            f"当前 Java 已匹配：`{java_path}`。"
        )
    if status == "selected":
        return (
            f"{header}\n"
            f"已切换到现有匹配 Java：`{java_path}`。"
        )
    if status == "installed":
        return (
            f"{header}\n"
            f"已下载安装到项目内便携 Java，并切换到：`{java_path}`。"
        )
    if status == "failed":
        return (
            f"{header}\n"
            f"Java 环境修复失败：{result.get('error_message') or message or '未知错误'}"
        )
    return message or f"Java 环境状态：{status}。"


def _format_server_start_with_java_answer(result: dict) -> str:
    java_text = _format_java_environment_answer(result.get("java_environment"))
    server = result.get("server") or {}
    state = server.get("state") or server.get("status") or "unknown"
    pid = server.get("pid")
    pid_text = f"，PID：{pid}" if pid else ""
    return f"{java_text}\n\n服务器启动请求已提交，状态：{state}{pid_text}。"


def _format_autonomous_task_answer(result: dict) -> str:
    status = result.get("status", "unknown")
    task = result.get("task") or {}
    proposal = result.get("current_proposal") or {}
    goal = task.get("user_goal", "")
    round_text = ""
    if task:
        round_text = f"第 {task.get('current_round', 0)}/{task.get('max_rounds', 0)} 轮"

    if status == "awaiting_user_confirmation" and proposal:
        changes = _format_config_changes(proposal.get("changes", []))
        return (
            "自主配置任务已暂停，等待确认。\n"
            f"目标：{goal}\n"
            f"进度：{round_text}\n"
            f"草案：{changes}\n"
            f"风险：{proposal.get('risk_level', 'UNKNOWN')}\n"
            f"需要重启：{'是' if proposal.get('restart_required') else '否'}\n"
            "请在任务卡片中确认、拒绝或取消。"
        )
    if status == "completed":
        return result.get("message") or task.get("final_summary") or "自主配置任务已完成。"
    if status == "cancelled":
        return result.get("message") or task.get("stopped_reason") or "自主配置任务已取消。"
    if status in {"failed", "max_rounds_reached", "needs_user_input"}:
        return result.get("message") or task.get("stopped_reason") or "自主配置任务已停止。"
    if status == "disabled":
        return result.get("message") or "自主配置循环未启用。"
    return result.get("message") or f"自主配置任务状态：{status}。"


def _format_config_changes(changes: list[dict]) -> str:
    parts = []
    for change in changes:
        key = change.get("key", "?")
        old = change.get("old_value", "?")
        new = change.get("new_value", "?")
        parts.append(f"{key}: {old} -> {new}")
    return "；".join(parts) if parts else "无配置变化"


def _extract_config_proposal(tool_results: list[dict]) -> dict | None:
    for item in reversed(tool_results):
        if item.get("tool_name") != "propose_config_change":
            continue
        raw_result = item.get("result", "")
        try:
            result = json.loads(raw_result)
        except (TypeError, json.JSONDecodeError):
            continue
        if result.get("status") == "proposal_created":
            return result
    return None


def _extract_command_actions(tool_results: list[dict]) -> list[dict]:
    actions: list[dict] = []
    for item in tool_results:
        if item.get("tool_name") not in {
            "execute_server_command",
            "propose_server_command",
            "restart_server",
        }:
            continue
        raw_result = item.get("result", "")
        try:
            result = json.loads(raw_result)
        except (TypeError, json.JSONDecodeError):
            continue
        if result.get("status") in {
            "confirmation_required",
            "blocked",
            "executed",
            "failed",
            "cancelled",
        }:
            result["source_tool"] = item.get("tool_name")
            actions.append(result)
    return actions


def _extract_command_action(tool_results: list[dict]) -> dict | None:
    actions = _extract_command_actions(tool_results)
    return actions[-1] if actions else None


def _extract_server_action(tool_results: list[dict]) -> dict | None:
    for item in reversed(tool_results):
        if item.get("tool_name") not in {"start_server", "ensure_java_environment"}:
            continue
        raw_result = item.get("result", "")
        try:
            result = json.loads(raw_result)
        except (TypeError, json.JSONDecodeError):
            continue
        server = result.get("server")
        if not isinstance(server, dict):
            continue
        state = server.get("state") or server.get("status")
        if state not in {"starting", "running"}:
            continue
        return {
            "action_type": "server_start",
            "source_tool": item.get("tool_name"),
            "server": server,
            "message": result.get("message") or server.get("message") or "",
        }
    return None


def _extract_java_environment(tool_results: list[dict]) -> dict | None:
    for item in reversed(tool_results):
        if item.get("tool_name") not in {
            "check_java_environment",
            "ensure_java_environment",
            "start_server",
        }:
            continue
        raw_result = item.get("result", "")
        try:
            result = json.loads(raw_result)
        except (TypeError, json.JSONDecodeError):
            continue
        if item.get("tool_name") == "start_server":
            result = result.get("java_environment")
        if isinstance(result, dict) and result.get("status") in {
            "ok",
            "selected",
            "installed",
            "needs_input",
            "failed",
        }:
            return result
    return None


def _server_start_action_from_result(result: dict) -> dict | None:
    server = result.get("server")
    if not isinstance(server, dict):
        return None
    state = server.get("state") or server.get("status")
    if state not in {"starting", "running"}:
        return None
    return {
        "action_type": "server_start",
        "source_tool": "start_server",
        "server": server,
        "message": result.get("message") or server.get("message") or "",
    }


def _format_recent_log_event(event: dict) -> str:
    raw_line = str(event.get("raw_line") or "").strip()
    if raw_line:
        return raw_line
    time_text = event.get("event_time") or event.get("created_at") or "?"
    level = event.get("level") or "INFO"
    message = event.get("message") or ""
    return f"[{time_text}] [{level}] {message}".strip()


def _format_recent_logs_for_prompt(lines: list[str]) -> str:
    if not lines:
        return "（未获取到最近服务器日志）"
    return "\n".join(f"- {line}" for line in lines[-8:])


def _format_current_online_players_for_prompt(snapshot: dict | None) -> str:
    if snapshot is None:
        return "（未获取实时在线玩家快照；不得根据历史日志推断当前在线状态）"
    return json.dumps(snapshot, ensure_ascii=False, default=str)


def _format_command_action_answer(action: dict) -> str:
    command = action.get("command") or action.get("normalized_command") or ""
    risk_level = action.get("risk_level", "UNKNOWN")
    status = action.get("status")
    message = action.get("message") or ""

    if _is_server_restart_result(action) or _is_server_restart_action(command):
        if status == "executed":
            start = action.get("start") or {}
            pid = start.get("pid")
            pid_text = f"，PID：{pid}" if pid else ""
            return (
                "服务器已完成停止流程，并已通过 `start_server` 提交启动请求。\n"
                f"启动状态：{start.get('state', 'unknown')}{pid_text}"
            )

        if status == "confirmation_required":
            return (
                "重启服务器是高风险运维操作，尚未执行。\n"
                f"风险等级：{risk_level}\n"
                "确认后将先停止当前 Minecraft 服务器，待停止完成后调用 `start_server` 启动。"
            )

        if status == "cancelled":
            return "已取消重启服务器，未停止或启动服务器。"

        return (
            "重启服务器未完成。\n"
            f"原因：{message or action.get('error_message') or '未知错误'}"
        )

    if status == "executed":
        output = action.get("output")
        suffix = f"\n输出：{output}" if output else ""
        return f"已通过受控命令通道发送：`{command}`。\n风险等级：{risk_level}{suffix}"

    if status == "confirmation_required":
        return (
            f"`{command}` 是高风险 Minecraft 命令，尚未发送到服务器。\n"
            f"风险等级：{risk_level}\n"
            "请在对话内确认卡片中点击执行。"
        )

    if status == "blocked":
        return (
            f"命令 `{command}` 已被安全策略拦截，未发送到服务器。\n"
            f"原因：{message}"
        )

    return (
        f"命令 `{command}` 未发送成功。\n"
        f"原因：{message or action.get('error_message') or '未知错误'}"
    )


def _message_metadata(prompt_parts: list[dict] | None) -> dict | None:
    if not prompt_parts:
        return None
    sanitized: list[dict] = []
    for part in prompt_parts:
        if part.get("kind") == "text":
            sanitized.append({"kind": "text", "text": str(part.get("text") or "")})
            continue
        if part.get("kind") != "attachment":
            continue
        attachment = part.get("attachment") or {}
        sanitized.append({
            "kind": "attachment",
            "attachment": {
                "attachment_id": attachment.get("attachment_id") or attachment.get("id"),
                "label": attachment.get("label") or attachment.get("source") or "日志片段",
                "line_count": attachment.get("line_count"),
                "time_range": attachment.get("time_range"),
            },
        })
    return {"prompt_parts": sanitized} if sanitized else None


def _stored_attachment_display(attachment: dict) -> dict:
    metadata: dict = {}
    raw_metadata = attachment.get("metadata_json")
    if raw_metadata:
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            metadata = {}
    return {
        "attachment_id": attachment.get("id"),
        "label": attachment.get("label") or metadata.get("source") or "日志片段",
        "line_count": metadata.get("line_count"),
        "time_range": metadata.get("time_range"),
    }


def _format_command_actions_answer(actions: list[dict]) -> str:
    return "\n\n".join(_format_command_action_answer(action) for action in actions)


def _command_identity(command: str) -> str:
    return re.sub(r"\s+", " ", str(command).strip().lstrip("/").lower())


def _server_restart_confirmation_action() -> dict:
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


def _server_restart_result(result: dict) -> dict:
    normalized = dict(result)
    normalized["operation"] = "restart_server"
    normalized["action_type"] = "server_restart"
    normalized["command"] = "restart_server"
    normalized["normalized_command"] = "restart_server"
    normalized["display_name"] = "重启服务器"
    normalized["risk_level"] = "HIGH"
    normalized["confirmation_required"] = True
    normalized.setdefault("audit_id", None)
    return normalized


def _is_server_restart_action(command: str | None) -> bool:
    return _command_identity(command or "") == "restart_server"


def _is_server_restart_result(value: dict) -> bool:
    return (
        value.get("action_type") == "server_restart"
        or value.get("operation") == "restart_server"
        or _is_server_restart_action(value.get("command") or value.get("normalized_command"))
    )
