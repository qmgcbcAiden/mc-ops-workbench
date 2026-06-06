from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ToolExecutionResult:
    content: str
    ok: bool = True
    error_message: str | None = None
    error_type: str | None = None


class ToolRegistry:
    def __init__(self):
        self._handlers: dict[str, ToolHandler] = {}
        self._schemas_by_name: dict[str, dict] = {}

    def register(self, name: str, handler: ToolHandler, schema: dict):
        if not schema or schema.get("type") != "function" or "function" not in schema:
            logger.warning("Tool %r registered with invalid schema — will be ignored at call time", name)
        elif schema["function"].get("name") != name:
            logger.warning(
                "Tool %r registered with schema name %r",
                name,
                schema["function"].get("name"),
            )
        self._handlers[name] = handler
        self._schemas_by_name[name] = schema

    def get_schemas(self) -> list[dict]:
        schemas = []
        for name in self._handlers:
            schema = self._schemas_by_name.get(name, {})
            if schema.get("type") == "function" and "function" in schema:
                schemas.append(schema)
            else:
                logger.warning("Tool %r has invalid schema (missing type/function), skipping", name)
        return schemas

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        if not isinstance(arguments, dict):
            return _tool_error(
                f"工具 {name} 参数必须是 JSON object。",
                error_type="invalid_arguments",
            )

        handler = self._handlers.get(name)
        if handler is None:
            available = list(self._handlers.keys())
            return _tool_error(
                f"未知工具: {name}，可用工具: {available}",
                error_type="unknown_tool",
            )

        try:
            result = handler(arguments)
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False)
            return ToolExecutionResult(content=result)
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return _tool_error(
                f"工具 {name} 执行失败: {exc}",
                error_type=type(exc).__name__,
            )


def _tool_error(message: str, error_type: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=json.dumps({
            "status": "error",
            "error": message,
            "error_type": error_type,
        }, ensure_ascii=False),
        ok=False,
        error_message=message,
        error_type=error_type,
    )
