"""Minecraft log compression and analysis agent.

Deterministic preprocessing + optional LLM interpretation.
When LLM is unavailable, still returns locally filtered results.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.ai.llm_client import LlmClient
from src.ai.prompts import SYSTEM_PROMPT_LOG_AGENT

logger = logging.getLogger(__name__)

# Patterns for lines that must be preserved
_ERROR_LINE = re.compile(r"\bERROR\b", re.IGNORECASE)
_WARN_LINE = re.compile(r"\bWARN(?:ING)?\b", re.IGNORECASE)
_STACK_TRACE = re.compile(
    r"(?:Exception|Caused by:|Suppressed:|"
    r"\s+at\s+|\.\.\.\s+\d+\s+more|"
    r"java\.\w+\.\w+Exception)",
    re.IGNORECASE,
)
_PLUGIN_MOD_KEYWORDS = re.compile(
    r"\b(?:Plugin|mod|Forge|Fabric|Paper|Spigot|Bukkit|"
    r"NeoForge|Quilt|Velocity|BungeeCord)\b",
    re.IGNORECASE,
)
_PLAYER_EVENT = re.compile(
    r"\b(?:joined the game|lost connection|left the game|"
    r"logged in|disconnected|was kicked|was banned|timed out)\b",
    re.IGNORECASE,
)
_COORDINATE_WORLD = re.compile(
    r"\b(?:overworld|nether|end|dimension|chunk|block)\b.*?\b(?:at|in|to)\b",
    re.IGNORECASE,
)


@dataclass
class LogAnalysisResult:
    summary: str
    timeline: list[str] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    retained_snippets: list[str] = field(default_factory=list)
    suspected_causes: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    compressed_text: str = ""


class LogAgent:
    def __init__(
        self,
        llm_client: LlmClient | None = None,
        log_model: str | None = None,
        raw_max_chars: int = 60000,
        compressed_max_chars: int = 8000,
    ):
        self._llm = llm_client
        self._log_model = log_model or (llm_client.model if llm_client else "unknown")
        self.raw_max_chars = raw_max_chars
        self.compressed_max_chars = compressed_max_chars

    def analyze_logs(
        self, raw_lines: list[str], source_label: str = "unknown"
    ) -> LogAnalysisResult:
        """Preprocess raw log lines, then optionally call LLM for interpretation."""

        # 1. Truncate raw input to budget
        total_chars = sum(len(line) for line in raw_lines)
        if total_chars > self.raw_max_chars:
            truncated: list[str] = []
            budget = self.raw_max_chars
            for line in raw_lines:
                if len(line) <= budget:
                    truncated.append(line)
                    budget -= len(line)
                else:
                    truncated.append(line[:budget])
                    break
            raw_lines = truncated

        # 2. Local preprocessing
        key_lines, retained, folded_count = self._preprocess(raw_lines)
        timeline = self._extract_timeline(key_lines)

        # 3. Build compressed text
        compressed = self._build_compressed_text(
            key_lines, retained, folded_count, source_label
        )

        # 4. If LLM available, get interpretation
        if self._llm and compressed.strip():
            llm_result = self._llm_interpret(compressed, source_label)
            if llm_result:
                return llm_result

        # 5. Fallback: local-only result
        return self._local_result(
            compressed, retained, timeline, source_label
        )

    def _preprocess(
        self, lines: list[str]
    ) -> tuple[list[str], list[str], dict[str, int]]:
        """Filter and classify log lines. Returns (key_lines, retained_snippets, folded)."""
        key_lines: list[str] = []
        retained_snippets: list[str] = []
        folded: dict[str, int] = {}
        seen_repeats: dict[str, int] = {}
        info_samples: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Always keep ERROR
            if _ERROR_LINE.search(stripped):
                key_lines.append(stripped)
                retained_snippets.append(stripped)
                continue

            # Always keep WARN
            if _WARN_LINE.search(stripped):
                key_lines.append(stripped)
                retained_snippets.append(stripped)
                continue

            # Keep stack traces
            if _STACK_TRACE.search(stripped):
                key_lines.append(stripped)
                retained_snippets.append(stripped)
                continue

            # Keep plugin/mod related
            if _PLUGIN_MOD_KEYWORDS.search(stripped):
                key_lines.append(stripped)
                continue

            # Keep player events
            if _PLAYER_EVENT.search(stripped):
                key_lines.append(stripped)
                continue

            # Keep coordinate/world/dimension lines
            if _COORDINATE_WORLD.search(stripped):
                key_lines.append(stripped)
                continue

            # Collect INFO lines for sampling (in case nothing else is retained)
            info_samples.append(stripped)

            # Fold repeated lines
            normalized = self._normalize_for_folding(stripped)
            if normalized:
                seen_repeats[normalized] = seen_repeats.get(normalized, 0) + 1

        # Report folded lines
        for norm, count in seen_repeats.items():
            if count >= 3:
                folded[norm] = count

        # Fallback: when no ERROR/WARN/stack-trace lines were retained (pure INFO),
        # sample representative INFO lines so the agent has something to summarize.
        if not retained_snippets and info_samples:
            retained_snippets = self._sample_info_lines(info_samples)
            key_lines = list(retained_snippets)

        return key_lines, retained_snippets, folded

    @staticmethod
    def _sample_info_lines(lines: list[str]) -> list[str]:
        """Pick representative INFO lines: deduplicate by normalized form, keep first of each."""
        seen: set[str] = set()
        samples: list[str] = []
        for line in lines:
            norm = line.strip()
            if not norm:
                continue
            # Use a simpler key: strip timestamps and numbers for grouping
            key = re.sub(r"\[?\d{2}:\d{2}:\d{2}(?:\.\d+)?]?", "", norm)
            key = re.sub(r"\d+", "#", key).strip()
            if key and key not in seen:
                seen.add(key)
                samples.append(norm)
        return samples[:80]  # Cap at 80 representative lines

    def _extract_timeline(self, key_lines: list[str]) -> list[str]:
        """Extract timestamped events in chronological order."""
        time_re = re.compile(r"\[?(\d{2}:\d{2}:\d{2})]?")
        timeline: list[str] = []
        for line in key_lines:
            m = time_re.search(line)
            if m:
                timeline.append(f"[{m.group(1)}] {line[:120]}")
            else:
                timeline.append(line[:120])
        return timeline[:50]  # Cap timeline entries

    def _build_compressed_text(
        self,
        key_lines: list[str],
        retained: list[str],
        folded: dict[str, int],
        source_label: str,
    ) -> str:
        parts: list[str] = []
        parts.append(f"来源：{source_label}")
        parts.append(f"关键行数：{len(key_lines)}，保留原文片段：{len(retained)}")
        parts.append("")

        if folded:
            parts.append("--- 重复日志折叠 ---")
            for norm, count in sorted(folded.items(), key=lambda x: -x[1]):
                parts.append(f"重复 {count} 次：{norm[:100]}")
            parts.append("")

        parts.append("--- 关键日志行 ---")
        for line in key_lines[:200]:  # Cap at 200 lines
            parts.append(line)
        parts.append("")

        parts.append("--- 保留原文片段（关键行已在上面） ---")

        compressed = "\n".join(parts)
        if len(compressed) > self.compressed_max_chars:
            compressed = compressed[: self.compressed_max_chars] + "\n...（已截断）"
        return compressed

    def _llm_interpret(
        self, compressed: str, source_label: str
    ) -> LogAnalysisResult | None:
        """Call the LLM to interpret compressed logs."""
        try:
            response = self._llm.chat([  # type: ignore[union-attr]
                {"role": "system", "content": SYSTEM_PROMPT_LOG_AGENT},
                {"role": "user", "content": compressed},
            ])
            if not response.content:
                return None

            # Parse the structured output
            parsed = self._parse_llm_output(response.content)
            parsed.compressed_text = compressed
            return parsed
        except Exception:
            logger.exception("LogAgent LLM call failed")
            return None

    def _parse_llm_output(self, text: str) -> LogAnalysisResult:
        """Parse the LLM's structured output into LogAnalysisResult."""
        sections: dict[str, object] = {
            "summary": "",
            "timeline": [],
            "key_findings": [],
            "retained_snippets": [],
            "suspected_causes": [],
            "next_steps": [],
        }

        current_section = ""
        section_buffers: dict[str, list[str]] = {k: [] for k in sections if k != "summary"}

        for line in text.split("\n"):
            stripped = line.strip()
            lower = stripped.lower()

            if "【摘要】" in stripped:
                current_section = "summary"
                continue
            elif "【时间线】" in stripped:
                current_section = "timeline"
                continue
            elif "【关键发现】" in stripped:
                current_section = "key_findings"
                continue
            elif "【保留原文片段】" in stripped:
                current_section = "retained_snippets"
                continue
            elif "【建议下一步】" in stripped:
                current_section = "next_steps"
                continue
            elif stripped.startswith("【") and stripped.endswith("】"):
                current_section = ""
                continue

            if not stripped or not current_section:
                continue

            if current_section == "summary":
                if sections["summary"]:
                    sections["summary"] += "\n"
                sections["summary"] += stripped
            elif current_section in section_buffers:
                section_buffers[current_section].append(stripped)

        # Extract suspected causes from key_findings
        for finding in section_buffers.get("key_findings", []):
            if any(kw in finding.lower() for kw in ["推断", "怀疑", "可能", "根因", "cause"]):
                sections["suspected_causes"].append(finding)

        return LogAnalysisResult(
            summary=sections["summary"],
            timeline=section_buffers.get("timeline", []),
            key_findings=section_buffers.get("key_findings", []),
            retained_snippets=section_buffers.get("retained_snippets", []),
            suspected_causes=sections["suspected_causes"],
            next_steps=section_buffers.get("next_steps", []),
        )

    def _local_result(
        self,
        compressed: str,
        retained: list[str],
        timeline: list[str],
        source_label: str,
    ) -> LogAnalysisResult:
        """Build a result when LLM is unavailable."""
        error_count = sum(1 for line in retained if _ERROR_LINE.search(line))
        warn_count = sum(1 for line in retained if _WARN_LINE.search(line))
        info_only = (error_count == 0 and warn_count == 0)

        findings = []
        if error_count:
            findings.append(f"发现 {error_count} 条 ERROR 级别日志（日志证据）")
        if warn_count:
            findings.append(f"发现 {warn_count} 条 WARN 级别日志（日志证据）")
        if info_only:
            # Extract topic summary from INFO lines
            topics = self._extract_info_topics(retained)
            findings.append(f"日志仅含 INFO 级别，共 {len(retained)} 条代表性日志")
            for topic in topics:
                findings.append(topic)
        elif not findings:
            findings.append("未发现明显异常日志")

        if info_only:
            summary = (
                f"来源 {source_label}，纯 INFO 日志，共采样 {len(retained)} 条代表性行。"
                "未配置大模型，以下为本地阶段归纳。"
            )
        else:
            summary = (
                f"来源 {source_label}，共 {len(retained)} 条关键日志（"
                f"ERROR {error_count}，WARN {warn_count}）。"
                "未配置大模型，仅完成本地日志筛选。"
            )

        return LogAnalysisResult(
            summary=summary,
            timeline=timeline[:10],
            key_findings=findings,
            retained_snippets=retained[:20],
            suspected_causes=[],
            next_steps=["配置 QWEN_API_KEY 以启用 AI 日志解读。",
                        "手动检查关键行的完整上下文。",
                        "查看对应时间段玩家行为和服务器事件。"],
            compressed_text=compressed,
        )

    @staticmethod
    def _extract_info_topics(lines: list[str]) -> list[str]:
        """Extract activity topics from INFO-level log lines."""
        topics: list[str] = []
        keyword_groups = {
            "启动完成": ["done", "started", "loaded", "enabled"],
            "世界加载": ["world", "chunk", "spawn", "dimension"],
            "玩家活动": ["joined", "left", "disconnected", "logged in"],
            "网络监听": ["listening", "port", "bind"],
            "插件/Mod 加载": ["plugin", "mod", "forge", "fabric", "paper"],
            "自动保存": ["save", "backup", "autosave"],
            "服务器关闭": ["stop", "shutdown", "closing"],
        }
        for line in lines[:50]:
            lowered = line.lower()
            for topic_label, keywords in keyword_groups.items():
                if any(kw in lowered for kw in keywords):
                    if topic_label not in topics:
                        topics.append(topic_label)
        return topics if topics else ["INFO 日志未匹配到已知阶段关键词"]

    @staticmethod
    def _normalize_for_folding(line: str) -> str:
        """Normalize a log line to detect repeats — strip timestamps and numbers."""
        line = re.sub(r"\[?\d{2}:\d{2}:\d{2}(?:\.\d+)?]?", "", line)
        line = re.sub(r"\d+", "#", line)
        return line.strip()
