DROP TABLE edges;
DROP TABLE claims;
DROP TABLE nodes;

-- Observed Materials are cheap to rebuild and must use the new canonical node IDs.
DELETE FROM materials;

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

INSERT INTO nodes(project_id, id, label, kind, subkind, ref, owner, state, provenance, content)
SELECT project_id,
       CASE kind WHEN 'decision' THEN 'intent:' WHEN 'note' THEN 'knowledge:' ELSE 'reference:' END || key,
       key,
       CASE kind WHEN 'decision' THEN 'intent' WHEN 'note' THEN 'knowledge' ELSE 'reference' END,
       kind,
       key,
       'durable',
       'active',
       'memory',
       coalesce(value, source)
FROM memories;

INSERT OR IGNORE INTO nodes(project_id, id, label, kind, ref, owner, state, provenance)
SELECT project_id, source_kind || ':' || source_ref, source_ref, source_kind, source_ref, 'durable', 'missing', 'durable-link'
FROM links;

INSERT OR IGNORE INTO nodes(project_id, id, label, kind, ref, owner, state, provenance)
SELECT project_id, target_kind || ':' || target_ref, target_ref, target_kind, target_ref, 'durable', 'missing', 'durable-link'
FROM links;

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

INSERT INTO edges(project_id, source_id, target_id, relation, owner, provenance)
SELECT project_id, source_kind || ':' || source_ref, target_kind || ':' || target_ref, relation, 'durable', 'durable-link'
FROM links;

DROP TABLE links;

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
