-- 006: config versioning support (built-in Git + audit extension)

ALTER TABLE config_change_proposals ADD COLUMN auto_approved INTEGER NOT NULL DEFAULT 0;
ALTER TABLE config_change_proposals ADD COLUMN approval_policy TEXT;
ALTER TABLE config_change_proposals ADD COLUMN version_commit_id TEXT;
ALTER TABLE config_change_proposals ADD COLUMN version_status TEXT;
ALTER TABLE config_change_proposals ADD COLUMN version_error_message TEXT;
ALTER TABLE config_change_proposals ADD COLUMN redaction_version TEXT;

CREATE TABLE IF NOT EXISTS config_version_commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_id TEXT NOT NULL,
    parent_commit_id TEXT,
    proposal_id TEXT,
    relative_path TEXT NOT NULL,
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    risk_level TEXT,
    auto_approved INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES config_change_proposals(id)
);

CREATE INDEX IF NOT EXISTS idx_config_version_commits_path_time
ON config_version_commits(relative_path, created_at);

CREATE INDEX IF NOT EXISTS idx_config_version_commits_proposal
ON config_version_commits(proposal_id);
