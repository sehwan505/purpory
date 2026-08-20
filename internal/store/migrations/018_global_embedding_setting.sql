INSERT INTO settings(key, value)
SELECT 'model.embedding', embedding_model
FROM projects
WHERE embedding_model != ''
ORDER BY updated_at DESC
LIMIT 1
ON CONFLICT(key) DO NOTHING;
