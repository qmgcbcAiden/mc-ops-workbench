"""AI assistant infrastructure.

LlmClient → OpenAI-compatible LLM calls
AssistantService → orchestrates LLM + tools + streaming
ContextManager → context window and summary management
LogAgent → Minecraft log compression and analysis
ToolRegistry → Function Calling tool registry
"""

from src.ai.llm_client import (
    FakeLlmClient,
    LlmClient,
    LlmRequestOptions,
    LlmResponse,
)
from src.ai.stream_events import ChatStreamEvent, StreamEventType
from src.ai.prompts import SYSTEM_PROMPT_ASSISTANT, SYSTEM_PROMPT_LOG_AGENT, DEFAULT_ASK_AI_QUESTION
from src.ai.context_manager import ContextManager
from src.ai.log_agent import LogAgent, LogAnalysisResult
from src.ai.assistant_service import AssistantService
from src.ai.tool_registry import ToolRegistry
from src.ai.tool_schemas import ALL_TOOLS

__all__ = [
    "LlmClient",
    "FakeLlmClient",
    "LlmRequestOptions",
    "LlmResponse",
    "ChatStreamEvent",
    "StreamEventType",
    "SYSTEM_PROMPT_ASSISTANT",
    "SYSTEM_PROMPT_LOG_AGENT",
    "DEFAULT_ASK_AI_QUESTION",
    "ContextManager",
    "LogAgent",
    "LogAnalysisResult",
    "AssistantService",
    "ToolRegistry",
    "ALL_TOOLS",
]
