CREATE TABLE context_requests (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    need TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
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
    delivery_json TEXT NOT NULL CHECK (json_valid(delivery_json)),
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

CREATE TABLE awareness_exposures (
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    key TEXT NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL,
    relation TEXT,
    shown_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, session_id, node_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE INDEX awareness_exposures_project_session ON awareness_exposures(project_id, session_id, shown_at DESC);
