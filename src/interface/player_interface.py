from __future__ import annotations

from src.service.player_service import PlayerService


class PlayerInterface:
    def __init__(self, player_service: PlayerService):
        self.player_service = player_service

    def get_current_players(self) -> dict:
        return self.player_service.get_online_players()

    def get_player_directory(self) -> dict:
        return self.player_service.get_player_directory()

    def refresh_player_directory(self) -> dict:
        return self.player_service.refresh_player_directory(force=True)
