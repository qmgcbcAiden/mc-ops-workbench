from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.ai.llm_client import LlmClient, LlmRequestOptions
from src.mc.server_log_tail import LogTailer
from src.repositories.chat_repository import ChatRepository
from src.repositories.llm_repository import LlmRepository
from src.repositories.player_ai_repository import PlayerAiRepository
from src.service.command_service import CommandService
from src.service.player_ai_policy_service import PlayerAiPolicyService


logger = logging.getLogger(__name__)

PLAYER_AI_POLL_INTERVAL_SECONDS = 0.25
PLAYER_AI_MAX_LINES_PER_POLL = 100
PLAYER_AI_QUEUE_SIZE = 8
PLAYER_AI_COOLDOWN_SECONDS = 3.0
PLAYER_AI_REPLY_MAX_CHARS = 180
PLAYER_AI_CHUNK_MAX_CHARS = 80
PLAYER_AI_MAX_CHUNKS = 3
PLAYER_AI_CONTEXT_TURN_LIMIT = 3
PLAYER_AI_CONTEXT_TTL_SECONDS = 600
PLAYER_AI_CONTEXT_MAX_CHARS = 1200
PLAYER_AI_MAX_TOKENS = 256
PLAYER_AI_TIMEOUT_SECONDS = 15
PLAYER_AI_TEMPERATURE = 0.2
PLAYER_AI_FALLBACK_TEXT = "我没想好怎么回答，换个问法试试。"

SYSTEM_PROMPT_PLAYER_AI = """\
你是 Minecraft 服务器里的游戏内聊天 AI。
你只和普通玩家闲聊、答疑、解释游戏机制或给简短建议。
不要执行、建议或伪装任何服务器命令、shell 命令、配置修改或运维动作。
只输出回复正文，不要包含玩家名、@玩家名、前后缀、标题或说明。
禁止使用 emoji、Markdown、代码块、列表格式、表格和链接。
回复必须是纯文字短句，尽量不超过 180 个汉字或字符。
如果信息不足，直接简短说明不确定，并让玩家换个问法。
"""

_PLAYER_CHAT_RE = re.compile(
    r"(?:^|[:\]]\s*)(?:\[Not Secure\]\s*)?"
    r"<(?P<player>[A-Za-z0-9_]{3,16})>\s+@ai(?:\s+(?P<prompt>.*))?$",
    re.IGNORECASE,
)
_MARKDOWN_RE = re.compile(r"[*_`~#>\[\]\(\)]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "\u2600-\u27bf"
    "]+",
    flags=re.UNICODE,
)
_DANGEROUS_SYMBOL_RE = re.compile(r"[<>|&]")
_DANGEROUS_WORD_RE = re.compile(
    r"\b(?:powershell|python|cmd|copy|move|type|cat|echo|del|rm)\b",
    re.IGNORECASE,
)
_PATH_FRAGMENT_RE = re.compile(r"(?:\.\./|\.\.\\|[A-Za-z]:\\)")


@dataclass(frozen=True)
class PlayerAiRequest:
    player_name: str
    prompt: str
    raw_line: str
    enqueued_at: float = 0.0


class PlayerAiChatService:
    def __init__(
        self,
        log_path: Path,
        chat_repository: ChatRepository,
        llm_repository: LlmRepository,
        player_ai_repository: PlayerAiRepository,
        policy_service: PlayerAiPolicyService,
        command_service: CommandService,
        llm_client: LlmClient | None = None,
        *,
        poll_interval_seconds: float = PLAYER_AI_POLL_INTERVAL_SECONDS,
        max_lines_per_poll: int = PLAYER_AI_MAX_LINES_PER_POLL,
        queue_size: int = PLAYER_AI_QUEUE_SIZE,
        cooldown_seconds: float = PLAYER_AI_COOLDOWN_SECONDS,
        is_ai_configured: Callable[[], bool] | None = None,
    ) -> None:
        self._log_path = log_path
        self._tailer = LogTailer(log_path)
        self._chat_repo = chat_repository
        self._llm_repo = llm_repository
        self._player_ai_repo = player_ai_repository
        self._policy = policy_service
        self._command_service = command_service
        self._llm = llm_client
        self._poll_interval = max(0.05, float(poll_interval_seconds))
        self._max_lines_per_poll = max(1, int(max_lines_per_poll))
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._is_ai_configured = is_ai_configured or (lambda: True)
        self._ai_configured = self._read_ai_configured()
        self._queue: queue.Queue[PlayerAiRequest] = queue.Queue(maxsize=max(1, queue_size))
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._cooldown_lock = threading.Lock()
        self._last_player_request_at: dict[str, float] = {}

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._poll_thread is not None and self._poll_thread.is_alive()

    def start(self) -> bool:
        self._ai_configured = self._read_ai_configured()
        if not self._can_run():
            return False
        with self._state_lock:
            if self._poll_thread is not None and self._poll_thread.is_alive():
                return True
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._tailer.seek_to_end()
            self._poll_thread = threading.Thread(
                target=self._poll_loop,
                args=(stop_event,),
                name="player-ai-chat-log-poll",
                daemon=True,
            )
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                args=(stop_event,),
                name="player-ai-chat-worker",
                daemon=True,
            )
            self._poll_thread.start()
            self._worker_thread.start()
            return True

    def stop(self) -> None:
        self._stop_event.set()
        self._clear_queue()
        threads = []
        with self._state_lock:
            if self._poll_thread is not None:
                threads.append(self._poll_thread)
            if self._worker_thread is not None:
                threads.append(self._worker_thread)
            self._poll_thread = None
            self._worker_thread = None
        for thread in threads:
            if thread is threading.current_thread():
                continue
            thread.join(timeout=1.0)

    def handle_log_line(self, raw_line: str, now: float | None = None) -> bool:
        request = detect_player_ai_request(raw_line)
        if request is None or not self._can_run():
            return False
        if not self._policy.is_allowed(request.player_name):
            return False
        if not self._claim_player_cooldown(request.player_name, now=now):
            return False
        request = replace(request, enqueued_at=time.perf_counter())
        try:
            self._queue.put_nowait(request)
        except queue.Full:
            self._release_player_cooldown(request.player_name)
            return False
        return True

    def process_next_queued_for_tests(self) -> bool:
        try:
            request = self._queue.get_nowait()
        except queue.Empty:
            return False
        try:
            self._handle_request(request)
        finally:
            self._queue.task_done()
        return True

    def _can_run(self) -> bool:
        if not self._policy.is_enabled() or self._llm is None:
            return False
        return self._ai_configured

    def _read_ai_configured(self) -> bool:
        try:
            return bool(self._is_ai_configured())
        except Exception:
            logger.exception("Could not determine whether player AI chat is configured")
            return False

    def _poll_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                for raw_line in self._tailer.read_new_lines(limit=self._max_lines_per_poll):
                    self.handle_log_line(raw_line)
            except Exception:
                logger.exception("Player AI chat log polling failed")
            stop_event.wait(self._poll_interval)

    def _worker_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                request = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._handle_request(request, cancel_event=stop_event)
            except Exception:
                logger.exception("Player AI chat request failed")
            finally:
                self._queue.task_done()

    def _handle_request(
        self,
        request: PlayerAiRequest,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if (
            self._llm is None
            or not self._can_run()
            or not self._policy.is_allowed(request.player_name)
        ):
            return
        request_started_at = request.enqueued_at or time.perf_counter()
        handling_started_at = time.perf_counter()
        queue_wait_ms = _elapsed_perf_ms(request_started_at)
        session_id = self._session_id_for(request.player_name)
        history_messages = self._recent_context_messages(session_id)
        turn_id = self._chat_repo.create_turn(session_id, source="player_ai_chat")
        self._chat_repo.add_message(
            session_id=session_id,
            role="user",
            content=request.prompt,
            turn_id=turn_id,
            metadata={
                "source": "minecraft_chat",
                "player_name": request.player_name,
                "raw_line": request.raw_line,
            },
        )

        call_id = self._llm_repo.create_llm_call(
            purpose="player_ai_chat",
            model=self._llm.model,
            status="started",
            session_id=session_id,
            turn_id=turn_id,
        )
        llm_started_at = time.perf_counter()
        try:
            response = self._llm.chat(
                _player_ai_messages(request, history_messages),
                tools=None,
                request_options=_player_ai_request_options(self._llm),
            )
        except Exception as exc:
            self._llm_repo.finish_llm_call(
                call_id,
                status="failed",
                error_message=str(exc),
                latency_ms=_elapsed_perf_ms(llm_started_at),
            )
            self._chat_repo.update_turn(
                turn_id,
                status="failed",
                source="player_ai_chat",
                completed=True,
            )
            return

        llm_latency_ms = _elapsed_perf_ms(llm_started_at)
        status = "truncated" if response.finish_reason == "length" else "completed"
        self._llm_repo.finish_llm_call(
            call_id,
            status=status,
            token_usage=response.token_usage,
            latency_ms=llm_latency_ms,
            error_message=(
                "Model output reached max_tokens limit."
                if response.finish_reason == "length"
                else None
            ),
        )
        if (
            (cancel_event is not None and cancel_event.is_set())
            or not self._policy.is_allowed(request.player_name)
        ):
            self._chat_repo.update_turn(
                turn_id,
                status="cancelled",
                source="player_ai_chat",
                completed=True,
            )
            return
        chunks = format_player_ai_reply(request.player_name, response.content)
        assistant_content = sanitize_player_ai_reply(response.content)
        command_started_at = time.perf_counter()
        for chunk in chunks:
            self._command_service.submit_command(
                f"say {chunk}",
                requested_by="player_ai",
                user_confirmed=False,
            )
        command_latency_ms = _elapsed_perf_ms(command_started_at)
        self._chat_repo.add_message(
            session_id=session_id,
            role="assistant",
            content=assistant_content,
            turn_id=turn_id,
            metadata={
                "source": "minecraft_chat",
                "player_name": request.player_name,
                "llm_call_id": call_id,
                "sent_chunks": chunks,
                "queue_wait_ms": queue_wait_ms,
                "llm_latency_ms": llm_latency_ms,
                "command_latency_ms": command_latency_ms,
                "handling_latency_ms": _elapsed_perf_ms(handling_started_at),
                "total_latency_ms": _elapsed_perf_ms(request_started_at),
            },
        )
        self._chat_repo.update_turn(
            turn_id,
            status="completed",
            source="player_ai_chat",
            completed=True,
        )
        self._player_ai_repo.touch_conversation(request.player_name)

    def _session_id_for(self, player_name: str) -> str:
        conversation = self._player_ai_repo.get_conversation(player_name)
        if conversation is not None:
            return str(conversation["session_id"])
        session_id = self._chat_repo.create_session(f"游戏内 AI · {player_name}")
        self._player_ai_repo.save_conversation(
            player_name=player_name,
            session_id=session_id,
        )
        return session_id

    def _recent_context_messages(self, session_id: str) -> list[dict]:
        since = (
            datetime.now(timezone.utc)
            - timedelta(seconds=PLAYER_AI_CONTEXT_TTL_SECONDS)
        ).isoformat()
        turns = self._chat_repo.list_recent_completed_turn_messages(
            session_id,
            source="player_ai_chat",
            since=since,
            limit=PLAYER_AI_CONTEXT_TURN_LIMIT,
        )
        selected_turns: list[list[dict]] = []
        total_chars = 0
        for turn in reversed(turns):
            messages = [
                {
                    "role": str(message["role"]),
                    "content": str(message["content"]),
                }
                for message in turn.get("messages", [])
                if message.get("role") in {"user", "assistant"}
            ]
            roles = {message["role"] for message in messages}
            if not {"user", "assistant"}.issubset(roles):
                continue
            turn_chars = sum(len(message["content"]) for message in messages)
            if total_chars + turn_chars > PLAYER_AI_CONTEXT_MAX_CHARS:
                break
            selected_turns.append(messages)
            total_chars += turn_chars
        context: list[dict] = []
        for messages in reversed(selected_turns):
            context.extend(messages)
        return context

    def _claim_player_cooldown(self, player_name: str, now: float | None = None) -> bool:
        if self._cooldown_seconds <= 0:
            return True
        current = time.monotonic() if now is None else now
        key = player_name.lower()
        with self._cooldown_lock:
            previous = self._last_player_request_at.get(key)
            if previous is not None and current - previous < self._cooldown_seconds:
                return False
            self._last_player_request_at[key] = current
            return True

    def _release_player_cooldown(self, player_name: str) -> None:
        with self._cooldown_lock:
            self._last_player_request_at.pop(player_name.lower(), None)

    def _clear_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
            else:
                self._queue.task_done()


def detect_player_ai_request(raw_line: str) -> PlayerAiRequest | None:
    if "@ai" not in raw_line.lower():
        return None
    match = _PLAYER_CHAT_RE.search(raw_line.strip())
    if match is None:
        return None
    prompt = (match.group("prompt") or "").strip()
    if not prompt:
        return None
    return PlayerAiRequest(
        player_name=match.group("player"),
        prompt=prompt,
        raw_line=raw_line,
    )


def format_player_ai_reply(player_name: str, text: str) -> list[str]:
    cleaned = sanitize_player_ai_reply(text)
    chunks = _split_reply(cleaned)
    return [f"@{player_name} {chunk}" for chunk in chunks]


def sanitize_player_ai_reply(text: str) -> str:
    cleaned = _CONTROL_RE.sub(" ", text or "")
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    cleaned = _EMOJI_RE.sub("", cleaned)
    cleaned = _MARKDOWN_RE.sub("", cleaned)
    cleaned = _DANGEROUS_SYMBOL_RE.sub(" ", cleaned)
    cleaned = _PATH_FRAGMENT_RE.sub(" ", cleaned)
    cleaned = _DANGEROUS_WORD_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > PLAYER_AI_REPLY_MAX_CHARS:
        cleaned = cleaned[: PLAYER_AI_REPLY_MAX_CHARS - 3].rstrip() + "..."
    return cleaned or PLAYER_AI_FALLBACK_TEXT


def _split_reply(text: str) -> list[str]:
    remaining = text
    chunks: list[str] = []
    for index in range(PLAYER_AI_MAX_CHUNKS):
        if len(remaining) <= PLAYER_AI_CHUNK_MAX_CHARS:
            chunks.append(remaining)
            break
        if index == PLAYER_AI_MAX_CHUNKS - 1:
            chunks.append(remaining[: PLAYER_AI_CHUNK_MAX_CHARS - 3].rstrip() + "...")
            break
        split_at = _best_split_at(remaining, PLAYER_AI_CHUNK_MAX_CHARS)
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk] or [PLAYER_AI_FALLBACK_TEXT]


def _best_split_at(text: str, limit: int) -> int:
    window = text[:limit]
    for separator in ("。", "！", "？", "，", "；", " "):
        index = window.rfind(separator)
        if index >= max(16, limit // 2):
            return index + (0 if separator == " " else 1)
    return limit


def _player_ai_messages(
    request: PlayerAiRequest,
    history_messages: list[dict] | None = None,
) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT_PLAYER_AI},
        *(history_messages or []),
        {"role": "user", "content": request.prompt},
    ]


def _player_ai_request_options(llm: LlmClient) -> LlmRequestOptions:
    provider = str(getattr(llm, "provider", "") or "").lower()
    return LlmRequestOptions(
        max_tokens=PLAYER_AI_MAX_TOKENS,
        temperature=PLAYER_AI_TEMPERATURE,
        timeout_seconds=PLAYER_AI_TIMEOUT_SECONDS,
        max_retries=0,
        extra_body={"enable_thinking": False} if provider == "qwen" else None,
        include_finish_notice=False,
    )


def _elapsed_perf_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
