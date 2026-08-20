INSERT OR IGNORE INTO resource_observations(id, provider, label, identity)
SELECT id, provider, label, identity FROM resources;

INSERT OR IGNORE INTO view_observations(id, resource_id, root, branch, revision, dirty, available, observed_at)
SELECT id, resource_id, root, branch, revision, dirty, available, observed_at FROM views;
