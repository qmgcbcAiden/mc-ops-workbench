-- 009: autonomous Minecraft configuration loop tasks.

CREATE TABLE IF NOT EXISTS autonomous_tasks (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    initial_turn_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    user_goal TEXT NOT NULL,
    status TEXT NOT NULL,
    max_rounds INTEGER NOT NULL DEFAULT 3,
    max_llm_calls INTEGER NOT NULL DEFAULT 8,
    max_tool_calls INTEGER NOT NULL DEFAULT 20,
    current_round INTEGER NOT NULL DEFAULT 0,
    llm_call_count INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    stopped_reason TEXT,
    final_summary TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
    FOREIGN KEY (initial_turn_id) REFERENCES chat_turns(id)
);

CREATE TABLE IF NOT EXISTS autonomous_task_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    round_index INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY (task_id) REFERENCES autonomous_tasks(id)
);

CREATE TABLE IF NOT EXISTS autonomous_task_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    round_index INTEGER NOT NULL,
    artifact_type TEXT NOT NULL,
    artifact_text_id TEXT,
    artifact_int_id INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    CHECK (artifact_text_id IS NOT NULL OR artifact_int_id IS NOT NULL),
    FOREIGN KEY (task_id) REFERENCES autonomous_tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_session_time
ON autonomous_tasks(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_turn
ON autonomous_tasks(initial_turn_id);

CREATE INDEX IF NOT EXISTS idx_autonomous_task_steps_task_round
ON autonomous_task_steps(task_id, round_index, created_at);

CREATE INDEX IF NOT EXISTS idx_autonomous_task_artifacts_task_round
ON autonomous_task_artifacts(task_id, round_index, artifact_type, created_at);

CREATE INDEX IF NOT EXISTS idx_autonomous_task_artifacts_text
ON autonomous_task_artifacts(artifact_type, artifact_text_id);

CREATE INDEX IF NOT EXISTS idx_autonomous_task_artifacts_int
ON autonomous_task_artifacts(artifact_type, artifact_int_id);
