ALTER TABLE projects ADD COLUMN registered INTEGER NOT NULL DEFAULT 0 CHECK (registered IN (0, 1));
