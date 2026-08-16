DELETE FROM resources
WHERE NOT EXISTS (
    SELECT 1 FROM views
    WHERE views.project_id = resources.project_id AND views.resource_id = resources.id
)
AND EXISTS (
    SELECT 1 FROM resources AS canonical
    WHERE canonical.project_id = resources.project_id
      AND canonical.provider = resources.provider
      AND canonical.identity = resources.identity
      AND canonical.id <> resources.id
      AND EXISTS (
          SELECT 1 FROM views
          WHERE views.project_id = canonical.project_id AND views.resource_id = canonical.id
      )
);
