from __future__ import annotations

from dataclasses import dataclass

from src.ai.prompts import SYSTEM_PROMPT_ASSISTANT
from src.repositories.chat_attachment_repository import ChatAttachmentRepository
from src.repositories.chat_repository import ChatRepository
from src.repositories.chat_summary_repository import ChatSummaryRepository


_SUMMARY_STRUCTURE = """\
【当前问题】{current_issue}
【已知事实】{known_facts}
【日志证据】{log_evidence}
【已尝试操作】{attempted_actions}
【待验证假设】{pending_hypotheses}
【用户偏好/约束】{user_constraints}"""


@dataclass(frozen=True)
class BuiltContext:
    messages: list[dict]
    snapshot_id: int
    stats: dict


@dataclass(frozen=True)
class _ContextItem:
    item_type: str
    role: str
    content: str
    item_id: str | None = None
    priority: int = 50
    trim_allowed: bool = True


class ContextManager:
    def __init__(
        self,
        chat_repo: ChatRepository,
        summary_repo: ChatSummaryRepository,
        attachment_repo: ChatAttachmentRepository,
        context_max_chars: int = 24000,
        recent_messages_limit: int = 16,
        summary_trigger_messages: int = 24,
        summary_target_chars: int = 3000,
    ):
        self._chat_repo = chat_repo
        self._summary_repo = summary_repo
        self._attachment_repo = attachment_repo
        self.context_max_chars = context_max_chars
        self.recent_messages_limit = recent_messages_limit
        self.summary_trigger_messages = summary_trigger_messages
        self.summary_target_chars = summary_target_chars

    def build_context(
        self,
        session_id: str,
        user_message: str,
        turn_id: str | None = None,
        attachment_ids: list[str] | None = None,
        model: str | None = None,
        purpose: str = "chat",
        include_current_turn_messages: bool = False,
        system_prompt: str | None = None,
    ) -> BuiltContext:
        summary = self._summary_repo.get_latest(session_id)
        covered_turn = int(summary["covered_through_turn_index"]) if summary else 0
        items: list[_ContextItem] = [
            _ContextItem(
                item_type="system_prompt",
                item_id="system",
                role="system",
                content=system_prompt or SYSTEM_PROMPT_ASSISTANT,
                priority=100,
                trim_allowed=False,
            )
        ]

        if summary:
            items.append(
                _ContextItem(
                    item_type="summary",
                    item_id=str(summary["id"]),
                    role="system",
                    content=f"【会话历史摘要】\n{summary['summary']}",
                    priority=90,
                    trim_allowed=True,
                )
            )

        items.extend(
            self._history_items(
                session_id=session_id,
                covered_turn=covered_turn,
                current_turn_id=turn_id,
                include_current_turn_messages=include_current_turn_messages,
            )
        )
        items.extend(self._attachment_items(attachment_ids or []))
        items.append(
            _ContextItem(
                item_type="current_user",
                item_id=turn_id,
                role="user",
                content=user_message,
                priority=100,
                trim_allowed=False,
            )
        )

        messages, snapshot_items = self._enforce_budget(items)
        total_chars = sum(len(message.get("content", "")) for message in messages)
        snapshot_id = self._chat_repo.create_context_snapshot(
            session_id=session_id,
            turn_id=turn_id,
            model=model,
            purpose=purpose,
            max_chars=self.context_max_chars,
            total_chars=total_chars,
            summary_id=summary["id"] if summary else None,
            items=snapshot_items,
        )
        return BuiltContext(
            messages=messages,
            snapshot_id=snapshot_id,
            stats={
                "total_chars": total_chars,
                "max_chars": self.context_max_chars,
                "summary_id": summary["id"] if summary else None,
                "item_count": len(snapshot_items),
                "truncated_count": sum(1 for item in snapshot_items if item["truncated"]),
            },
        )

    def build_messages(
        self,
        session_id: str,
        user_message: str,
        attachment_ids: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> list[dict]:
        return self.build_context(
            session_id=session_id,
            user_message=user_message,
            attachment_ids=attachment_ids,
            system_prompt=system_prompt,
        ).messages

    def needs_summary(self, session_id: str) -> bool:
        latest = self._summary_repo.get_latest(session_id)
        covered_turn = int(latest["covered_through_turn_index"]) if latest else 0
        latest_turn = self._chat_repo.get_latest_turn(session_id)
        if latest_turn is None:
            return False
        return int(latest_turn["turn_index"]) - covered_turn > self.summary_trigger_messages

    def summary_target_turn_index(self, session_id: str) -> int | None:
        latest_turn = self._chat_repo.get_latest_turn(session_id)
        if latest_turn is None:
            return None
        target = int(latest_turn["turn_index"]) - max(1, self.recent_messages_limit // 2)
        latest_summary = self._summary_repo.get_latest(session_id)
        covered = int(latest_summary["covered_through_turn_index"]) if latest_summary else 0
        if target <= covered:
            return None
        return target

    def build_summary_prompt(self, session_id: str, through_turn_index: int | None = None) -> str:
        latest_summary = self._summary_repo.get_latest(session_id)
        covered_turn = int(latest_summary["covered_through_turn_index"]) if latest_summary else 0
        if through_turn_index is None:
            through_turn_index = self.summary_target_turn_index(session_id)
        if through_turn_index is None:
            return "暂无需要总结的新对话内容。"

        messages = self._chat_repo.list_messages_for_summary(
            session_id=session_id,
            after_turn_index=covered_turn,
            through_turn_index=through_turn_index,
        )
        if not messages:
            return "暂无需要总结的新对话内容。"

        previous = ""
        if latest_summary:
            previous = f"上一版摘要：\n{latest_summary['summary']}\n\n"
        conversation = "\n".join(
            _summary_line(message) for message in messages
        )
        return (
            "请把下面 Minecraft 服务器运维对话合并进滚动摘要，"
            "保留技术细节（错误信息、配置名、玩家名、IP、端口、文件路径、命令及执行结果），"
            f"输出不超过 {self.summary_target_chars} 字。\n\n"
            + _SUMMARY_STRUCTURE.format(
                current_issue="（从对话中提取）",
                known_facts="（从对话中提取）",
                log_evidence="（从对话中提取）",
                attempted_actions="（从对话中提取）",
                pending_hypotheses="（从对话中提取）",
                user_constraints="（从对话中提取）",
            )
            + f"\n\n{previous}本次新增对话：\n{conversation}"
        )

    def _history_items(
        self,
        session_id: str,
        covered_turn: int,
        current_turn_id: str | None,
        include_current_turn_messages: bool,
    ) -> list[_ContextItem]:
        turns = self._chat_repo.list_turns(
            session_id=session_id,
            limit=self.recent_messages_limit,
            after_turn_index=covered_turn,
        )
        items: list[_ContextItem] = []
        for turn in turns:
            if turn["id"] == current_turn_id and not include_current_turn_messages:
                continue
            for message in self._chat_repo.list_turn_messages(turn["id"]):
                if turn["id"] == current_turn_id and message["role"] == "user":
                    continue
                items.append(_message_item(message))
        return items

    def _attachment_items(self, attachment_ids: list[str]) -> list[_ContextItem]:
        items: list[_ContextItem] = []
        for attachment_id in attachment_ids:
            attachment = self._attachment_repo.get(attachment_id)
            if attachment is None:
                continue
            content = attachment.get("compressed_content") or attachment.get("content") or ""
            original_chars = len(attachment.get("content") or "")
            label = attachment.get("label") or "附件"
            if attachment.get("compressed_content"):
                prefix = f"【附件摘要：{label}】\n"
            else:
                prefix = f"【附件：{label}】\n"
            items.append(
                _ContextItem(
                    item_type="attachment",
                    item_id=attachment_id,
                    role="system",
                    content=prefix + content,
                    priority=88,
                    trim_allowed=True,
                )
            )
            if not attachment.get("compressed_content") and original_chars > len(content):
                self._attachment_repo.update_compressed_content(attachment_id, content)
        return items

    def _enforce_budget(self, items: list[_ContextItem]) -> tuple[list[dict], list[dict]]:
        total = sum(len(item.content) for item in items)
        if total <= self.context_max_chars:
            return _messages_and_snapshot_items(items)

        required = [item for item in items if not item.trim_allowed]
        flexible = [item for item in items if item.trim_allowed]
        required_chars = sum(len(item.content) for item in required)
        remaining_budget = max(0, self.context_max_chars - required_chars)

        # Keep high-priority and recent items first. Python's stable sort preserves original
        # order among equal priority items, and we restore original order before returning.
        indexed_flexible = list(enumerate(flexible))
        ranked = sorted(indexed_flexible, key=lambda pair: pair[1].priority, reverse=True)
        kept_by_original_index: dict[int, _ContextItem] = {}
        snapshot_overrides: dict[int, dict] = {}
        for original_index, item in ranked:
            content_len = len(item.content)
            if content_len <= remaining_budget:
                kept_by_original_index[original_index] = item
                remaining_budget -= content_len
                continue
            if remaining_budget <= 0:
                snapshot_overrides[original_index] = {
                    "included_chars": 0,
                    "truncated": True,
                }
                continue
            truncated_content = item.content[: max(0, remaining_budget - 3)] + "..."
            kept_by_original_index[original_index] = _ContextItem(
                item_type=item.item_type,
                item_id=item.item_id,
                role=item.role,
                content=truncated_content,
                priority=item.priority,
                trim_allowed=item.trim_allowed,
            )
            snapshot_overrides[original_index] = {
                "included_chars": len(truncated_content),
                "truncated": True,
            }
            remaining_budget = 0

        selected: list[_ContextItem] = []
        flexible_seen = 0
        for item in items:
            if not item.trim_allowed:
                selected.append(item)
                continue
            replacement = kept_by_original_index.get(flexible_seen)
            if replacement is not None:
                selected.append(replacement)
            flexible_seen += 1

        messages, snapshot_items = _messages_and_snapshot_items(selected)
        flexible_seen = 0
        for item in items:
            if not item.trim_allowed:
                continue
            override = snapshot_overrides.get(flexible_seen)
            if override is not None:
                target = next(
                    (
                        snapshot
                        for snapshot in snapshot_items
                        if snapshot["item_type"] == item.item_type
                        and snapshot.get("item_id") == item.item_id
                    ),
                    None,
                )
                if target is not None:
                    target.update(override)
            flexible_seen += 1
        return messages, snapshot_items


def _message_item(message: dict) -> _ContextItem:
    if message["role"] == "tool":
        return _ContextItem(
            item_type="tool_result",
            item_id=message["id"],
            role="system",
            content=_tool_history_content(message),
            priority=60,
            trim_allowed=True,
        )
    return _ContextItem(
        item_type="message",
        item_id=message["id"],
        role=message["role"],
        content=message.get("content") or "",
        priority=72 if message["role"] == "user" else 68,
        trim_allowed=True,
    )


def _messages_and_snapshot_items(items: list[_ContextItem]) -> tuple[list[dict], list[dict]]:
    messages: list[dict] = []
    snapshot_items: list[dict] = []
    for item in items:
        content = item.content or ""
        if not content and item.item_type != "current_user":
            continue
        messages.append({"role": item.role, "content": content})
        snapshot_items.append({
            "item_type": item.item_type,
            "item_id": item.item_id,
            "role": item.role,
            "char_count": len(content),
            "included_chars": len(content),
            "truncated": False,
        })
    return messages, snapshot_items


def _summary_line(message: dict) -> str:
    role = message.get("role", "")
    tool_name = message.get("tool_name")
    label = f"{role}:{tool_name}" if tool_name else role
    return f"[turn {message.get('turn_index')} #{message.get('message_index')} {label}] {message.get('content') or ''}"


def _tool_history_content(message: dict) -> str:
    tool_name = message.get("tool_name") or "local_tool"
    content = message.get("content") or ""
    if len(content) > 1800:
        content = content[:1797] + "..."
    return f"【历史工具结果：{tool_name}】\n{content}"
