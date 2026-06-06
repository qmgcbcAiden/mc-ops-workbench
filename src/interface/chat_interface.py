from __future__ import annotations

from typing import Iterator

from src.ai.stream_events import ChatStreamEvent
from src.service.chat_service import ChatService


class ChatInterface:
    def __init__(self, chat_service: ChatService):
        self.chat_service = chat_service

    def create_session(self, title: str | None = "server_ops") -> str:
        return self.chat_service.create_session(title)

    def list_sessions(self, limit: int = 20) -> list[dict]:
        return self.chat_service.list_sessions(limit=limit)

    def get_session_view(self, session_id: str, limit_turns: int = 50) -> dict:
        return self.chat_service.get_session_view(
            session_id=session_id,
            limit_turns=limit_turns,
        )

    def send_turn(
        self,
        session_id: str,
        text: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> dict:
        return self.chat_service.send_turn(
            session_id=session_id,
            user_message=text,
            attachment_ids=attachment_ids,
            prompt_parts=prompt_parts,
        )

    def send_message(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> dict:
        return self.chat_service.send_message(
            session_id=session_id,
            user_message=user_message,
            attachment_ids=attachment_ids,
            prompt_parts=prompt_parts,
        )

    def stream_turn(
        self,
        session_id: str,
        text: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> Iterator[ChatStreamEvent]:
        return self.chat_service.stream_turn(
            session_id=session_id,
            user_message=text,
            attachment_ids=attachment_ids,
            prompt_parts=prompt_parts,
        )

    def stream_message(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None = None,
        prompt_parts: list[dict] | None = None,
    ) -> Iterator[ChatStreamEvent]:
        return self.chat_service.stream_message(
            session_id=session_id,
            user_message=user_message,
            attachment_ids=attachment_ids,
            prompt_parts=prompt_parts,
        )

    def list_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        return self.chat_service.list_messages(session_id=session_id, limit=limit)

    def attach_log_selection(self, session_id: str, selection: dict) -> dict:
        return self.chat_service.attach_log_selection(
            session_id=session_id,
            selection=selection,
        )

    def confirm_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.confirm_command_action(
            session_id=session_id,
            command=command,
            audit_id=audit_id,
            turn_id=turn_id,
        )

    def execute_confirmed_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.execute_confirmed_command_action(
            session_id=session_id,
            command=command,
            audit_id=audit_id,
            turn_id=turn_id,
        )

    def cancel_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.cancel_command_action(
            session_id=session_id,
            command=command,
            audit_id=audit_id,
            turn_id=turn_id,
        )

    def record_cancelled_command_action(
        self,
        session_id: str,
        command: str,
        audit_id: int | None = None,
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.record_cancelled_command_action(
            session_id=session_id,
            command=command,
            audit_id=audit_id,
            turn_id=turn_id,
        )

    def complete_command_action_feedback(
        self,
        session_id: str,
        recorded_result: dict,
        action_kind: str = "command_confirmation",
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.complete_command_action_feedback(
            session_id=session_id,
            recorded_result=recorded_result,
            action_kind=action_kind,
            turn_id=turn_id,
        )

    def apply_config_proposal(
        self,
        session_id: str,
        proposal_id: str,
        high_risk_confirmed: bool = False,
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.apply_config_proposal(
            session_id=session_id,
            proposal_id=proposal_id,
            high_risk_confirmed=high_risk_confirmed,
            turn_id=turn_id,
        )

    def execute_config_proposal_action(
        self,
        session_id: str,
        proposal_id: str,
        high_risk_confirmed: bool = False,
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.execute_config_proposal_action(
            session_id=session_id,
            proposal_id=proposal_id,
            high_risk_confirmed=high_risk_confirmed,
            turn_id=turn_id,
        )

    def reject_config_proposal(
        self,
        session_id: str,
        proposal_id: str,
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.reject_config_proposal(
            session_id=session_id,
            proposal_id=proposal_id,
            turn_id=turn_id,
        )

    def record_rejected_config_proposal_action(
        self,
        session_id: str,
        proposal_id: str,
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.record_rejected_config_proposal_action(
            session_id=session_id,
            proposal_id=proposal_id,
            turn_id=turn_id,
        )

    def complete_config_action_feedback(
        self,
        session_id: str,
        recorded_result: dict,
        action_kind: str = "config_apply",
        turn_id: str | None = None,
    ) -> dict:
        return self.chat_service.complete_config_action_feedback(
            session_id=session_id,
            recorded_result=recorded_result,
            action_kind=action_kind,
            turn_id=turn_id,
        )

    def create_autonomous_config_task(self, session_id: str, user_goal: str) -> dict:
        return self.chat_service.create_autonomous_config_task(
            session_id=session_id,
            user_goal=user_goal,
        )

    def continue_autonomous_task(
        self,
        task_id: str,
        proposal_id: str,
        approved: bool,
    ) -> dict:
        return self.chat_service.continue_autonomous_task(
            task_id=task_id,
            proposal_id=proposal_id,
            approved=approved,
        )

    def cancel_autonomous_task(self, task_id: str) -> dict:
        return self.chat_service.cancel_autonomous_task(task_id=task_id)

    def get_autonomous_task_status(self, task_id: str) -> dict:
        return self.chat_service.get_autonomous_task_status(task_id=task_id)

    def list_ai_models(self) -> list[dict]:
        return self.chat_service.list_ai_models()

    def get_selected_ai_model(self) -> dict:
        return self.chat_service.get_selected_ai_model()

    def select_ai_model(self, model_id: str) -> dict:
        return self.chat_service.select_ai_model(model_id)

    @property
    def has_ai(self) -> bool:
        return self.chat_service.has_ai
