package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/sehwan505/purpory/internal/memory"
)

type Embedding struct {
	NodeID      string    `json:"nodeId"`
	ContentHash string    `json:"contentHash"`
	Model       string    `json:"model"`
	Vector      []float64 `json:"-"`
}

func (s *Store) Setting(ctx context.Context, key string) (string, bool, error) {
	var value string
	err := s.db.QueryRowContext(ctx, `SELECT value FROM settings WHERE key = ?`, strings.TrimSpace(key)).Scan(&value)
	if err == sql.ErrNoRows {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("load setting: %w", err)
	}
	return value, true, nil
}

func (s *Store) SaveSetting(ctx context.Context, key, value string) error {
	key, value = strings.TrimSpace(key), strings.TrimSpace(value)
	if key == "" || value == "" || len(key) > 255 || len(value) > 1024 {
		return fmt.Errorf("save setting: valid key and value are required")
	}
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO settings(key, value) VALUES (?, ?)
		ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=unixepoch()
	`, key, value)
	if err != nil {
		return fmt.Errorf("save setting: %w", err)
	}
	return nil
}

func (s *Store) MemoryUsage(ctx context.Context, projectID string) (map[string]memory.Usage, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT key, selected_count, expanded_count, last_selected_at, last_expanded_at
		FROM memory_usage WHERE project_id = ?
	`, projectID)
	if err != nil {
		return nil, fmt.Errorf("load memory usage: %w", err)
	}
	defer rows.Close()
	result := map[string]memory.Usage{}
	for rows.Next() {
		var key string
		var item memory.Usage
		var selectedAt, expandedAt sql.NullInt64
		if err := rows.Scan(&key, &item.SelectedCount, &item.ExpandedCount, &selectedAt, &expandedAt); err != nil {
			return nil, fmt.Errorf("load memory usage: scan: %w", err)
		}
		if selectedAt.Valid {
			item.LastSelected = time.Unix(selectedAt.Int64, 0).UTC().Format(time.RFC3339)
		}
		if expandedAt.Valid {
			item.LastExpanded = time.Unix(expandedAt.Int64, 0).UTC().Format(time.RFC3339)
		}
		result[key] = item
	}
	return result, rows.Err()
}

func (s *Store) RecordMemoryUsage(ctx context.Context, projectID, key, action string) error {
	column, timestamp := "", ""
	switch action {
	case "selected":
		column, timestamp = "selected_count", "last_selected_at"
	case "expanded":
		column, timestamp = "expanded_count", "last_expanded_at"
	default:
		return fmt.Errorf("record memory usage: action must be selected or expanded")
	}
	query := fmt.Sprintf(`
		INSERT INTO memory_usage(project_id, key, %s, %s) VALUES (?, ?, 1, unixepoch())
		ON CONFLICT(project_id, key) DO UPDATE SET %s=%s+1, %s=unixepoch()
	`, column, timestamp, column, column, timestamp)
	if _, err := s.db.ExecContext(ctx, query, projectID, key); err != nil {
		return fmt.Errorf("record memory usage: %w", err)
	}
	return nil
}

func (s *Store) Embeddings(ctx context.Context, projectID, model string) ([]Embedding, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT node_id, content_hash, model, vector_json FROM embeddings
		WHERE project_id = ? AND model = ?
	`, projectID, model)
	if err != nil {
		return nil, fmt.Errorf("load embeddings: %w", err)
	}
	defer rows.Close()
	result := []Embedding{}
	for rows.Next() {
		var item Embedding
		var vectorJSON string
		if err := rows.Scan(&item.NodeID, &item.ContentHash, &item.Model, &vectorJSON); err != nil {
			return nil, fmt.Errorf("load embeddings: scan: %w", err)
		}
		if err := json.Unmarshal([]byte(vectorJSON), &item.Vector); err != nil {
			return nil, fmt.Errorf("load embeddings: decode: %w", err)
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func (s *Store) SaveEmbedding(ctx context.Context, projectID, nodeID, contentHash, model string, vector []float64) error {
	if strings.TrimSpace(nodeID) == "" || strings.TrimSpace(contentHash) == "" || strings.TrimSpace(model) == "" || len(vector) == 0 {
		return fmt.Errorf("save embedding: node, hash, model, and vector are required")
	}
	encoded, err := json.Marshal(vector)
	if err != nil {
		return fmt.Errorf("save embedding: encode: %w", err)
	}
	_, err = s.db.ExecContext(ctx, `
		INSERT INTO embeddings(project_id, node_id, content_hash, model, dimensions, vector_json)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(project_id, node_id, model) DO UPDATE SET
			content_hash=excluded.content_hash, dimensions=excluded.dimensions,
			vector_json=excluded.vector_json, updated_at=unixepoch()
	`, projectID, nodeID, contentHash, model, len(vector), string(encoded))
	if err != nil {
		return fmt.Errorf("save embedding: %w", err)
	}
	return nil
}
