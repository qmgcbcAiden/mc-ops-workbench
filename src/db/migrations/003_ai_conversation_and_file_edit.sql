-- Migration 003: AI conversation context, log analysis, and file editing audit.

CREATE TABLE IF NOT EXISTS chat_session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    covered_message_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_chat_session_summaries_session
ON chat_session_summaries(session_id, created_at);

CREATE TABLE IF NOT EXISTS chat_attachments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_chat_attachments_session
ON chat_attachments(session_id, created_at);

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

CREATE INDEX IF NOT EXISTS idx_log_analysis_results_attachment
ON log_analysis_results(attachment_id);

CREATE TABLE IF NOT EXISTS file_edit_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relative_path TEXT NOT NULL,
    size_before INTEGER NOT NULL,
    size_after INTEGER NOT NULL,
    backup_path TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_edit_audits_time
ON file_edit_audits(created_at);
