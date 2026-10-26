package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
)

func (s *Store) SaveMemory(ctx context.Context, value memory.Memory) (SaveResult, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return SaveResult{}, fmt.Errorf("save memory: begin: %w", err)
	}
	defer tx.Rollback()
	result, err := saveMemory(ctx, tx, value)
	if err != nil {
		return SaveResult{}, err
	}
	if err := tx.Commit(); err != nil {
		return SaveResult{}, fmt.Errorf("save memory: commit: %w", err)
	}
	return result, nil
}

type databaseRunner interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

func saveMemory(ctx context.Context, database databaseRunner, value memory.Memory) (SaveResult, error) {
	var currentHash, currentKind string
	err := database.QueryRowContext(ctx,
		"SELECT content_hash, kind FROM memories WHERE project_id = ? AND key = ?",
		value.ProjectID, value.Key,
	).Scan(&currentHash, &currentKind)
	action := "updated"
	switch {
	case errors.Is(err, sql.ErrNoRows):
		action = "created"
	case err != nil:
		return SaveResult{}, fmt.Errorf("save memory: load current: %w", err)
	case currentHash == value.Hash:
		if err := upsertMemoryNode(ctx, database, value); err != nil {
			return SaveResult{}, err
		}
		return SaveResult{Action: "unchanged"}, nil
	}

	if _, err := database.ExecContext(ctx, `
		INSERT INTO memories (project_id, key, kind, value, source, content_hash)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(project_id, key) DO UPDATE SET
			kind = excluded.kind,
			value = excluded.value,
			source = excluded.source,
			content_hash = excluded.content_hash,
			updated_at = unixepoch()
	`, value.ProjectID, value.Key, value.Kind, value.Value, value.Source, value.Hash); err != nil {
		return SaveResult{}, fmt.Errorf("save memory: upsert: %w", err)
	}
	if currentKind != "" && memory.Kind(currentKind).NodeKind() != value.Kind.NodeKind() {
		oldNodeID := graph.ReferenceID(memory.Kind(currentKind).NodeKind(), value.Key)
		if _, err := database.ExecContext(ctx, `DELETE FROM nodes WHERE project_id = ? AND id = ?`, value.ProjectID, oldNodeID); err != nil {
			return SaveResult{}, fmt.Errorf("save memory: replace graph kind: %w", err)
		}
		if _, err := database.ExecContext(ctx, `DELETE FROM embeddings WHERE project_id = ? AND node_id = ?`, value.ProjectID, oldNodeID); err != nil {
			return SaveResult{}, fmt.Errorf("save memory: replace embedding kind: %w", err)
		}
	}
	if err := upsertMemoryNode(ctx, database, value); err != nil {
		return SaveResult{}, err
	}
	result, err := database.ExecContext(ctx, `
		INSERT INTO memory_versions (project_id, key, kind, value, source, content_hash)
		VALUES (?, ?, ?, ?, ?, ?)
	`, value.ProjectID, value.Key, value.Kind, value.Value, value.Source, value.Hash)
	if err != nil {
		return SaveResult{}, fmt.Errorf("save memory: record version: %w", err)
	}
	versionID, err := result.LastInsertId()
	if err != nil {
		return SaveResult{}, fmt.Errorf("save memory: version ID: %w", err)
	}
	return SaveResult{Action: action, VersionID: versionID}, nil
}

func upsertMemoryNode(ctx context.Context, database databaseRunner, value memory.Memory) error {
	content := ""
	if value.Value != nil {
		content = *value.Value
	} else if value.Source != nil {
		content = *value.Source
	}
	kind := value.Kind.NodeKind()
	if _, err := database.ExecContext(ctx, `
		INSERT INTO nodes(project_id, id, label, kind, subkind, ref, owner, state, provenance, content)
		VALUES (?, ?, ?, ?, ?, ?, 'durable', 'active', 'memory', ?)
		ON CONFLICT(project_id, id) DO UPDATE SET
			label=excluded.label, kind=excluded.kind, subkind=excluded.subkind, ref=excluded.ref,
			owner='durable', state='active', provenance='memory', content=excluded.content
	`, value.ProjectID, graph.ReferenceID(kind, value.Key), value.Key, kind, value.Kind, value.Key, content); err != nil {
		return fmt.Errorf("save memory: upsert graph node: %w", err)
	}
	return nil
}

func (s *Store) Memory(ctx context.Context, projectID, key string) (memory.Memory, error) {
	var value memory.Memory
	var created, timestamp int64
	err := s.db.QueryRowContext(ctx, `
		SELECT project_id, key, kind, value, source, content_hash, created_at, updated_at
		FROM memories WHERE project_id = ? AND key = ?
	`, projectID, key).Scan(
		&value.ProjectID, &value.Key, &value.Kind, &value.Value, &value.Source, &value.Hash, &created, &timestamp,
	)
	if err != nil {
		return memory.Memory{}, fmt.Errorf("load memory: %w", err)
	}
	value.CreatedAt = time.Unix(created, 0).UTC().Format(time.RFC3339)
	value.UpdatedAt = time.Unix(timestamp, 0).UTC().Format(time.RFC3339)
	return value, nil
}

func (s *Store) MemoryVersions(ctx context.Context, projectID, key string) ([]memory.Version, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, key, kind, value, source, content_hash, created_at
		FROM memory_versions WHERE project_id = ? AND key = ? ORDER BY id DESC
	`, projectID, key)
	if err != nil {
		return nil, fmt.Errorf("list memory versions: %w", err)
	}
	defer rows.Close()
	var versions []memory.Version
	for rows.Next() {
		var version memory.Version
		var created int64
		if err := rows.Scan(&version.ID, &version.Key, &version.Kind, &version.Value, &version.Source, &version.Hash, &created); err != nil {
			return nil, fmt.Errorf("list memory versions: scan: %w", err)
		}
		version.CreatedAt = time.Unix(created, 0).UTC().Format(time.RFC3339)
		versions = append(versions, version)
	}
	return versions, rows.Err()
}

func (s *Store) Memories(ctx context.Context, projectID, prefix string) ([]memory.Memory, error) {
	query := `
		SELECT project_id, key, kind, value, source, content_hash, created_at, updated_at
		FROM memories WHERE project_id = ?`
	args := []any{projectID}
	if prefix != "" {
		query += " AND (key = ? OR key LIKE ? ESCAPE '\\')"
		args = append(args, prefix, escapeLike(prefix)+".%")
	}
	query += " ORDER BY key"
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("list memories: %w", err)
	}
	defer rows.Close()
	var values []memory.Memory
	for rows.Next() {
		var value memory.Memory
		var created, timestamp int64
		if err := rows.Scan(
			&value.ProjectID, &value.Key, &value.Kind, &value.Value, &value.Source, &value.Hash, &created, &timestamp,
		); err != nil {
			return nil, fmt.Errorf("list memories: scan: %w", err)
		}
		value.CreatedAt = time.Unix(created, 0).UTC().Format(time.RFC3339)
		value.UpdatedAt = time.Unix(timestamp, 0).UTC().Format(time.RFC3339)
		values = append(values, value)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("list memories: %w", err)
	}
	return values, nil
}
