-- Migration 005: richer Function Calling observability.

ALTER TABLE tool_calls ADD COLUMN provider_tool_call_id TEXT;
ALTER TABLE tool_calls ADD COLUMN finished_at TEXT;
ALTER TABLE tool_calls ADD COLUMN latency_ms INTEGER;
ALTER TABLE tool_calls ADD COLUMN error_type TEXT;

CREATE INDEX IF NOT EXISTS idx_tool_calls_llm_call
ON tool_calls(llm_call_id);

CREATE INDEX IF NOT EXISTS idx_tool_calls_name_time
ON tool_calls(tool_name, created_at);

CREATE INDEX IF NOT EXISTS idx_tool_calls_time
ON tool_calls(created_at);
