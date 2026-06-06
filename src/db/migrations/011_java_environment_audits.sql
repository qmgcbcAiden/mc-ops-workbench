CREATE TABLE IF NOT EXISTS java_environment_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    minecraft_version TEXT,
    required_java_major INTEGER,
    selected_java_path TEXT,
    selected_java_home TEXT,
    candidate_source TEXT,
    distribution TEXT,
    package_type TEXT,
    error_message TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_java_environment_audits_time
ON java_environment_audits(created_at);

CREATE INDEX IF NOT EXISTS idx_java_environment_audits_action_time
ON java_environment_audits(action, created_at);
