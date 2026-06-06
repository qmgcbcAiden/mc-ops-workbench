from __future__ import annotations

import json

from src.ai.tool_registry import ToolRegistry


VALID_SCHEMA = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo arguments.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def test_tool_registry_executes_registered_tool() -> None:
    registry = ToolRegistry()
    registry.register("echo", lambda args: json.dumps({"ok": args["value"]}), VALID_SCHEMA)

    result = registry.execute("echo", {"value": "yes"})

    assert result.ok is True
    assert json.loads(result.content)["ok"] == "yes"


def test_tool_registry_reports_unknown_tool_as_failure() -> None:
    registry = ToolRegistry()

    result = registry.execute("missing", {})

    assert result.ok is False
    assert result.error_type == "unknown_tool"
    assert json.loads(result.content)["status"] == "error"


def test_tool_registry_reports_handler_exception_as_failure() -> None:
    registry = ToolRegistry()

    def explode(_args: dict) -> str:
        raise RuntimeError("boom")

    registry.register("echo", explode, VALID_SCHEMA)

    result = registry.execute("echo", {})

    assert result.ok is False
    assert result.error_type == "RuntimeError"
    assert "boom" in result.content
