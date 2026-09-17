package store

import (
	"context"
	"fmt"
)

type MaintenanceResult struct {
	OrphanEmbeddings           int64 `json:"orphanEmbeddings"`
	PrunedMemoryVersions       int64 `json:"prunedMemoryVersions"`
	PrunedReconciliationEvents int64 `json:"prunedReconciliationEvents"`
	PrunedNavigationEvents     int64 `json:"prunedNavigationEvents"`
	BytesBefore                int64 `json:"bytesBefore"`
	BytesAfter                 int64 `json:"bytesAfter"`
	BytesReclaimed             int64 `json:"bytesReclaimed"`
}

// Maintain bounds repeated history, removes orphan vectors, and compacts SQLite.
func (s *Store) Maintain(ctx context.Context, keep int) (MaintenanceResult, error) {
	if keep <= 0 {
		return MaintenanceResult{}, fmt.Errorf("maintain store: keep must be positive")
	}
	before, err := s.databaseBytes(ctx)
	if err != nil {
		return MaintenanceResult{}, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return MaintenanceResult{}, fmt.Errorf("maintain store: begin: %w", err)
	}
	defer tx.Rollback()
	result := MaintenanceResult{BytesBefore: before}
	remove := func(name, query string, args ...any) (int64, error) {
		deleted, err := tx.ExecContext(ctx, query, args...)
		if err != nil {
			return 0, fmt.Errorf("maintain store: remove %s: %w", name, err)
		}
		count, err := deleted.RowsAffected()
		if err != nil {
			return 0, fmt.Errorf("maintain store: count %s: %w", name, err)
		}
		return count, nil
	}
	result.OrphanEmbeddings, err = remove("orphan embeddings", `
			DELETE FROM embeddings
			WHERE NOT EXISTS (
				SELECT 1 FROM nodes
				WHERE nodes.project_id = embeddings.project_id AND nodes.id = embeddings.node_id
			)
		`)
	if err != nil {
		return MaintenanceResult{}, err
	}
	result.PrunedMemoryVersions, err = remove("memory versions", `
			DELETE FROM memory_versions WHERE id IN (
				SELECT id FROM (
					SELECT id,
						row_number() OVER (PARTITION BY project_id, key ORDER BY id DESC) AS newest,
						row_number() OVER (PARTITION BY project_id, key ORDER BY id ASC) AS oldest
					FROM memory_versions
				) WHERE newest > ? AND oldest > 1
			) AND id NOT IN (
				SELECT result_version_id FROM needs_reviews WHERE result_version_id IS NOT NULL
			)
		`, keep-1)
	if err != nil {
		return MaintenanceResult{}, err
	}
	result.PrunedReconciliationEvents, err = remove("reconciliation events", `
			DELETE FROM reconciliation_events WHERE id IN (
				SELECT id FROM (
					SELECT id, row_number() OVER (PARTITION BY project_id ORDER BY id DESC) AS position
					FROM reconciliation_events
				) WHERE position > ?
			)
		`, keep)
	if err != nil {
		return MaintenanceResult{}, err
	}
	result.PrunedNavigationEvents, err = remove("navigation events", `
			DELETE FROM navigation_events WHERE id IN (
				SELECT id FROM (
					SELECT id, row_number() OVER (PARTITION BY project_id, session_id ORDER BY id DESC) AS position
					FROM navigation_events
				) WHERE position > ?
			)
		`, keep)
	if err != nil {
		return MaintenanceResult{}, err
	}
	if err := tx.Commit(); err != nil {
		return MaintenanceResult{}, fmt.Errorf("maintain store: commit: %w", err)
	}
	if _, err := s.db.ExecContext(ctx, "VACUUM"); err != nil {
		return result, fmt.Errorf("maintain store: vacuum: %w", err)
	}
	after, err := s.databaseBytes(ctx)
	if err != nil {
		return result, err
	}
	result.BytesAfter = after
	if before > after {
		result.BytesReclaimed = before - after
	}
	return result, nil
}

func (s *Store) databaseBytes(ctx context.Context) (int64, error) {
	var pages, pageSize int64
	if err := s.db.QueryRowContext(ctx, "PRAGMA page_count").Scan(&pages); err != nil {
		return 0, fmt.Errorf("maintain store: count pages: %w", err)
	}
	if err := s.db.QueryRowContext(ctx, "PRAGMA page_size").Scan(&pageSize); err != nil {
		return 0, fmt.Errorf("maintain store: load page size: %w", err)
	}
	return pages * pageSize, nil
}
