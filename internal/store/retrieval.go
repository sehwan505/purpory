package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
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

func (s *Store) Credential(ctx context.Context, account string) ([]byte, bool, error) {
	var ciphertext []byte
	err := s.db.QueryRowContext(ctx, `SELECT ciphertext FROM provider_credentials WHERE account = ?`, strings.TrimSpace(account)).Scan(&ciphertext)
	if err == sql.ErrNoRows {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, fmt.Errorf("load credential: %w", err)
	}
	return ciphertext, true, nil
}

func (s *Store) SaveCredential(ctx context.Context, account string, ciphertext []byte) error {
	account = strings.TrimSpace(account)
	if account == "" || len(account) > 255 || len(ciphertext) == 0 || len(ciphertext) > 4_096 {
		return fmt.Errorf("save credential: valid account and ciphertext are required")
	}
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO provider_credentials(account, ciphertext) VALUES (?, ?)
		ON CONFLICT(account) DO UPDATE SET ciphertext=excluded.ciphertext, updated_at=unixepoch()
	`, account, ciphertext)
	if err != nil {
		return fmt.Errorf("save credential: %w", err)
	}
	return nil
}

func (s *Store) DeleteCredential(ctx context.Context, account string) error {
	if _, err := s.db.ExecContext(ctx, `DELETE FROM provider_credentials WHERE account = ?`, strings.TrimSpace(account)); err != nil {
		return fmt.Errorf("delete credential: %w", err)
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
