-- 007: persistent player directory cache

CREATE TABLE IF NOT EXISTS player_directory_players (
    player_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    uuid TEXT,
    avatar_url TEXT,
    is_operator INTEGER NOT NULL DEFAULT 0,
    operator_level INTEGER,
    is_banned INTEGER NOT NULL DEFAULT 0,
    ban_json TEXT,
    known_ips_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_directory_banned_ips (
    ip TEXT PRIMARY KEY,
    players_json TEXT NOT NULL DEFAULT '[]',
    created TEXT,
    source TEXT,
    expires TEXT,
    reason TEXT,
    mapping_source TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_directory_sync_state (
    source TEXT PRIMARY KEY,
    mtime_ns INTEGER,
    size_bytes INTEGER,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_player_directory_players_name
ON player_directory_players(name);

CREATE INDEX IF NOT EXISTS idx_player_directory_players_operator
ON player_directory_players(is_operator);

CREATE INDEX IF NOT EXISTS idx_player_directory_players_banned
ON player_directory_players(is_banned);
