package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/prepare"
)

func (s *Store) SessionItemKeys(ctx context.Context, projectID, sessionID string) (map[string]bool, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT DISTINCT key FROM session_items WHERE project_id = ? AND session_id = ?
	`, projectID, sessionID)
	if err != nil {
		return nil, fmt.Errorf("load session items: %w", err)
	}
	defer rows.Close()
	result := map[string]bool{}
	for rows.Next() {
		var key string
		if err := rows.Scan(&key); err != nil {
			return nil, fmt.Errorf("load session items: scan: %w", err)
		}
		result[key] = true
	}
	return result, rows.Err()
}

func (s *Store) OpenContextRequestCount(ctx context.Context, projectID string) (int, error) {
	var count int
	err := s.db.QueryRowContext(ctx, `SELECT count(*) FROM context_requests WHERE project_id = ? AND status = 'open'`, projectID).Scan(&count)
	return count, err
}

func (s *Store) EnsureContextRequest(ctx context.Context, projectID, sessionID, need string) (int64, error) {
	need = strings.TrimSpace(need)
	if need == "" {
		return 0, errors.New("save context request: need is required")
	}
	if _, err := s.db.ExecContext(ctx, `
		INSERT INTO context_requests(project_id, session_id, need) VALUES (?, ?, ?)
		ON CONFLICT(project_id, session_id, need) DO UPDATE SET
			status='open', resolved_key=NULL, resolved_at=NULL, updated_at=unixepoch()
	`, projectID, sessionID, need); err != nil {
		return 0, fmt.Errorf("save context request: %w", err)
	}
	var id int64
	if err := s.db.QueryRowContext(ctx, `SELECT id FROM context_requests WHERE project_id = ? AND session_id = ? AND need = ?`, projectID, sessionID, need).Scan(&id); err != nil {
		return 0, fmt.Errorf("save context request: load ID: %w", err)
	}
	return id, nil
}

func (s *Store) ContextRequests(ctx context.Context, projectID, status string) ([]prepare.ContextRequest, error) {
	status = strings.ToLower(strings.TrimSpace(status))
	if status != "" && status != "open" && status != "resolved" {
		return nil, errors.New("list context requests: status must be open or resolved")
	}
	query := `SELECT id, session_id, project_id, need, status, resolved_key, created_at, resolved_at FROM context_requests WHERE project_id = ?`
	arguments := []any{projectID}
	if status != "" {
		query += " AND status = ?"
		arguments = append(arguments, status)
	}
	query += " ORDER BY created_at DESC, id DESC"
	rows, err := s.db.QueryContext(ctx, query, arguments...)
	if err != nil {
		return nil, fmt.Errorf("list context requests: %w", err)
	}
	defer rows.Close()
	result := []prepare.ContextRequest{}
	for rows.Next() {
		var item prepare.ContextRequest
		var resolvedKey sql.NullString
		var createdAt int64
		var resolvedAt sql.NullInt64
		if err := rows.Scan(&item.ID, &item.SessionID, &item.ProjectID, &item.Need, &item.Status, &resolvedKey, &createdAt, &resolvedAt); err != nil {
			return nil, fmt.Errorf("list context requests: scan: %w", err)
		}
		if resolvedKey.Valid {
			item.ResolvedKey = &resolvedKey.String
		}
		item.CreatedAt = time.Unix(createdAt, 0).UTC().Format(time.RFC3339)
		if resolvedAt.Valid {
			item.ResolvedAt = time.Unix(resolvedAt.Int64, 0).UTC().Format(time.RFC3339)
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func (s *Store) ResolveContextRequest(ctx context.Context, projectID string, requestID int64, key string) (bool, error) {
	key, err := memory.ValidateKey(key)
	if err != nil {
		return false, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return false, fmt.Errorf("resolve context request: begin: %w", err)
	}
	defer tx.Rollback()
	var count int
	if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM memories WHERE project_id = ? AND key = ?`, projectID, key).Scan(&count); err != nil {
		return false, fmt.Errorf("resolve context request: load memory: %w", err)
	}
	if count == 0 {
		return false, fmt.Errorf("resolve context request: memory %q does not exist", key)
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE context_requests SET status='resolved', resolved_key=?, resolved_at=unixepoch(), updated_at=unixepoch()
		WHERE project_id = ? AND id = ? AND status = 'open'
	`, key, projectID, requestID)
	if err != nil {
		return false, fmt.Errorf("resolve context request: %w", err)
	}
	changed, err := result.RowsAffected()
	if err != nil {
		return false, fmt.Errorf("resolve context request: result: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return false, fmt.Errorf("resolve context request: commit: %w", err)
	}
	return changed > 0, nil
}

func (s *Store) SavePrepareDecision(ctx context.Context, record prepare.DecisionRecord) (int64, error) {
	proposal, err := json.Marshal(record.Proposal)
	if err != nil {
		return 0, fmt.Errorf("save prepare decision: encode proposal: %w", err)
	}
	hints, err := json.Marshal(record.Hints)
	if err != nil {
		return 0, fmt.Errorf("save prepare decision: encode hints: %w", err)
	}
	result, err := s.db.ExecContext(ctx, `
		INSERT INTO context_decisions(
			project_id, session_id, input_hash, input_text, proposal_json, final_action,
			hints_json, request_id, model_id, model_revision, prompt_version, latency_ms, fallback_reason
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, record.ProjectID, record.SessionID, record.InputHash, record.InputText, string(proposal), record.Action,
		string(hints), record.RequestID, record.Model.ID, record.Model.Revision, prepare.PromptVersion, record.Model.LatencyMS, record.Fallback)
	if err != nil {
		return 0, fmt.Errorf("save prepare decision: %w", err)
	}
	id, err := result.LastInsertId()
	if err != nil {
		return 0, fmt.Errorf("save prepare decision: ID: %w", err)
	}
	return id, nil
}

func (s *Store) PrepareDecisions(ctx context.Context, projectID string, limit int) ([]prepare.Decision, error) {
	if limit <= 0 || limit > 1000 {
		limit = 100
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT d.id, d.session_id, d.project_id, d.input_hash, d.input_text, d.final_action,
		       d.proposal_json, d.hints_json, d.request_id, d.model_id, d.model_revision,
		       d.prompt_version, d.latency_ms, d.fallback_reason, d.created_at,
		       f.verdict, f.expected_action, f.expected_keys_json, f.note, f.created_at
		FROM context_decisions d LEFT JOIN gate_feedback f ON f.decision_id = d.id
		WHERE d.project_id = ? ORDER BY d.created_at DESC, d.id DESC LIMIT ?
	`, projectID, limit)
	if err != nil {
		return nil, fmt.Errorf("load prepare decisions: %w", err)
	}
	defer rows.Close()
	result := []prepare.Decision{}
	for rows.Next() {
		var value prepare.Decision
		var input sql.NullString
		var proposalJSON, hintsJSON string
		var requestID, latency, createdAt sql.NullInt64
		var modelID, modelRevision, fallback sql.NullString
		var verdict, expectedAction, expectedKeys, note sql.NullString
		var feedbackAt sql.NullInt64
		if err := rows.Scan(&value.ID, &value.SessionID, &value.ProjectID, &value.InputHash, &input, &value.FinalAction,
			&proposalJSON, &hintsJSON, &requestID, &modelID, &modelRevision, &value.PromptVersion, &latency, &fallback, &createdAt,
			&verdict, &expectedAction, &expectedKeys, &note, &feedbackAt); err != nil {
			return nil, fmt.Errorf("load prepare decisions: scan: %w", err)
		}
		if input.Valid {
			value.InputText = &input.String
		}
		if err := json.Unmarshal([]byte(proposalJSON), &value.Proposal); err != nil {
			return nil, fmt.Errorf("load prepare decisions: decode proposal: %w", err)
		}
		if err := json.Unmarshal([]byte(hintsJSON), &value.Hints); err != nil {
			return nil, fmt.Errorf("load prepare decisions: decode hints: %w", err)
		}
		if requestID.Valid {
			value.RequestID = &requestID.Int64
		}
		if modelID.Valid {
			value.ModelID = &modelID.String
		}
		if modelRevision.Valid {
			value.ModelRevision = &modelRevision.String
		}
		if latency.Valid {
			parsed := int(latency.Int64)
			value.LatencyMS = &parsed
		}
		if fallback.Valid {
			value.Fallback = &fallback.String
		}
		value.CreatedAt = time.Unix(createdAt.Int64, 0).UTC().Format(time.RFC3339)
		if verdict.Valid {
			feedback := prepare.Feedback{DecisionID: value.ID, Verdict: verdict.String, ExpectedKeys: []string{}}
			if expectedAction.Valid {
				feedback.ExpectedAction = &expectedAction.String
			}
			if expectedKeys.Valid {
				if err := json.Unmarshal([]byte(expectedKeys.String), &feedback.ExpectedKeys); err != nil {
					return nil, fmt.Errorf("load prepare decisions: decode feedback keys: %w", err)
				}
			}
			if note.Valid {
				feedback.Note = &note.String
			}
			if feedbackAt.Valid {
				feedback.CreatedAt = time.Unix(feedbackAt.Int64, 0).UTC().Format(time.RFC3339)
			}
			value.Feedback = &feedback
		}
		result = append(result, value)
	}
	return result, rows.Err()
}

func (s *Store) SavePrepareFeedback(ctx context.Context, projectID string, feedback prepare.Feedback) (prepare.Feedback, error) {
	feedback.Verdict = strings.ToLower(strings.TrimSpace(feedback.Verdict))
	if feedback.Verdict != "correct" && feedback.Verdict != "incorrect" {
		return prepare.Feedback{}, errors.New("save prepare feedback: verdict must be correct or incorrect")
	}
	if feedback.ExpectedAction != nil {
		action := strings.ToLower(strings.TrimSpace(*feedback.ExpectedAction))
		if action != "skip" && action != "retrieve" && action != "ask" {
			return prepare.Feedback{}, errors.New("save prepare feedback: expected action must be skip, retrieve, or ask")
		}
		feedback.ExpectedAction = &action
	}
	if feedback.Verdict == "incorrect" && feedback.ExpectedAction == nil {
		return prepare.Feedback{}, errors.New("save prepare feedback: incorrect verdict requires expected action")
	}
	seen := map[string]bool{}
	keys := make([]string, 0, len(feedback.ExpectedKeys))
	for _, raw := range feedback.ExpectedKeys {
		key, err := memory.ValidateKey(raw)
		if err != nil {
			return prepare.Feedback{}, err
		}
		if !seen[key] {
			seen[key] = true
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	feedback.ExpectedKeys = keys
	if feedback.Note != nil {
		note := strings.TrimSpace(*feedback.Note)
		if len(note) > 4096 {
			return prepare.Feedback{}, errors.New("save prepare feedback: note exceeds 4096 characters")
		}
		if note == "" {
			feedback.Note = nil
		} else {
			feedback.Note = &note
		}
	}
	encoded, err := json.Marshal(feedback.ExpectedKeys)
	if err != nil {
		return prepare.Feedback{}, fmt.Errorf("save prepare feedback: encode keys: %w", err)
	}
	result, err := s.db.ExecContext(ctx, `
		INSERT INTO gate_feedback(decision_id, verdict, expected_action, expected_keys_json, note)
		SELECT id, ?, ?, ?, ? FROM context_decisions WHERE id = ? AND project_id = ?
		ON CONFLICT(decision_id) DO UPDATE SET verdict=excluded.verdict, expected_action=excluded.expected_action,
			expected_keys_json=excluded.expected_keys_json, note=excluded.note, created_at=unixepoch()
	`, feedback.Verdict, feedback.ExpectedAction, string(encoded), feedback.Note, feedback.DecisionID, projectID)
	if err != nil {
		return prepare.Feedback{}, fmt.Errorf("save prepare feedback: %w", err)
	}
	changed, err := result.RowsAffected()
	if err != nil || changed == 0 {
		if err != nil {
			return prepare.Feedback{}, fmt.Errorf("save prepare feedback: result: %w", err)
		}
		return prepare.Feedback{}, errors.New("save prepare feedback: decision does not exist")
	}
	feedback.CreatedAt = time.Now().UTC().Format(time.RFC3339)
	return feedback, nil
}
