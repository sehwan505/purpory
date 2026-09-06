-- Remove pre-release exploration schemas from databases that already applied them.
DROP TABLE IF EXISTS exploration_edges;
DROP TABLE IF EXISTS exploration_nodes;
DROP TABLE IF EXISTS agent_knowledge_versions;

CREATE TABLE IF NOT EXISTS agent_change_log (
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

CREATE INDEX IF NOT EXISTS agent_change_log_project_id ON agent_change_log(project_id, id);
