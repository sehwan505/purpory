CREATE TABLE exploration_modes (
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, session_id),
    FOREIGN KEY (project_id, session_id) REFERENCES sessions(project_id, id) ON DELETE CASCADE
) STRICT;

CREATE TABLE agent_change_log (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    entity TEXT NOT NULL CHECK (entity IN ('knowledge', 'edge')),
    entity_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('set', 'delete', 'link', 'unlink')),
    before_json TEXT NOT NULL CHECK (json_valid(before_json)),
    after_json TEXT NOT NULL CHECK (json_valid(after_json)),
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE INDEX agent_change_log_project_id ON agent_change_log(project_id, id);
