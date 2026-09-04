ALTER TABLE edges ADD COLUMN state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'retired'));
ALTER TABLE edges ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0;
ALTER TABLE edges ADD COLUMN retired_at INTEGER;

UPDATE edges SET created_at = unixepoch() WHERE created_at = 0;

CREATE INDEX edges_project_state ON edges(project_id, state);

CREATE TABLE navigation_events (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('query', 'explain', 'path', 'deliver')),
    source_node_id TEXT NOT NULL DEFAULT '',
    target_node_id TEXT NOT NULL DEFAULT '',
    decision_id INTEGER,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (project_id, session_id) REFERENCES sessions(project_id, id) ON DELETE CASCADE,
    FOREIGN KEY (decision_id) REFERENCES context_decisions(id) ON DELETE SET NULL,
    CHECK (source_node_id != '' OR target_node_id != '')
) STRICT;

CREATE INDEX navigation_events_project_session ON navigation_events(project_id, session_id, id DESC);
