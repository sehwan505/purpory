CREATE TABLE links (
    project_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('intent', 'material', 'knowledge')),
    source_ref TEXT NOT NULL,
    relation TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('intent', 'material', 'knowledge')),
    target_ref TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, source_kind, source_ref, relation, target_kind, target_ref),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

CREATE INDEX links_project_source ON links(project_id, source_kind, source_ref);
CREATE INDEX links_project_target ON links(project_id, target_kind, target_ref);
