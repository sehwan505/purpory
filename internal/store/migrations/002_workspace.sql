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
    observed_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id, resource_id) REFERENCES resources(project_id, id) ON DELETE CASCADE
) STRICT;

CREATE TABLE sessions (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    view_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'ended')),
    started_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id, view_id) REFERENCES views(project_id, id) ON DELETE CASCADE
) STRICT;

CREATE INDEX sessions_project_status ON sessions(project_id, status, updated_at DESC);
