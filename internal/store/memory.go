package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

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
	var currentHash string
	err := database.QueryRowContext(ctx,
		"SELECT content_hash FROM memories WHERE project_id = ? AND key = ?",
		value.ProjectID, value.Key,
	).Scan(&currentHash)
	action := "updated"
	switch {
	case errors.Is(err, sql.ErrNoRows):
		action = "created"
	case err != nil:
		return SaveResult{}, fmt.Errorf("save memory: load current: %w", err)
	case currentHash == value.Hash:
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

func (s *Store) ReconcileMemories(ctx context.Context, sessionID string, proposals []MemoryProposal) ([]SaveResult, error) {
	if strings.TrimSpace(sessionID) == "" || len(proposals) == 0 || len(proposals) > 20 {
		return nil, errors.New("reconcile memory: session and 1-20 proposals are required")
	}
	projectID := proposals[0].Memory.ProjectID
	seen := map[string]bool{}
	for _, proposal := range proposals {
		if proposal.Memory.ProjectID != projectID || seen[proposal.Memory.Key] {
			return nil, errors.New("reconcile memory: proposals must have one project and unique keys")
		}
		seen[proposal.Memory.Key] = true
	}
	connection, err := s.db.Conn(ctx)
	if err != nil {
		return nil, fmt.Errorf("reconcile memory: connection: %w", err)
	}
	defer connection.Close()
	if _, err := connection.ExecContext(ctx, "BEGIN IMMEDIATE"); err != nil {
		return nil, fmt.Errorf("reconcile memory: begin: %w", err)
	}
	committed := false
	defer func() {
		if !committed {
			_, _ = connection.ExecContext(context.Background(), "ROLLBACK")
		}
	}()
	for _, proposal := range proposals {
		var current string
		err := connection.QueryRowContext(ctx, "SELECT content_hash FROM memories WHERE project_id = ? AND key = ?", projectID, proposal.Memory.Key).Scan(&current)
		switch {
		case errors.Is(err, sql.ErrNoRows) && proposal.ExpectedHash == nil:
		case err == nil && proposal.ExpectedHash != nil && current == *proposal.ExpectedHash:
		case err != nil && !errors.Is(err, sql.ErrNoRows):
			return nil, fmt.Errorf("reconcile memory: load current: %w", err)
		default:
			return nil, ErrMemoryConflict
		}
	}
	type auditChange struct {
		Key       string         `json:"key"`
		Action    string         `json:"action"`
		Before    *memory.Memory `json:"before,omitempty"`
		After     memory.Memory  `json:"after"`
		VersionID int64          `json:"versionId,omitempty"`
	}
	results := make([]SaveResult, 0, len(proposals))
	var changes []auditChange
	for _, proposal := range proposals {
		var before memory.Memory
		var value, source sql.NullString
		err := connection.QueryRowContext(ctx, `SELECT project_id, key, kind, value, source, content_hash FROM memories WHERE project_id = ? AND key = ?`, projectID, proposal.Memory.Key).Scan(
			&before.ProjectID, &before.Key, &before.Kind, &value, &source, &before.Hash,
		)
		var previous *memory.Memory
		if err == nil {
			if value.Valid {
				before.Value = &value.String
			}
			if source.Valid {
				before.Source = &source.String
			}
			previous = &before
		} else if !errors.Is(err, sql.ErrNoRows) {
			return nil, fmt.Errorf("reconcile memory: load audit state: %w", err)
		}
		result, err := saveMemory(ctx, connection, proposal.Memory)
		if err != nil {
			return nil, err
		}
		results = append(results, result)
		if result.Action != "unchanged" {
			changes = append(changes, auditChange{Key: proposal.Memory.Key, Action: result.Action, Before: previous, After: proposal.Memory, VersionID: result.VersionID})
		}
	}
	if len(changes) > 0 {
		encoded, err := json.Marshal(map[string]any{"changes": changes})
		if err != nil {
			return nil, fmt.Errorf("reconcile memory: encode audit: %w", err)
		}
		if _, err := connection.ExecContext(ctx, `INSERT INTO reconciliation_events(project_id, session_id, changes_json) VALUES (?, ?, ?)`, projectID, sessionID, string(encoded)); err != nil {
			return nil, fmt.Errorf("reconcile memory: record audit: %w", err)
		}
	}
	if _, err := connection.ExecContext(ctx, "COMMIT"); err != nil {
		return nil, fmt.Errorf("reconcile memory: commit: %w", err)
	}
	committed = true
	return results, nil
}

func (s *Store) Memory(ctx context.Context, projectID, key string) (memory.Memory, error) {
	var value memory.Memory
	var timestamp int64
	err := s.db.QueryRowContext(ctx, `
		SELECT project_id, key, kind, value, source, content_hash, updated_at
		FROM memories WHERE project_id = ? AND key = ?
	`, projectID, key).Scan(
		&value.ProjectID, &value.Key, &value.Kind, &value.Value, &value.Source, &value.Hash, &timestamp,
	)
	if err != nil {
		return memory.Memory{}, fmt.Errorf("load memory: %w", err)
	}
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
		SELECT project_id, key, kind, value, source, content_hash, updated_at
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
		var timestamp int64
		if err := rows.Scan(
			&value.ProjectID, &value.Key, &value.Kind, &value.Value, &value.Source, &value.Hash, &timestamp,
		); err != nil {
			return nil, fmt.Errorf("list memories: scan: %w", err)
		}
		value.UpdatedAt = time.Unix(timestamp, 0).UTC().Format(time.RFC3339)
		values = append(values, value)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("list memories: %w", err)
	}
	return values, nil
}

func (s *Store) SearchMemories(ctx context.Context, projectID, query string, limit int) ([]memory.Memory, error) {
	query = strings.TrimSpace(query)
	if query == "" {
		return nil, errors.New("search memories: query is empty")
	}
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	pattern := "%" + escapeLike(query) + "%"
	rows, err := s.db.QueryContext(ctx, `
		SELECT project_id, key, kind, value, source, content_hash, updated_at
		FROM memories
		WHERE project_id = ? AND (key LIKE ? ESCAPE '\' OR value LIKE ? ESCAPE '\' OR source LIKE ? ESCAPE '\')
		ORDER BY CASE WHEN key = ? THEN 0 WHEN key LIKE ? ESCAPE '\' THEN 1 ELSE 2 END, updated_at DESC, key
		LIMIT ?
	`, projectID, pattern, pattern, pattern, query, escapeLike(query)+"%", limit)
	if err != nil {
		return nil, fmt.Errorf("search memories: %w", err)
	}
	defer rows.Close()
	var values []memory.Memory
	for rows.Next() {
		var value memory.Memory
		var timestamp int64
		if err := rows.Scan(
			&value.ProjectID, &value.Key, &value.Kind, &value.Value, &value.Source, &value.Hash, &timestamp,
		); err != nil {
			return nil, fmt.Errorf("search memories: scan: %w", err)
		}
		value.UpdatedAt = time.Unix(timestamp, 0).UTC().Format(time.RFC3339)
		values = append(values, value)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("search memories: %w", err)
	}
	return values, nil
}
