CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    tool_call_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
);

CREATE TABLE IF NOT EXISTS server_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT,
    level TEXT,
    category TEXT,
    player_id TEXT,
    message TEXT NOT NULL,
    raw_line TEXT NOT NULL,
    raw_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    online_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS player_snapshot_items (
    snapshot_id INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, player_id),
    FOREIGN KEY (snapshot_id) REFERENCES player_snapshots(id)
);

CREATE TABLE IF NOT EXISTS metrics_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    cpu_percent REAL NOT NULL,
    memory_percent REAL NOT NULL,
    memory_used_mb REAL,
    memory_total_mb REAL,
    server_pid INTEGER
);

CREATE TABLE IF NOT EXISTS command_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    normalized_command TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    confirmation_required INTEGER NOT NULL,
    output TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    executed_at TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    llm_call_id INTEGER,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (llm_call_id) REFERENCES llm_calls(id)
);

CREATE INDEX IF NOT EXISTS idx_server_events_time ON server_events(event_time);
CREATE INDEX IF NOT EXISTS idx_server_events_level ON server_events(level);
CREATE INDEX IF NOT EXISTS idx_metrics_samples_time ON metrics_samples(captured_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_time ON chat_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_command_audits_time ON command_audits(created_at);

