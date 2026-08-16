CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX projects_root ON projects(root);

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

CREATE TABLE nodes (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_file TEXT NOT NULL DEFAULT '',
    source_line INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, id)
) STRICT;

CREATE INDEX nodes_project_label ON nodes(project_id, label);

CREATE TABLE edges (
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (project_id, source_id, target_id, relation),
    FOREIGN KEY (project_id, source_id) REFERENCES nodes(project_id, id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, target_id) REFERENCES nodes(project_id, id) ON DELETE CASCADE
) STRICT;

CREATE INDEX edges_project_target ON edges(project_id, target_id);
