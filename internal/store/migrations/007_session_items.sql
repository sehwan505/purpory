CREATE TABLE session_items (
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    preview TEXT NOT NULL DEFAULT '',
    value_hash TEXT NOT NULL DEFAULT '',
    delivered_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, session_id, key, value_hash),
    FOREIGN KEY (project_id, session_id) REFERENCES sessions(project_id, id) ON DELETE CASCADE
) STRICT;

CREATE INDEX session_items_project_session ON session_items(project_id, session_id, delivered_at DESC);
