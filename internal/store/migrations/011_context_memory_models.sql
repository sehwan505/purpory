ALTER TABLE context_requests ADD COLUMN resolved_key TEXT;
ALTER TABLE context_requests ADD COLUMN resolved_at INTEGER;

CREATE TABLE gate_feedback (
    decision_id INTEGER PRIMARY KEY,
    verdict TEXT NOT NULL CHECK (verdict IN ('correct', 'incorrect')),
    expected_action TEXT CHECK (expected_action IN ('skip', 'retrieve', 'ask')),
    expected_keys_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(expected_keys_json)),
    note TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (decision_id) REFERENCES context_decisions(id) ON DELETE CASCADE
) STRICT;

CREATE TABLE needs_reviews (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    outcome TEXT CHECK (outcome IN ('keep', 'change')),
    result_version_id INTEGER,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    resolved_at INTEGER,
    UNIQUE(project_id, key, source_type, source_id, content_hash),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE INDEX needs_reviews_project_status ON needs_reviews(project_id, status, created_at DESC);

CREATE TABLE memory_usage (
    project_id TEXT NOT NULL,
    key TEXT NOT NULL,
    selected_count INTEGER NOT NULL DEFAULT 0,
    expanded_count INTEGER NOT NULL DEFAULT 0,
    last_selected_at INTEGER,
    last_expanded_at INTEGER,
    PRIMARY KEY (project_id, key),
    FOREIGN KEY (project_id, key) REFERENCES memories(project_id, key) ON DELETE CASCADE
) STRICT;

CREATE TABLE embeddings (
    project_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    vector_json TEXT NOT NULL CHECK (json_valid(vector_json)),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, node_id, model),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE INDEX embeddings_project_model ON embeddings(project_id, model, updated_at DESC);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;
