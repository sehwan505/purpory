package store

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/sehwan505/purpory/internal/prepare"
)

func (s *Store) SessionItemKeys(ctx context.Context, projectID, sessionID string) ([]string, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT key FROM session_items WHERE project_id = ? AND session_id = ?
		GROUP BY key ORDER BY max(delivered_at) DESC, key
	`, projectID, sessionID)
	if err != nil {
		return nil, fmt.Errorf("load session items: %w", err)
	}
	defer rows.Close()
	var result []string
	for rows.Next() {
		var key string
		if err := rows.Scan(&key); err != nil {
			return nil, fmt.Errorf("load session items: scan: %w", err)
		}
		result = append(result, key)
	}
	return result, rows.Err()
}

func (s *Store) AppendNavigation(ctx context.Context, projectID, sessionID string, events []prepare.NavigationEvent) error {
	if strings.TrimSpace(projectID) == "" || strings.TrimSpace(sessionID) == "" {
		return errors.New("save navigation: project and session are required")
	}
	if len(events) == 0 {
		return nil
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("save navigation: begin: %w", err)
	}
	defer tx.Rollback()
	for _, event := range events {
		if (event.Action != "query" && event.Action != "explain" && event.Action != "path" && event.Action != "deliver") ||
			strings.TrimSpace(event.SourceNodeID) == "" && strings.TrimSpace(event.TargetNodeID) == "" {
			return errors.New("save navigation: valid action and node are required")
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO navigation_events(project_id, session_id, action, source_node_id, target_node_id)
			VALUES (?, ?, ?, ?, ?)
		`, projectID, sessionID, event.Action, event.SourceNodeID, event.TargetNodeID); err != nil {
			return fmt.Errorf("save navigation: insert: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("save navigation: commit: %w", err)
	}
	return nil
}

func (s *Store) Navigation(ctx context.Context, projectID, sessionID string, limit int) ([]prepare.NavigationEvent, error) {
	if limit <= 0 || limit > 100 {
		limit = 8
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, action, source_node_id, target_node_id, created_at
		FROM navigation_events WHERE project_id = ? AND session_id = ?
		ORDER BY id DESC LIMIT ?
	`, projectID, sessionID, limit)
	if err != nil {
		return nil, fmt.Errorf("load navigation: %w", err)
	}
	defer rows.Close()
	var result []prepare.NavigationEvent
	for rows.Next() {
		var event prepare.NavigationEvent
		var createdAt int64
		if err := rows.Scan(&event.ID, &event.Action, &event.SourceNodeID, &event.TargetNodeID, &createdAt); err != nil {
			return nil, fmt.Errorf("load navigation: scan: %w", err)
		}
		event.CreatedAt = time.Unix(createdAt, 0).UTC().Format(time.RFC3339)
		result = append(result, event)
	}
	return result, rows.Err()
}
