from __future__ import annotations

from src.service.player_ai_chat_service import PlayerAiChatService
from src.service.player_ai_policy_service import PlayerAiPolicyService


class PlayerAiChatInterface:
    def __init__(
        self,
        service: PlayerAiChatService,
        policy_service: PlayerAiPolicyService,
    ):
        self._service = service
        self._policy = policy_service

    @property
    def is_running(self) -> bool:
        return self._service.is_running

    def start(self) -> bool:
        return self._service.start()

    def stop(self) -> None:
        self._service.stop()

    def get_settings(self) -> dict:
        return self._policy.get_settings().to_dict()

    def save_settings(self, payload: dict) -> dict:
        settings = self._policy.save_settings(payload)
        if settings.enabled:
            self._service.start()
        else:
            self._service.stop()
        return settings.to_dict()

    def list_known_players(self) -> list[dict]:
        return self._policy.list_known_players()
