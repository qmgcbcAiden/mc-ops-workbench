CREATE TABLE IF NOT EXISTS server_runtime_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    pid INTEGER,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_server_runtime_events_time
ON server_runtime_events(created_at);
