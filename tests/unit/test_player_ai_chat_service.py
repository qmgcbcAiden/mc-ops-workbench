from __future__ import annotations

import time
from pathlib import Path

from src.ai.llm_client import LlmRequestOptions, LlmResponse
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.mc.command_policy import LOW, classify_command
from src.repositories.chat_repository import ChatRepository
from src.repositories.llm_repository import LlmRepository
from src.repositories.player_ai_repository import PlayerAiRepository
from src.service.player_ai_chat_service import (
    PLAYER_AI_CHUNK_MAX_CHARS,
    PLAYER_AI_MAX_CHUNKS,
    PlayerAiChatService,
    _player_ai_request_options,
    detect_player_ai_request,
    format_player_ai_reply,
    sanitize_player_ai_reply,
)
from src.service.player_ai_policy_service import PlayerAiPolicyService


class FakeLlm:
    model = "fake-player-ai"
    provider = "qwen"

    def __init__(self, content: str = "你好，冒险顺利。") -> None:
        self.content = content
        self.calls: list[dict] = []

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        request_options: LlmRequestOptions | None = None,
    ) -> LlmResponse:
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "request_options": request_options,
        })
        return LlmResponse(content=self.content, model=self.model)


class FakeCommandService:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    def submit_command(
        self,
        command: str,
        requested_by: str = "ui",
        user_confirmed: bool = False,
    ) -> dict:
        self.commands.append({
            "command": command,
            "requested_by": requested_by,
            "user_confirmed": user_confirmed,
        })
        return {
            "status": "executed",
            "command": command,
            "risk_level": "LOW",
            "confirmation_required": False,
        }


def test_detect_player_ai_request_parses_standard_chat_lines() -> None:
    request = detect_player_ai_request("[12:00:00 INFO]: <Steve> @AI hello")
    lower_request = detect_player_ai_request("<Alex_12> @ai 你好")
    insecure_request = detect_player_ai_request(
        "[076月2026 15:42:52.422] [Server thread/INFO] "
        "[net.minecraft.server.MinecraftServer/]: "
        "[Not Secure] <Aiden233> @AI 你好我叫阿鬼"
    )

    assert request is not None
    assert request.player_name == "Steve"
    assert request.prompt == "hello"
    assert lower_request is not None
    assert lower_request.player_name == "Alex_12"
    assert lower_request.prompt == "你好"
    assert insecure_request is not None
    assert insecure_request.player_name == "Aiden233"
    assert insecure_request.prompt == "你好我叫阿鬼"


def test_detect_player_ai_request_ignores_non_player_or_empty_lines() -> None:
    assert detect_player_ai_request("[Server] @Steve hello") is None
    assert detect_player_ai_request("[12:00:00 INFO]: <Steve> hello") is None
    assert detect_player_ai_request("[12:00:00 INFO]: <St> @AI hello") is None
    assert detect_player_ai_request("[12:00:00 INFO]: <Steve> @AI  ") is None


def test_sanitize_player_ai_reply_removes_markdown_emoji_and_blocked_tokens() -> None:
    cleaned = sanitize_player_ai_reply(
        "**你好** 😀\n`cmd` > echo ../secret | python test && ok"
    )

    assert cleaned == "你好 secret test ok"


def test_format_player_ai_reply_caps_to_three_say_chunks() -> None:
    chunks = format_player_ai_reply("Steve", "一" * 260)

    assert 1 <= len(chunks) <= PLAYER_AI_MAX_CHUNKS
    assert all(chunk.startswith("@Steve ") for chunk in chunks)
    assert all(len(chunk.removeprefix("@Steve ")) <= PLAYER_AI_CHUNK_MAX_CHARS for chunk in chunks)
    assert chunks[-1].endswith("...")


def test_say_player_reply_is_low_risk_command() -> None:
    risk = classify_command("say @Steve hello")

    assert risk.risk_level == LOW
    assert risk.confirmation_required is False


def test_player_ai_request_options_only_disable_thinking_for_qwen() -> None:
    qwen = FakeLlm()
    deepseek = FakeLlm()
    deepseek.provider = "deepseek"

    assert _player_ai_request_options(qwen).extra_body == {
        "enable_thinking": False
    }
    assert _player_ai_request_options(deepseek).extra_body is None
    assert _player_ai_request_options(deepseek).include_finish_notice is False


def test_player_ai_service_tails_only_new_ai_chat_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "latest.log"
    log_path.write_text("[12:00:00 INFO]: <Steve> @AI old question\n", encoding="utf-8")
    service, fake_llm, command_service, close = _make_service(
        tmp_path,
        log_path,
        llm_text="新回复",
        poll_interval_seconds=0.02,
        cooldown_seconds=0,
    )
    try:
        assert service.start() is True
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("[12:00:01 INFO]: <Steve> @AI new question\n")

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not command_service.commands:
            time.sleep(0.02)
    finally:
        service.stop()
        close()

    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["tools"] is None
    assert fake_llm.calls[0]["messages"][0]["role"] == "system"
    assert fake_llm.calls[0]["messages"][1] == {
        "role": "user",
        "content": "new question",
    }
    assert fake_llm.calls[0]["request_options"] == LlmRequestOptions(
        max_tokens=256,
        temperature=0.2,
        timeout_seconds=15,
        max_retries=0,
        extra_body={"enable_thinking": False},
        include_finish_notice=False,
    )
    assert command_service.commands == [{
        "command": "say @Steve 新回复",
        "requested_by": "player_ai",
        "user_confirmed": False,
    }]


def test_player_ai_service_records_chat_and_llm_audit(tmp_path: Path) -> None:
    log_path = tmp_path / "latest.log"
    service, fake_llm, command_service, close = _make_service(
        tmp_path,
        log_path,
        llm_text="记录一下",
        cooldown_seconds=0,
    )
    try:
        assert service.handle_log_line("[12:00:00 INFO]: <Steve> @AI hi") is True
        assert service.process_next_queued_for_tests() is True

        conn = service._chat_repo.connection
        llm_count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        messages = conn.execute(
            "SELECT role, content FROM chat_messages ORDER BY message_index"
        ).fetchall()
    finally:
        close()

    assert len(fake_llm.calls) == 1
    assert command_service.commands[0]["command"] == "say @Steve 记录一下"
    assert llm_count == 1
    assert [(row["role"], row["content"]) for row in messages] == [
        ("user", "hi"),
        ("assistant", "记录一下"),
    ]


def test_player_ai_service_drops_cooldown_and_full_queue_without_llm_calls(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "latest.log"
    service, fake_llm, command_service, close = _make_service(
        tmp_path,
        log_path,
        queue_size=1,
        cooldown_seconds=3,
    )
    try:
        assert service.handle_log_line("[12:00:00 INFO]: <Steve> @AI first", now=10) is True
        assert service.handle_log_line("[12:00:01 INFO]: <Steve> @AI second", now=11) is False
        assert service.handle_log_line("[12:00:02 INFO]: <Alex> @AI third", now=12) is False

        assert service.process_next_queued_for_tests() is True
        assert service.process_next_queued_for_tests() is False
    finally:
        close()

    assert len(fake_llm.calls) == 1
    assert len(command_service.commands) == 1


def test_player_ai_service_stays_quiet_when_disabled_or_unconfigured(tmp_path: Path) -> None:
    log_path = tmp_path / "latest.log"
    disabled, disabled_llm, disabled_commands, close_disabled = _make_service(
        tmp_path / "disabled",
        log_path,
        enabled=False,
    )
    unconfigured, unconfigured_llm, unconfigured_commands, close_unconfigured = _make_service(
        tmp_path / "unconfigured",
        log_path,
        is_ai_configured=lambda: False,
    )
    try:
        assert disabled.start() is False
        assert disabled.handle_log_line("[12:00:00 INFO]: <Steve> @AI hi") is False
        assert unconfigured.start() is False
        assert unconfigured.handle_log_line("[12:00:00 INFO]: <Steve> @AI hi") is False
    finally:
        disabled.stop()
        unconfigured.stop()
        close_disabled()
        close_unconfigured()

    assert disabled_llm.calls == []
    assert disabled_commands.commands == []
    assert unconfigured_llm.calls == []
    assert unconfigured_commands.commands == []


def test_player_ai_permission_matrix_is_checked_before_queue(tmp_path: Path) -> None:
    log_path = tmp_path / "latest.log"
    service, fake_llm, command_service, close = _make_service(
        tmp_path,
        log_path,
        cooldown_seconds=0,
        operators={"admin"},
    )
    cases = [
        ("all", "allowlist", ["Steve"], "Steve", True),
        ("all", "allowlist", ["Steve"], "Alex", True),
        ("all", "blocklist", ["Steve"], "Steve", False),
        ("all", "blocklist", ["Steve"], "Alex", True),
        ("operators", "allowlist", ["Admin"], "Admin", True),
        ("operators", "allowlist", ["Steve"], "Steve", True),
        ("operators", "allowlist", ["Steve"], "Alex", False),
        ("operators", "blocklist", ["Admin"], "Admin", False),
        ("operators", "blocklist", ["Steve"], "Admin", True),
        ("operators", "blocklist", ["Steve"], "Steve", False),
    ]
    try:
        for index, (audience, list_mode, entries, player, expected) in enumerate(cases):
            service._policy.save_settings({
                "enabled": True,
                "audience": audience,
                "list_mode": list_mode,
                "access_entries": [
                    {"display_name": entry}
                    for entry in entries
                ],
            })
            accepted = service.handle_log_line(
                f"[12:00:{index:02d} INFO]: <{player}> @AI hello"
            )
            assert accepted is expected
            if accepted:
                assert service.process_next_queued_for_tests() is True
    finally:
        close()

    assert len(fake_llm.calls) == 6
    assert len(command_service.commands) == 6


def test_player_ai_context_is_isolated_and_limited_to_recent_completed_turns(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "latest.log"
    service, fake_llm, _commands, close = _make_service(
        tmp_path,
        log_path,
        cooldown_seconds=0,
    )
    try:
        for prompt in ("one", "two", "three", "four"):
            assert service.handle_log_line(f"<Steve> @AI {prompt}") is True
            assert service.process_next_queued_for_tests() is True
        assert service.handle_log_line("<Alex> @AI private") is True
        assert service.process_next_queued_for_tests() is True
    finally:
        close()

    steve_messages = fake_llm.calls[3]["messages"]
    assert steve_messages[1:] == [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "你好，冒险顺利。"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "你好，冒险顺利。"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "你好，冒险顺利。"},
        {"role": "user", "content": "four"},
    ]
    assert fake_llm.calls[4]["messages"][1:] == [
        {"role": "user", "content": "private"},
    ]


def test_player_ai_context_excludes_expired_turns(tmp_path: Path) -> None:
    log_path = tmp_path / "latest.log"
    service, fake_llm, _commands, close = _make_service(
        tmp_path,
        log_path,
        cooldown_seconds=0,
    )
    try:
        assert service.handle_log_line("<Steve> @AI old") is True
        assert service.process_next_queued_for_tests() is True
        service._chat_repo.connection.execute(
            """
            UPDATE chat_turns
            SET completed_at = '2000-01-01T00:00:00+00:00'
            WHERE source = 'player_ai_chat'
            """
        )
        service._chat_repo.connection.commit()
        assert service.handle_log_line("<Steve> @AI new") is True
        assert service.process_next_queued_for_tests() is True
    finally:
        close()

    assert fake_llm.calls[1]["messages"][1:] == [
        {"role": "user", "content": "new"},
    ]


def test_player_ai_context_keeps_only_whole_turns_within_character_budget(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "latest.log"
    service, fake_llm, _commands, close = _make_service(
        tmp_path,
        log_path,
        llm_text="答" * 180,
        cooldown_seconds=0,
    )
    prompts = [character * 400 for character in ("一", "二", "三", "四")]
    try:
        for prompt in prompts:
            assert service.handle_log_line(f"<Steve> @AI {prompt}") is True
            assert service.process_next_queued_for_tests() is True
    finally:
        close()

    history = fake_llm.calls[3]["messages"][1:-1]
    assert history == [
        {"role": "user", "content": prompts[1]},
        {"role": "assistant", "content": "答" * 180},
        {"role": "user", "content": prompts[2]},
        {"role": "assistant", "content": "答" * 180},
    ]
    assert sum(len(message["content"]) for message in history) <= 1200


def _make_service(
    tmp_path: Path,
    log_path: Path,
    *,
    llm_text: str = "你好，冒险顺利。",
    enabled: bool = True,
    is_ai_configured=lambda: True,
    poll_interval_seconds: float = 0.25,
    cooldown_seconds: float = 0,
    queue_size: int = 8,
    operators: set[str] | None = None,
) -> tuple[PlayerAiChatService, FakeLlm, FakeCommandService, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text("", encoding="utf-8")
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    fake_llm = FakeLlm(llm_text)
    command_service = FakeCommandService()
    player_ai_repository = PlayerAiRepository(connection)
    policy_service = PlayerAiPolicyService(
        player_ai_repository,
        is_operator=lambda name: name.lower() in (operators or set()),
    )
    if not enabled:
        policy_service.save_settings({
            "enabled": False,
            "audience": "all",
            "list_mode": "blocklist",
            "access_entries": [],
        })
    service = PlayerAiChatService(
        log_path=log_path,
        chat_repository=ChatRepository(connection),
        llm_repository=LlmRepository(connection),
        player_ai_repository=player_ai_repository,
        policy_service=policy_service,
        command_service=command_service,
        llm_client=fake_llm,
        is_ai_configured=is_ai_configured,
        poll_interval_seconds=poll_interval_seconds,
        cooldown_seconds=cooldown_seconds,
        queue_size=queue_size,
    )
    return service, fake_llm, command_service, connection.close
