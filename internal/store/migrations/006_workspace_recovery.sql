ALTER TABLE views ADD COLUMN available INTEGER NOT NULL DEFAULT 1;

DROP INDEX sessions_project_status;
ALTER TABLE sessions RENAME TO sessions_before_workspace_recovery;

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

INSERT INTO sessions(project_id, id, view_id, agent, status, started_at, updated_at)
SELECT project_id, id, view_id, agent, status, started_at, updated_at
FROM sessions_before_workspace_recovery;

DROP TABLE sessions_before_workspace_recovery;
CREATE INDEX sessions_project_status ON sessions(project_id, status, updated_at DESC);
