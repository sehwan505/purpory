CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root TEXT NOT NULL,
    registered INTEGER NOT NULL DEFAULT 0 CHECK (registered IN (0, 1)),
    embedding_model TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX projects_root ON projects(root);

CREATE TRIGGER projects_embedding_model_immutable
BEFORE UPDATE OF embedding_model ON projects
WHEN OLD.embedding_model != '' AND NEW.embedding_model != OLD.embedding_model
BEGIN
    SELECT RAISE(ABORT, 'project embedding model is immutable');
END;

CREATE TABLE memories (
    project_id TEXT NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('note', 'decision', 'reference')),
    value TEXT,
    source TEXT,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, key),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CHECK ((value IS NOT NULL) <> (source IS NOT NULL))
) STRICT;

CREATE INDEX memories_project_key ON memories(project_id, key);

CREATE TABLE memory_versions (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT,
    source TEXT,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX memory_versions_project_key ON memory_versions(project_id, key, id DESC);

CREATE TABLE resources (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    identity TEXT NOT NULL,
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE TABLE views (
    project_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    id TEXT NOT NULL,
    root TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT '',
    revision TEXT NOT NULL DEFAULT '',
    dirty INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 1,
    observed_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id, resource_id) REFERENCES resources(project_id, id) ON DELETE CASCADE
) STRICT;

CREATE TABLE sessions (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    view_id TEXT,
    agent TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'ended')),
    started_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id, view_id) REFERENCES views(project_id, id) ON DELETE SET NULL
) STRICT;

CREATE INDEX sessions_project_status ON sessions(project_id, status, updated_at DESC);

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

CREATE TABLE materials (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    uri TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    modified_at INTEGER NOT NULL,
    processor TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, id),
    UNIQUE (project_id, uri),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE TABLE nodes (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('intent', 'material', 'knowledge', 'reference')),
    subkind TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('observed', 'durable')),
    state TEXT NOT NULL CHECK (state IN ('active', 'missing')),
    provenance TEXT NOT NULL DEFAULT '',
    material_id TEXT NOT NULL DEFAULT '',
    material_uri TEXT NOT NULL DEFAULT '',
    locator TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE UNIQUE INDEX nodes_project_kind_ref ON nodes(project_id, kind, ref);
CREATE INDEX nodes_project_label ON nodes(project_id, label);
CREATE INDEX nodes_project_material ON nodes(project_id, material_id);

CREATE TABLE edges (
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('observed', 'durable')),
    provenance TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, source_id, target_id, relation),
    FOREIGN KEY (project_id, source_id) REFERENCES nodes(project_id, id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, target_id) REFERENCES nodes(project_id, id) ON DELETE CASCADE
) STRICT;

CREATE INDEX edges_project_target ON edges(project_id, target_id);
CREATE INDEX edges_project_owner ON edges(project_id, owner);

CREATE TABLE claims (
    project_id TEXT NOT NULL,
    material_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL DEFAULT '',
    target TEXT NOT NULL DEFAULT '',
    relation TEXT NOT NULL,
    PRIMARY KEY (project_id, material_id, source_id, target_id, target, relation),
    FOREIGN KEY (project_id, material_id) REFERENCES materials(project_id, id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, source_id) REFERENCES nodes(project_id, id) ON DELETE CASCADE,
    CHECK ((target_id != '') <> (target != ''))
) STRICT;

CREATE INDEX claims_project_material ON claims(project_id, material_id);

CREATE TABLE reconciliation_events (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE INDEX reconciliation_events_project_session ON reconciliation_events(project_id, session_id, created_at DESC);

CREATE TABLE context_requests (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    need TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    resolved_key TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    resolved_at INTEGER,
    UNIQUE(project_id, session_id, need),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE INDEX context_requests_project_status ON context_requests(project_id, status, updated_at DESC);

CREATE TABLE context_decisions (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    input_text TEXT,
    proposal_json TEXT NOT NULL CHECK (json_valid(proposal_json)),
    final_action TEXT NOT NULL CHECK (final_action IN ('skip', 'retrieve', 'ask')),
    hints_json TEXT NOT NULL DEFAULT 'null' CHECK (json_valid(hints_json)),
    request_id INTEGER,
    model_id TEXT,
    model_revision TEXT,
    prompt_version TEXT NOT NULL,
    latency_ms INTEGER,
    fallback_reason TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (request_id) REFERENCES context_requests(id) ON DELETE SET NULL
) STRICT;

CREATE INDEX context_decisions_project_created ON context_decisions(project_id, created_at DESC, id DESC);

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
