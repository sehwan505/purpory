ALTER TABLE projects ADD COLUMN embedding_model TEXT NOT NULL DEFAULT '';

CREATE TRIGGER projects_embedding_model_immutable
BEFORE UPDATE OF embedding_model ON projects
WHEN OLD.embedding_model != '' AND NEW.embedding_model != OLD.embedding_model
BEGIN
    SELECT RAISE(ABORT, 'project embedding model is immutable');
END;
