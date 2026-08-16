CREATE TABLE materials (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    uri TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    modified_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (project_id, id),
    UNIQUE (project_id, uri),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) STRICT;

ALTER TABLE nodes ADD COLUMN material_id TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN material_uri TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN locator TEXT NOT NULL DEFAULT '';
CREATE INDEX nodes_project_material ON nodes(project_id, material_id);

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
