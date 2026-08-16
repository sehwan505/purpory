package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/sehwan505/purpory/internal/memory"
)

func (s *Store) DeleteMemory(ctx context.Context, projectID, key string) (bool, error) {
	key, err := memory.ValidateKey(key)
	if err != nil {
		return false, err
	}
	result, err := s.db.ExecContext(ctx, `DELETE FROM memories WHERE project_id = ? AND key = ?`, projectID, key)
	if err != nil {
		return false, fmt.Errorf("delete memory: %w", err)
	}
	changed, err := result.RowsAffected()
	return changed > 0, err
}

func (s *Store) ConfirmMemory(ctx context.Context, projectID, key string) (bool, error) {
	key, err := memory.ValidateKey(key)
	if err != nil {
		return false, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return false, fmt.Errorf("confirm memory: begin: %w", err)
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `UPDATE memories SET updated_at=unixepoch() WHERE project_id = ? AND key = ?`, projectID, key)
	if err != nil {
		return false, fmt.Errorf("confirm memory: %w", err)
	}
	changed, err := result.RowsAffected()
	if err != nil {
		return false, fmt.Errorf("confirm memory: result: %w", err)
	}
	if changed > 0 {
		if _, err := tx.ExecContext(ctx, `
			UPDATE needs_reviews SET status='resolved', outcome='keep', resolved_at=unixepoch()
			WHERE project_id = ? AND key = ? AND status = 'open'
		`, projectID, key); err != nil {
			return false, fmt.Errorf("confirm memory: resolve reviews: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return false, fmt.Errorf("confirm memory: commit: %w", err)
	}
	return changed > 0, nil
}

func (s *Store) PreviewMemoryBatch(ctx context.Context, projectID string, changes []memory.BatchChange, apply bool, sessionID string) (memory.BatchResult, error) {
	if len(changes) == 0 || len(changes) > 20 {
		return memory.BatchResult{}, errors.New("reconcile memory batch: 1-20 changes are required")
	}
	prepared := make([]memory.Memory, len(changes))
	current := make([]*memory.Memory, len(changes))
	seen := map[string]bool{}
	result := memory.BatchResult{ProjectID: projectID, Changes: make([]memory.BatchItem, len(changes))}
	conflict := false
	for index, change := range changes {
		if change.Kind == "" {
			change.Kind = memory.Note
		}
		entry, err := memory.New(projectID, change.Key, change.Kind, change.Value, change.Source)
		if err != nil {
			return memory.BatchResult{}, err
		}
		if seen[entry.Key] {
			return memory.BatchResult{}, fmt.Errorf("reconcile memory batch: duplicate key %q", entry.Key)
		}
		seen[entry.Key] = true
		prepared[index] = entry
		stored, err := s.Memory(ctx, projectID, entry.Key)
		if err == nil {
			current[index] = &stored
		} else if !errors.Is(err, sql.ErrNoRows) {
			return memory.BatchResult{}, err
		}
		var currentHash *string
		action := "created"
		if current[index] != nil {
			hash := current[index].Hash
			currentHash = &hash
			action = "updated"
			if hash == entry.Hash {
				action = "unchanged"
			}
		}
		if apply && (!change.ExpectedHashSet || !sameOptionalString(change.ExpectedHash, currentHash)) {
			action = "conflict"
			conflict = true
		}
		result.Changes[index] = memory.BatchItem{Key: entry.Key, Action: action, CurrentHash: currentHash, ProposedHash: entry.Hash, ExpectedHash: currentHash}
	}
	if !apply || conflict {
		return result, nil
	}
	proposals := make([]MemoryProposal, len(prepared))
	for index, entry := range prepared {
		proposals[index] = MemoryProposal{Memory: entry, ExpectedHash: changes[index].ExpectedHash}
	}
	results, err := s.ReconcileMemories(ctx, sessionID, proposals)
	if err != nil {
		return memory.BatchResult{}, err
	}
	result.Applied = true
	for index := range results {
		result.Changes[index].Action = results[index].Action
		result.Changes[index].VersionID = results[index].VersionID
	}
	return result, nil
}

func sameOptionalString(left, right *string) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return *left == *right
}

func (s *Store) CreateNeedsReview(ctx context.Context, projectID, key, sourceType, sourceID, contentHash, reason string) (memory.Review, error) {
	key, err := memory.ValidateKey(key)
	if err != nil {
		return memory.Review{}, err
	}
	sourceType = strings.TrimSpace(sourceType)
	sourceID = strings.TrimSpace(sourceID)
	contentHash = strings.TrimSpace(contentHash)
	reason = strings.TrimSpace(reason)
	if sourceType == "" || sourceID == "" || contentHash == "" || reason == "" {
		return memory.Review{}, errors.New("create needs review: source type, source ID, content hash, and reason are required")
	}
	if len(sourceType) > 64 || len(sourceID) > 1024 || len(contentHash) > 128 || len(reason) > 4096 {
		return memory.Review{}, errors.New("create needs review: field exceeds its limit")
	}
	var exists int
	if err := s.db.QueryRowContext(ctx, `SELECT count(*) FROM memories WHERE project_id = ? AND key = ?`, projectID, key).Scan(&exists); err != nil {
		return memory.Review{}, fmt.Errorf("create needs review: load memory: %w", err)
	}
	if exists == 0 {
		return memory.Review{}, fmt.Errorf("create needs review: memory %q does not exist", key)
	}
	if _, err := s.db.ExecContext(ctx, `
		INSERT INTO needs_reviews(project_id, key, source_type, source_id, content_hash, reason)
		VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, key, source_type, source_id, content_hash) DO NOTHING
	`, projectID, key, sourceType, sourceID, contentHash, reason); err != nil {
		return memory.Review{}, fmt.Errorf("create needs review: %w", err)
	}
	var id int64
	if err := s.db.QueryRowContext(ctx, `
		SELECT id FROM needs_reviews WHERE project_id = ? AND key = ? AND source_type = ? AND source_id = ? AND content_hash = ?
	`, projectID, key, sourceType, sourceID, contentHash).Scan(&id); err != nil {
		return memory.Review{}, fmt.Errorf("create needs review: load: %w", err)
	}
	return s.needsReview(ctx, projectID, id)
}

func (s *Store) NeedsReviews(ctx context.Context, projectID, status string) ([]memory.Review, error) {
	status = strings.ToLower(strings.TrimSpace(status))
	if status != "" && status != "open" && status != "resolved" {
		return nil, errors.New("list needs reviews: status must be open or resolved")
	}
	query := `SELECT id, project_id, key, status, source_type, source_id, content_hash, reason, outcome, result_version_id, created_at, resolved_at FROM needs_reviews WHERE project_id = ?`
	arguments := []any{projectID}
	if status != "" {
		query += " AND status = ?"
		arguments = append(arguments, status)
	}
	query += " ORDER BY created_at DESC, id DESC"
	rows, err := s.db.QueryContext(ctx, query, arguments...)
	if err != nil {
		return nil, fmt.Errorf("list needs reviews: %w", err)
	}
	defer rows.Close()
	result := []memory.Review{}
	for rows.Next() {
		item, err := scanReview(rows)
		if err != nil {
			return nil, fmt.Errorf("list needs reviews: scan: %w", err)
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func (s *Store) ResolveNeedsReview(ctx context.Context, projectID string, reviewID int64, outcome string, resultVersionID *int64) (*memory.Review, error) {
	outcome = strings.ToLower(strings.TrimSpace(outcome))
	if outcome != "keep" && outcome != "change" {
		return nil, errors.New("resolve needs review: outcome must be keep or change")
	}
	if outcome == "change" && resultVersionID == nil {
		return nil, errors.New("resolve needs review: change requires a result version")
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("resolve needs review: begin: %w", err)
	}
	defer tx.Rollback()
	var key string
	if err := tx.QueryRowContext(ctx, `SELECT key FROM needs_reviews WHERE project_id = ? AND id = ? AND status = 'open'`, projectID, reviewID).Scan(&key); errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	} else if err != nil {
		return nil, fmt.Errorf("resolve needs review: load: %w", err)
	}
	if resultVersionID != nil {
		var count int
		if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM memory_versions WHERE id = ? AND project_id = ? AND key = ?`, *resultVersionID, projectID, key).Scan(&count); err != nil {
			return nil, fmt.Errorf("resolve needs review: load version: %w", err)
		}
		if count == 0 {
			return nil, errors.New("resolve needs review: result version does not match memory")
		}
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE needs_reviews SET status='resolved', outcome=?, result_version_id=?, resolved_at=unixepoch()
		WHERE project_id = ? AND id = ? AND status = 'open'
	`, outcome, resultVersionID, projectID, reviewID); err != nil {
		return nil, fmt.Errorf("resolve needs review: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("resolve needs review: commit: %w", err)
	}
	item, err := s.needsReview(ctx, projectID, reviewID)
	return &item, err
}

func (s *Store) needsReview(ctx context.Context, projectID string, id int64) (memory.Review, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, project_id, key, status, source_type, source_id, content_hash, reason, outcome, result_version_id, created_at, resolved_at
		FROM needs_reviews WHERE project_id = ? AND id = ?
	`, projectID, id)
	return scanReview(row)
}

type reviewScanner interface{ Scan(...any) error }

func scanReview(row reviewScanner) (memory.Review, error) {
	var item memory.Review
	var outcome sql.NullString
	var resultVersion sql.NullInt64
	var createdAt int64
	var resolvedAt sql.NullInt64
	if err := row.Scan(&item.ID, &item.ProjectID, &item.Key, &item.Status, &item.SourceType, &item.SourceID, &item.ContentHash, &item.Reason, &outcome, &resultVersion, &createdAt, &resolvedAt); err != nil {
		return memory.Review{}, err
	}
	if outcome.Valid {
		item.Outcome = outcome.String
	}
	if resultVersion.Valid {
		item.ResultVersionID = &resultVersion.Int64
	}
	item.CreatedAt = time.Unix(createdAt, 0).UTC().Format(time.RFC3339)
	if resolvedAt.Valid {
		item.ResolvedAt = time.Unix(resolvedAt.Int64, 0).UTC().Format(time.RFC3339)
	}
	return item, nil
}
