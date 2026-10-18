ALTER TABLE projects ADD COLUMN knowledge_revision INTEGER NOT NULL DEFAULT 0;

CREATE TABLE navigation_events_next (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('query', 'explain', 'path', 'deliver')),
    source_node_id TEXT NOT NULL DEFAULT '',
    target_node_id TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (project_id, session_id) REFERENCES sessions(project_id, id) ON DELETE CASCADE,
    CHECK (source_node_id != '' OR target_node_id != '')
) STRICT;

INSERT INTO navigation_events_next
SELECT id, project_id, session_id, action, source_node_id, target_node_id, created_at FROM navigation_events;
DROP TABLE navigation_events;
ALTER TABLE navigation_events_next RENAME TO navigation_events;
CREATE INDEX navigation_events_project_session ON navigation_events(project_id, session_id, id DESC);

DROP TABLE gate_feedback;
DROP TABLE context_decisions;
DROP TABLE context_requests;
DROP TABLE reconciliation_events;
DELETE FROM settings WHERE key IN ('model.gate', 'model.reconcile');
DELETE FROM provider_credentials WHERE account = 'provider.openai-codex.oauth';

UPDATE settings
SET value = json_object('provider', 'ollama', 'model', value, 'dimensions', 512)
WHERE key = 'model.embedding'
  AND CASE WHEN json_valid(value) THEN json_type(value) != 'object' ELSE 1 END;

UPDATE settings
SET value = json_set(value, '$.dimensions', 512)
WHERE key = 'model.embedding' AND COALESCE(json_extract(value, '$.dimensions'), 0) <= 0;
