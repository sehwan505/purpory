package store

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
)

type AgentChange struct {
	ID        int64           `json:"id"`
	SessionID string          `json:"sessionId"`
	Entity    string          `json:"entity"`
	EntityKey string          `json:"entityKey"`
	Action    string          `json:"action"`
	Before    json.RawMessage `json:"before"`
	After     json.RawMessage `json:"after"`
	CreatedAt string          `json:"createdAt"`
}

type knowledgeSnapshot struct {
	Memory     memory.Memory `json:"memory"`
	Provenance string        `json:"provenance"`
}

type edgeSnapshot struct {
	SourceID   string `json:"sourceId"`
	TargetID   string `json:"targetId"`
	Relation   string `json:"relation"`
	Owner      string `json:"owner"`
	Provenance string `json:"provenance"`
	State      string `json:"state"`
}

func (s *Store) SetExploration(ctx context.Context, projectID, sessionID string, enabled bool) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO exploration_modes(project_id, session_id, enabled) VALUES (?, ?, ?)
		ON CONFLICT(project_id, session_id) DO UPDATE SET enabled=excluded.enabled, updated_at=unixepoch()
	`, projectID, sessionID, enabled)
	if err != nil {
		return fmt.Errorf("set exploration mode: %w", err)
	}
	return nil
}

func (s *Store) ExplorationEnabled(ctx context.Context, projectID, sessionID string) (bool, error) {
	var enabled bool
	err := s.db.QueryRowContext(ctx, `
		SELECT COALESCE((SELECT enabled FROM exploration_modes WHERE project_id = ? AND session_id = ?), 0)
	`, projectID, sessionID).Scan(&enabled)
	if err != nil {
		return false, fmt.Errorf("load exploration mode: %w", err)
	}
	return enabled, nil
}

func (s *Store) AgentChangeCount(ctx context.Context, projectID string) (int, error) {
	var count int
	if err := s.db.QueryRowContext(ctx, `SELECT count(*) FROM agent_change_log WHERE project_id = ?`, projectID).Scan(&count); err != nil {
		return 0, fmt.Errorf("count agent changes: %w", err)
	}
	return count, nil
}

func (s *Store) AgentChanges(ctx context.Context, projectID string, limit int) ([]AgentChange, error) {
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, session_id, entity, entity_key, action, before_json, after_json, created_at
		FROM agent_change_log WHERE project_id = ? ORDER BY id DESC LIMIT ?
	`, projectID, limit)
	if err != nil {
		return nil, fmt.Errorf("list agent changes: %w", err)
	}
	defer rows.Close()
	changes := []AgentChange{}
	for rows.Next() {
		var change AgentChange
		var before, after string
		var created int64
		if err := rows.Scan(&change.ID, &change.SessionID, &change.Entity, &change.EntityKey, &change.Action, &before, &after, &created); err != nil {
			return nil, fmt.Errorf("list agent changes: scan: %w", err)
		}
		change.Before, change.After = json.RawMessage(before), json.RawMessage(after)
		change.CreatedAt = time.Unix(created, 0).UTC().Format(time.RFC3339)
		changes = append(changes, change)
	}
	return changes, rows.Err()
}

func (s *Store) SetAgentKnowledge(ctx context.Context, projectID, sessionID, provenance string, entry memory.Memory) (AgentChange, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return AgentChange{}, fmt.Errorf("set agent knowledge: begin: %w", err)
	}
	defer tx.Rollback()
	before, err := loadKnowledgeSnapshot(ctx, tx, projectID, entry.Key)
	if err != nil {
		return AgentChange{}, err
	}
	if before != nil && before.Memory.Kind != entry.Kind {
		return AgentChange{}, fmt.Errorf("set agent knowledge %q: existing memory has kind %q", entry.Key, before.Memory.Kind)
	}
	if before != nil && before.Memory.Hash == entry.Hash && before.Provenance == provenance {
		return AgentChange{Action: "unchanged"}, tx.Commit()
	}
	if _, err := saveMemory(ctx, tx, entry); err != nil {
		return AgentChange{}, err
	}
	if _, err := tx.ExecContext(ctx, `UPDATE nodes SET provenance = ? WHERE project_id = ? AND id = ?`, provenance, projectID, graph.ReferenceID(graph.KindKnowledge, entry.Key)); err != nil {
		return AgentChange{}, fmt.Errorf("set agent knowledge: provenance: %w", err)
	}
	after, err := loadKnowledgeSnapshot(ctx, tx, projectID, entry.Key)
	if err != nil {
		return AgentChange{}, err
	}
	change, err := appendAgentChange(ctx, tx, projectID, sessionID, "knowledge", entry.Key, "set", before, after)
	if err != nil {
		return AgentChange{}, err
	}
	if err := tx.Commit(); err != nil {
		return AgentChange{}, fmt.Errorf("set agent knowledge: commit: %w", err)
	}
	return change, nil
}

func (s *Store) DeleteAgentKnowledge(ctx context.Context, projectID, sessionID, key string) (AgentChange, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return AgentChange{}, fmt.Errorf("delete agent knowledge: begin: %w", err)
	}
	defer tx.Rollback()
	before, err := loadKnowledgeSnapshot(ctx, tx, projectID, key)
	if err != nil {
		return AgentChange{}, err
	}
	if before == nil || before.Memory.Kind != memory.Note {
		return AgentChange{}, fmt.Errorf("delete agent knowledge %q: not found", key)
	}
	edges, err := incidentEdges(ctx, tx, projectID, graph.ReferenceID(graph.KindKnowledge, key))
	if err != nil {
		return AgentChange{}, err
	}
	for _, edge := range edges {
		if _, err := appendAgentChange(ctx, tx, projectID, sessionID, "edge", edgeKey(edge), "unlink", edge, nil); err != nil {
			return AgentChange{}, err
		}
	}
	deleted, err := deleteMemory(ctx, tx, projectID, key)
	if err != nil {
		return AgentChange{}, err
	}
	if !deleted {
		return AgentChange{}, fmt.Errorf("delete agent knowledge %q: disappeared during transaction", key)
	}
	change, err := appendAgentChange(ctx, tx, projectID, sessionID, "knowledge", key, "delete", before, nil)
	if err != nil {
		return AgentChange{}, err
	}
	if err := tx.Commit(); err != nil {
		return AgentChange{}, fmt.Errorf("delete agent knowledge: commit: %w", err)
	}
	return change, nil
}

func (s *Store) SetAgentEdge(ctx context.Context, projectID, sessionID string, edge graph.Edge) (AgentChange, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return AgentChange{}, fmt.Errorf("set agent edge: begin: %w", err)
	}
	defer tx.Rollback()
	before, err := loadEdgeSnapshot(ctx, tx, projectID, edge.SourceID, edge.TargetID, edge.Relation)
	if err != nil {
		return AgentChange{}, err
	}
	desired := edgeSnapshot{
		SourceID: edge.SourceID, TargetID: edge.TargetID, Relation: edge.Relation,
		Owner: graph.OwnerDurable, Provenance: edge.Provenance, State: graph.StateActive,
	}
	if before != nil && *before == desired {
		return AgentChange{Action: "unchanged"}, tx.Commit()
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO edges(project_id, source_id, target_id, relation, owner, provenance, state, created_at)
		VALUES (?, ?, ?, ?, 'durable', ?, 'active', unixepoch())
		ON CONFLICT(project_id, source_id, target_id, relation) DO UPDATE SET
		owner='durable', provenance=excluded.provenance, state='active', retired_at=NULL
	`, projectID, edge.SourceID, edge.TargetID, edge.Relation, edge.Provenance); err != nil {
		return AgentChange{}, fmt.Errorf("set agent edge: save: %w", err)
	}
	after, err := loadEdgeSnapshot(ctx, tx, projectID, edge.SourceID, edge.TargetID, edge.Relation)
	if err != nil {
		return AgentChange{}, err
	}
	change, err := appendAgentChange(ctx, tx, projectID, sessionID, "edge", edgeKey(*after), "link", before, after)
	if err != nil {
		return AgentChange{}, err
	}
	if err := tx.Commit(); err != nil {
		return AgentChange{}, fmt.Errorf("set agent edge: commit: %w", err)
	}
	return change, nil
}

func (s *Store) DeleteAgentEdge(ctx context.Context, projectID, sessionID string, edge graph.Edge) (AgentChange, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return AgentChange{}, fmt.Errorf("delete agent edge: begin: %w", err)
	}
	defer tx.Rollback()
	before, err := loadEdgeSnapshot(ctx, tx, projectID, edge.SourceID, edge.TargetID, edge.Relation)
	if err != nil {
		return AgentChange{}, err
	}
	if before == nil || before.State != graph.StateActive {
		return AgentChange{}, errors.New("delete agent edge: relation not found")
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE edges SET state='retired', retired_at=unixepoch()
		WHERE project_id = ? AND source_id = ? AND target_id = ? AND relation = ?
	`, projectID, edge.SourceID, edge.TargetID, edge.Relation); err != nil {
		return AgentChange{}, fmt.Errorf("delete agent edge: retire: %w", err)
	}
	after, err := loadEdgeSnapshot(ctx, tx, projectID, edge.SourceID, edge.TargetID, edge.Relation)
	if err != nil {
		return AgentChange{}, err
	}
	change, err := appendAgentChange(ctx, tx, projectID, sessionID, "edge", edgeKey(*before), "unlink", before, after)
	if err != nil {
		return AgentChange{}, err
	}
	if err := tx.Commit(); err != nil {
		return AgentChange{}, fmt.Errorf("delete agent edge: commit: %w", err)
	}
	return change, nil
}

func (s *Store) RollbackAgentChanges(ctx context.Context, projectID string) (int, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, fmt.Errorf("rollback agent changes: begin: %w", err)
	}
	defer tx.Rollback()
	changes, err := baselineChanges(ctx, tx, projectID)
	if err != nil {
		return 0, err
	}
	if len(changes) == 0 {
		return 0, errors.New("rollback agent changes: no changes since baseline")
	}
	if err := validateAgentRollback(ctx, tx, projectID, changes); err != nil {
		return 0, err
	}
	// Restore knowledge first so edge foreign keys point at their baseline nodes.
	for _, change := range changes {
		if change.Entity != "knowledge" {
			continue
		}
		if string(change.Before) == "null" {
			if _, err := deleteMemory(ctx, tx, projectID, change.EntityKey); err != nil {
				return 0, err
			}
			continue
		}
		var snapshot knowledgeSnapshot
		if err := json.Unmarshal(change.Before, &snapshot); err != nil {
			return 0, fmt.Errorf("rollback agent changes: decode knowledge: %w", err)
		}
		if _, err := saveMemory(ctx, tx, snapshot.Memory); err != nil {
			return 0, err
		}
		if _, err := tx.ExecContext(ctx, `UPDATE nodes SET provenance = ? WHERE project_id = ? AND id = ?`, snapshot.Provenance, projectID, graph.ReferenceID(snapshot.Memory.Kind.NodeKind(), snapshot.Memory.Key)); err != nil {
			return 0, fmt.Errorf("rollback agent changes: knowledge provenance: %w", err)
		}
	}
	for _, change := range changes {
		if change.Entity != "edge" {
			continue
		}
		identity := change.After
		if string(identity) == "null" {
			identity = change.Before
		}
		var edge edgeSnapshot
		if err := json.Unmarshal(identity, &edge); err != nil {
			return 0, fmt.Errorf("rollback agent changes: decode edge identity: %w", err)
		}
		if string(change.Before) == "null" {
			if _, err := tx.ExecContext(ctx, `DELETE FROM edges WHERE project_id = ? AND source_id = ? AND target_id = ? AND relation = ?`, projectID, edge.SourceID, edge.TargetID, edge.Relation); err != nil {
				return 0, fmt.Errorf("rollback agent changes: delete edge: %w", err)
			}
			continue
		}
		if err := json.Unmarshal(change.Before, &edge); err != nil {
			return 0, fmt.Errorf("rollback agent changes: decode edge: %w", err)
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO edges(project_id, source_id, target_id, relation, owner, provenance, state, created_at, retired_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, unixepoch(), CASE WHEN ? = 'retired' THEN unixepoch() END)
			ON CONFLICT(project_id, source_id, target_id, relation) DO UPDATE SET
			owner=excluded.owner, provenance=excluded.provenance, state=excluded.state, retired_at=excluded.retired_at
		`, projectID, edge.SourceID, edge.TargetID, edge.Relation, edge.Owner, edge.Provenance, edge.State, edge.State); err != nil {
			return 0, fmt.Errorf("rollback agent changes: restore edge: %w", err)
		}
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM agent_change_log WHERE project_id = ?`, projectID); err != nil {
		return 0, fmt.Errorf("rollback agent changes: clear log: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("rollback agent changes: commit: %w", err)
	}
	return len(changes), nil
}

func (s *Store) CheckpointAgentChanges(ctx context.Context, projectID string) (int, error) {
	result, err := s.db.ExecContext(ctx, `DELETE FROM agent_change_log WHERE project_id = ?`, projectID)
	if err != nil {
		return 0, fmt.Errorf("checkpoint agent changes: %w", err)
	}
	count, err := result.RowsAffected()
	return int(count), err
}

func appendAgentChange(ctx context.Context, tx *sql.Tx, projectID, sessionID, entity, entityKey, action string, before, after any) (AgentChange, error) {
	beforeJSON, err := json.Marshal(before)
	if err != nil {
		return AgentChange{}, fmt.Errorf("log agent change: encode before: %w", err)
	}
	afterJSON, err := json.Marshal(after)
	if err != nil {
		return AgentChange{}, fmt.Errorf("log agent change: encode after: %w", err)
	}
	result, err := tx.ExecContext(ctx, `
		INSERT INTO agent_change_log(project_id, session_id, entity, entity_key, action, before_json, after_json)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, projectID, sessionID, entity, entityKey, action, string(beforeJSON), string(afterJSON))
	if err != nil {
		return AgentChange{}, fmt.Errorf("log agent change: %w", err)
	}
	id, err := result.LastInsertId()
	if err != nil {
		return AgentChange{}, fmt.Errorf("log agent change: ID: %w", err)
	}
	return AgentChange{ID: id, SessionID: sessionID, Entity: entity, EntityKey: entityKey, Action: action, Before: beforeJSON, After: afterJSON, CreatedAt: time.Now().UTC().Truncate(time.Second).Format(time.RFC3339)}, nil
}

func loadKnowledgeSnapshot(ctx context.Context, database databaseRunner, projectID, key string) (*knowledgeSnapshot, error) {
	var snapshot knowledgeSnapshot
	err := database.QueryRowContext(ctx, `
		SELECT project_id, key, kind, value, source, content_hash FROM memories
		WHERE project_id = ? AND key = ?
	`, projectID, key).Scan(&snapshot.Memory.ProjectID, &snapshot.Memory.Key, &snapshot.Memory.Kind, &snapshot.Memory.Value, &snapshot.Memory.Source, &snapshot.Memory.Hash)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("load agent knowledge baseline: %w", err)
	}
	if err := database.QueryRowContext(ctx, `SELECT provenance FROM nodes WHERE project_id = ? AND id = ?`, projectID, graph.ReferenceID(snapshot.Memory.Kind.NodeKind(), key)).Scan(&snapshot.Provenance); err != nil {
		return nil, fmt.Errorf("load agent knowledge provenance: %w", err)
	}
	return &snapshot, nil
}

func loadEdgeSnapshot(ctx context.Context, database databaseRunner, projectID, sourceID, targetID, relation string) (*edgeSnapshot, error) {
	var edge edgeSnapshot
	err := database.QueryRowContext(ctx, `
		SELECT source_id, target_id, relation, owner, provenance, state FROM edges
		WHERE project_id = ? AND source_id = ? AND target_id = ? AND relation = ?
	`, projectID, sourceID, targetID, relation).Scan(&edge.SourceID, &edge.TargetID, &edge.Relation, &edge.Owner, &edge.Provenance, &edge.State)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("load agent edge baseline: %w", err)
	}
	return &edge, nil
}

func incidentEdges(ctx context.Context, tx *sql.Tx, projectID, nodeID string) ([]edgeSnapshot, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT source_id, target_id, relation, owner, provenance, state FROM edges
		WHERE project_id = ? AND (source_id = ? OR target_id = ?)
	`, projectID, nodeID, nodeID)
	if err != nil {
		return nil, fmt.Errorf("load agent knowledge edges: %w", err)
	}
	defer rows.Close()
	edges := []edgeSnapshot{}
	for rows.Next() {
		var edge edgeSnapshot
		if err := rows.Scan(&edge.SourceID, &edge.TargetID, &edge.Relation, &edge.Owner, &edge.Provenance, &edge.State); err != nil {
			return nil, fmt.Errorf("load agent knowledge edges: scan: %w", err)
		}
		edges = append(edges, edge)
	}
	return edges, rows.Err()
}

func baselineChanges(ctx context.Context, tx *sql.Tx, projectID string) ([]AgentChange, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT id, session_id, entity, entity_key, action, before_json, after_json
		FROM agent_change_log WHERE project_id = ? ORDER BY id
	`, projectID)
	if err != nil {
		return nil, fmt.Errorf("rollback agent changes: load log: %w", err)
	}
	defer rows.Close()
	seen := map[string]int{}
	changes := []AgentChange{}
	for rows.Next() {
		var change AgentChange
		var before, after string
		if err := rows.Scan(&change.ID, &change.SessionID, &change.Entity, &change.EntityKey, &change.Action, &before, &after); err != nil {
			return nil, fmt.Errorf("rollback agent changes: scan log: %w", err)
		}
		identity := change.Entity + "\x00" + change.EntityKey
		change.Before, change.After = json.RawMessage(before), json.RawMessage(after)
		if index, found := seen[identity]; found {
			changes[index].After = change.After
			continue
		}
		seen[identity] = len(changes)
		changes = append(changes, change)
	}
	return changes, rows.Err()
}

func validateAgentRollback(ctx context.Context, tx *sql.Tx, projectID string, changes []AgentChange) error {
	for _, change := range changes {
		var encoded []byte
		var err error
		switch change.Entity {
		case "knowledge":
			snapshot, loadErr := loadKnowledgeSnapshot(ctx, tx, projectID, change.EntityKey)
			if loadErr != nil {
				return loadErr
			}
			encoded, err = json.Marshal(snapshot)
		case "edge":
			identity := change.After
			if string(identity) == "null" {
				identity = change.Before
			}
			var edge edgeSnapshot
			if err := json.Unmarshal(identity, &edge); err != nil {
				return fmt.Errorf("rollback agent changes: decode edge identity: %w", err)
			}
			snapshot, loadErr := loadEdgeSnapshot(ctx, tx, projectID, edge.SourceID, edge.TargetID, edge.Relation)
			if loadErr != nil {
				return loadErr
			}
			encoded, err = json.Marshal(snapshot)
		}
		if err != nil {
			return fmt.Errorf("rollback agent changes: encode current state: %w", err)
		}
		if !bytes.Equal(encoded, change.After) {
			return fmt.Errorf("rollback agent changes: %s %q changed after the agent log; resolve it or checkpoint the current state", change.Entity, change.EntityKey)
		}
	}
	return nil
}

func edgeKey(edge edgeSnapshot) string {
	encoded, _ := json.Marshal([3]string{edge.SourceID, edge.Relation, edge.TargetID})
	return string(encoded)
}
