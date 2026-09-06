ALTER TABLE agent_change_log RENAME TO agent_change_log_previous;

CREATE TABLE agent_change_log (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    entity TEXT NOT NULL CHECK (entity IN ('memory', 'edge')),
    entity_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('set', 'delete', 'link', 'unlink')),
    before_json TEXT NOT NULL CHECK (json_valid(before_json)),
    after_json TEXT NOT NULL CHECK (json_valid(after_json)),
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

INSERT INTO agent_change_log(id, project_id, session_id, entity, entity_key, action, before_json, after_json, created_at)
SELECT id, project_id, session_id, CASE entity WHEN 'knowledge' THEN 'memory' ELSE entity END,
       entity_key, action, before_json, after_json, created_at
FROM agent_change_log_previous;

DROP TABLE agent_change_log_previous;

CREATE INDEX agent_change_log_project_id ON agent_change_log(project_id, id);
