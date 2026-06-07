from __future__ import annotations

from pathlib import Path

import pytest

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.chat_repository import ChatRepository
from src.repositories.player_ai_repository import PlayerAiRepository
from src.service.player_ai_policy_service import PlayerAiPolicyService


@pytest.fixture()
def repositories(tmp_path: Path):
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    try:
        yield PlayerAiRepository(connection), ChatRepository(connection), connection
    finally:
        connection.close()


def test_player_ai_repository_defaults_and_deduplicates_entries(repositories) -> None:
    repository, _chat_repository, connection = repositories

    assert repository.get_settings() == {
        "enabled": True,
        "audience": "all",
        "list_mode": "blocklist",
        "access_entries": [],
        "updated_at": connection.execute(
            "SELECT updated_at FROM player_ai_settings WHERE id = 1"
        ).fetchone()["updated_at"],
    }

    saved = repository.save_settings(
        enabled=True,
        audience="operators",
        list_mode="allowlist",
        access_entries=[
            {"display_name": "Steve"},
            {"display_name": "steve", "player_uuid": "uuid-1"},
            {"display_name": "Alex"},
        ],
    )

    assert saved["audience"] == "operators"
    assert saved["list_mode"] == "allowlist"
    assert [
        (entry["player_key"], entry["display_name"], entry["player_uuid"])
        for entry in saved["access_entries"]
    ] == [
        ("alex", "Alex", None),
        ("steve", "steve", "uuid-1"),
    ]


def test_player_ai_repository_persists_player_session_mapping(repositories) -> None:
    repository, chat_repository, _connection = repositories
    session_id = chat_repository.create_session("游戏内 AI · Steve")

    repository.save_conversation(
        player_name="Steve",
        player_uuid="uuid-1",
        session_id=session_id,
    )

    assert repository.get_conversation("steve") == {
        "player_key": "steve",
        "display_name": "Steve",
        "player_uuid": "uuid-1",
        "session_id": session_id,
        "last_active_at": repository.get_conversation("Steve")["last_active_at"],
    }


def test_player_ai_policy_validates_names_and_lists_known_players(repositories) -> None:
    repository, _chat_repository, _connection = repositories
    policy = PlayerAiPolicyService(
        repository,
        is_operator=lambda name: name.lower() == "admin",
        list_known_players=lambda: {
            "players": [
                {
                    "name": "Admin",
                    "uuid": "uuid-admin",
                    "is_operator": True,
                    "is_online": True,
                }
            ]
        },
    )

    with pytest.raises(ValueError, match="玩家名无效"):
        policy.save_settings({
            "enabled": True,
            "audience": "all",
            "list_mode": "allowlist",
            "access_entries": [{"display_name": "x"}],
        })

    assert policy.list_known_players() == [
        {
            "name": "Admin",
            "uuid": "uuid-admin",
            "is_operator": True,
            "is_online": True,
        }
    ]


def test_player_ai_policy_uses_group_first_and_lists_as_exceptions(
    repositories,
) -> None:
    repository, _chat_repository, _connection = repositories
    policy = PlayerAiPolicyService(
        repository,
        is_operator=lambda name: name.lower() == "admin",
    )

    policy.save_settings({
        "enabled": True,
        "audience": "operators",
        "list_mode": "allowlist",
        "access_entries": [{"display_name": "Steve"}],
    })
    assert policy.is_allowed("Admin") is True
    assert policy.is_allowed("Steve") is True
    assert policy.is_allowed("Alex") is False

    policy.save_settings({
        "enabled": True,
        "audience": "operators",
        "list_mode": "blocklist",
        "access_entries": [{"display_name": "Admin"}],
    })
    assert policy.is_allowed("Admin") is False
    assert policy.is_allowed("Steve") is False

    policy.save_settings({
        "enabled": True,
        "audience": "all",
        "list_mode": "allowlist",
        "access_entries": [],
    })
    assert policy.is_allowed("Alex") is True
