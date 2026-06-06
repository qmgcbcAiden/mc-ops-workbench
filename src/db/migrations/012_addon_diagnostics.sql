CREATE TABLE IF NOT EXISTS addon_scan_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    refresh_online INTEGER NOT NULL DEFAULT 0,
    asset_count INTEGER NOT NULL DEFAULT 0,
    diagnostic_count INTEGER NOT NULL DEFAULT 0,
    blocker_count INTEGER NOT NULL DEFAULT 0,
    high_count INTEGER NOT NULL DEFAULT 0,
    medium_count INTEGER NOT NULL DEFAULT 0,
    low_count INTEGER NOT NULL DEFAULT 0,
    info_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS addon_assets (
    id TEXT PRIMARY KEY,
    scan_run_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    folder TEXT NOT NULL,
    kind TEXT NOT NULL,
    addon_id TEXT,
    name TEXT,
    version TEXT,
    loader TEXT,
    environment TEXT,
    minecraft_versions_json TEXT NOT NULL DEFAULT '[]',
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    conflicts_json TEXT NOT NULL DEFAULT '[]',
    sha1 TEXT,
    sha512 TEXT,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    metadata_source TEXT,
    metadata_confidence TEXT NOT NULL DEFAULT 'heuristic',
    knowledge_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (scan_run_id) REFERENCES addon_scan_runs(id)
);

CREATE TABLE IF NOT EXISTS addon_diagnostics (
    id TEXT PRIMARY KEY,
    scan_run_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    confidence TEXT NOT NULL,
    affected_files_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    sources_json TEXT NOT NULL DEFAULT '[]',
    suggested_actions_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (scan_run_id) REFERENCES addon_scan_runs(id)
);

CREATE TABLE IF NOT EXISTS addon_remediation_proposals (
    id TEXT PRIMARY KEY,
    scan_run_id TEXT,
    diagnostic_ids_json TEXT NOT NULL DEFAULT '[]',
    actions_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    confirmation_required INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (scan_run_id) REFERENCES addon_scan_runs(id)
);

CREATE TABLE IF NOT EXISTS addon_knowledge_cache (
    cache_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    response_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_knowledge_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    request_type TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    error_message TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_addon_assets_scan_run
ON addon_assets(scan_run_id);

CREATE INDEX IF NOT EXISTS idx_addon_assets_path
ON addon_assets(relative_path);

CREATE INDEX IF NOT EXISTS idx_addon_diagnostics_scan_severity
ON addon_diagnostics(scan_run_id, severity);

CREATE INDEX IF NOT EXISTS idx_addon_scan_runs_time
ON addon_scan_runs(started_at);

CREATE INDEX IF NOT EXISTS idx_external_knowledge_requests_time
ON external_knowledge_requests(created_at);
