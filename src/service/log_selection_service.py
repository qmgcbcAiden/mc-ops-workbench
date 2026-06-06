from __future__ import annotations

import logging
from dataclasses import dataclass

from src.ai.log_agent import LogAgent, LogAnalysisResult
from src.repositories.chat_attachment_repository import ChatAttachmentRepository
from src.repositories.log_analysis_repository import LogAnalysisRepository

logger = logging.getLogger(__name__)


@dataclass
class LogSelection:
    source: str
    label: str
    raw_lines: list[str]
    line_count: int
    time_range: str
    event_ids: list[int]
    raw_text: str


class LogSelectionService:
    def __init__(
        self,
        attachment_repo: ChatAttachmentRepository,
        analysis_repo: LogAnalysisRepository,
        log_agent: LogAgent | None = None,
    ):
        self._attachment_repo = attachment_repo
        self._analysis_repo = analysis_repo
        self._log_agent = log_agent

    def create_attachment(
        self, session_id: str, selection: LogSelection
    ) -> dict:
        """Create a chat attachment from user-selected log lines."""
        attachment_id = self._attachment_repo.create(
            session_id=session_id,
            kind="log_selection",
            label=selection.label,
            content=selection.raw_text,
            metadata={
                "source": selection.source,
                "line_count": selection.line_count,
                "time_range": selection.time_range,
                "event_ids": selection.event_ids,
            },
        )
        return {
            "attachment_id": attachment_id,
            "label": selection.label,
            "line_count": selection.line_count,
            "time_range": selection.time_range,
        }

    def analyze_attachment(self, attachment_id: str) -> dict | None:
        """Run LogAgent on an attachment and persist the result."""
        attachment = self._attachment_repo.get(attachment_id)
        if attachment is None:
            return None

        raw_lines = attachment["content"].split("\n")
        source_label = attachment["label"]

        if self._log_agent:
            result = self._log_agent.analyze_logs(raw_lines, source_label)
            analysis_id = self._analysis_repo.create(
                source_label=source_label,
                raw_line_count=len(raw_lines),
                raw_char_count=len(attachment["content"]),
                compressed_text=result.compressed_text,
                key_findings=result.key_findings,
                retained_snippets=result.retained_snippets,
                attachment_id=attachment_id,
                model=self._log_agent._log_model,
            )
            return {
                "analysis_id": analysis_id,
                "summary": result.summary,
                "key_findings": result.key_findings,
                "suspected_causes": result.suspected_causes,
                "next_steps": result.next_steps,
                "compressed_text": result.compressed_text,
            }

        # No log agent configured — return raw text summary
        return {
            "analysis_id": None,
            "summary": f"共 {len(raw_lines)} 行日志，未配置 AI 日志解读。",
            "key_findings": [],
            "suspected_causes": [],
            "next_steps": [],
            "compressed_text": attachment["content"][:8000],
        }
