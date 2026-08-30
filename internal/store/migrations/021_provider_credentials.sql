CREATE TABLE provider_credentials (
    account TEXT PRIMARY KEY,
    ciphertext BLOB NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;
