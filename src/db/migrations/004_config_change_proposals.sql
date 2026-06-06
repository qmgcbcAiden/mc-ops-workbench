-- Migration 004: auditable Minecraft configuration change proposals.

CREATE TABLE IF NOT EXISTS config_change_proposals (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    user_request TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    before_hash TEXT NOT NULL,
    after_hash TEXT NOT NULL,
    before_content TEXT NOT NULL,
    after_content TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    restart_required INTEGER NOT NULL,
    warnings_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    applied_at TEXT,
    confirmed_by TEXT,
    backup_path TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_config_change_proposals_session
ON config_change_proposals(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_config_change_proposals_path
ON config_change_proposals(relative_path, created_at);
