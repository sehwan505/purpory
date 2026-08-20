CREATE TABLE resource_observations (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    identity TEXT NOT NULL,
    observed_at INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE (provider, identity)
) STRICT;

CREATE TABLE view_observations (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    root TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT '',
    revision TEXT NOT NULL DEFAULT '',
    dirty INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 1,
    observed_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (resource_id) REFERENCES resource_observations(id) ON DELETE CASCADE
) STRICT;

CREATE INDEX view_observations_resource ON view_observations(resource_id, observed_at DESC);
