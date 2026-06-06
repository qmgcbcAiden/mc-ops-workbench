-- 008: rebuild conversation context schema.
--
-- This intentionally clears chat/AI conversation history and observability rows.
-- Server/runtime data, command audits, metrics, player cache, and config audits remain intact.

DROP TABLE IF EXISTS chat_context_items;
DROP TABLE IF EXISTS chat_context_snapshots;
DROP TABLE IF EXISTS log_analysis_results;
DROP TABLE IF EXISTS chat_session_summaries;
DROP TABLE IF EXISTS chat_attachments;
DROP TABLE IF EXISTS chat_messages;
DROP TABLE IF EXISTS chat_turns;
DROP TABLE IF EXISTS chat_sessions;
DROP TABLE IF EXISTS tool_calls;
DROP TABLE IF EXISTS llm_calls;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_turn_at TEXT
);

CREATE TABLE IF NOT EXISTS chat_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
    UNIQUE (session_id, turn_index)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'visible',
    content_type TEXT NOT NULL DEFAULT 'text',
    content TEXT NOT NULL,
    tool_name TEXT,
    tool_call_id TEXT,
    char_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
    FOREIGN KEY (turn_id) REFERENCES chat_turns(id),
    UNIQUE (turn_id, message_index)
);

CREATE TABLE IF NOT EXISTS chat_attachments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    content TEXT NOT NULL,
    compressed_content TEXT,
    include_policy TEXT NOT NULL DEFAULT 'current_turn',
    metadata_json TEXT,
    char_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
    FOREIGN KEY (turn_id) REFERENCES chat_turns(id)
);

CREATE TABLE IF NOT EXISTS chat_session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    previous_summary_id INTEGER,
    covered_through_turn_index INTEGER NOT NULL DEFAULT 0,
    covered_through_message_index INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    source_llm_call_id INTEGER,
    char_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
    FOREIGN KEY (previous_summary_id) REFERENCES chat_session_summaries(id),
    FOREIGN KEY (source_llm_call_id) REFERENCES llm_calls(id)
);

CREATE TABLE IF NOT EXISTS chat_context_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    model TEXT,
    purpose TEXT NOT NULL,
    max_chars INTEGER NOT NULL,
    total_chars INTEGER NOT NULL,
    summary_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
    FOREIGN KEY (turn_id) REFERENCES chat_turns(id),
    FOREIGN KEY (summary_id) REFERENCES chat_session_summaries(id)
);

CREATE TABLE IF NOT EXISTS chat_context_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    item_order INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_id TEXT,
    role TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    included_chars INTEGER NOT NULL,
    truncated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES chat_context_snapshots(id)
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    turn_id TEXT,
    context_snapshot_id INTEGER,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
    FOREIGN KEY (turn_id) REFERENCES chat_turns(id),
    FOREIGN KEY (context_snapshot_id) REFERENCES chat_context_snapshots(id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    llm_call_id INTEGER,
    session_id TEXT,
    turn_id TEXT,
    context_snapshot_id INTEGER,
    provider_tool_call_id TEXT,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    latency_ms INTEGER,
    error_type TEXT,
    FOREIGN KEY (llm_call_id) REFERENCES llm_calls(id),
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
    FOREIGN KEY (turn_id) REFERENCES chat_turns(id),
    FOREIGN KEY (context_snapshot_id) REFERENCES chat_context_snapshots(id)
);

CREATE TABLE IF NOT EXISTS log_analysis_results (
    id TEXT PRIMARY KEY,
    attachment_id TEXT,
    source_label TEXT NOT NULL,
    raw_line_count INTEGER NOT NULL,
    raw_char_count INTEGER NOT NULL,
    compressed_text TEXT NOT NULL,
    key_findings_json TEXT NOT NULL,
    retained_snippets TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (attachment_id) REFERENCES chat_attachments(id)
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated
ON chat_sessions(updated_at);

CREATE INDEX IF NOT EXISTS idx_chat_turns_session_index
ON chat_turns(session_id, turn_index);

CREATE INDEX IF NOT EXISTS idx_chat_messages_turn_index
ON chat_messages(turn_id, message_index);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_turn
ON chat_messages(session_id, turn_id, message_index);

CREATE INDEX IF NOT EXISTS idx_chat_messages_visibility
ON chat_messages(session_id, visibility, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_attachments_session_turn
ON chat_attachments(session_id, turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_session_summaries_session
ON chat_session_summaries(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_context_snapshots_turn
ON chat_context_snapshots(session_id, turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_context_items_snapshot
ON chat_context_items(snapshot_id, item_order);

CREATE INDEX IF NOT EXISTS idx_llm_calls_turn
ON llm_calls(session_id, turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_tool_calls_llm_call
ON tool_calls(llm_call_id);

CREATE INDEX IF NOT EXISTS idx_tool_calls_name_time
ON tool_calls(tool_name, created_at);

CREATE INDEX IF NOT EXISTS idx_tool_calls_turn
ON tool_calls(session_id, turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_log_analysis_results_attachment
ON log_analysis_results(attachment_id);
