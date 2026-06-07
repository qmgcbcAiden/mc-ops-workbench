-- 013: player AI chat policy, access list, and per-player conversations

CREATE TABLE IF NOT EXISTS player_ai_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    audience TEXT NOT NULL DEFAULT 'all'
        CHECK (audience IN ('all', 'operators')),
    list_mode TEXT NOT NULL DEFAULT 'blocklist'
        CHECK (list_mode IN ('allowlist', 'blocklist')),
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO player_ai_settings (
    id,
    enabled,
    audience,
    list_mode,
    updated_at
)
VALUES (1, 1, 'all', 'blocklist', CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS player_ai_access_entries (
    player_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    player_uuid TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_ai_conversations (
    player_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    player_uuid TEXT,
    session_id TEXT NOT NULL UNIQUE,
    last_active_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_player_ai_access_entries_name
ON player_ai_access_entries(display_name COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_player_ai_conversations_active
ON player_ai_conversations(last_active_at);
