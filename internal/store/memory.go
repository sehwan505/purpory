package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
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
		if _, err := database.ExecContext(ctx, `DELETE FROM nodes WHERE project_id = ? AND kind = ? AND ref = ?`, value.ProjectID, memory.Kind(currentKind).NodeKind(), value.Key); err != nil {
			return SaveResult{}, fmt.Errorf("save memory: replace graph kind: %w", err)
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

func (s *Store) ReconcileMemories(ctx context.Context, sessionID string, proposals []MemoryProposal) ([]SaveResult, error) {
	if strings.TrimSpace(sessionID) == "" || len(proposals) == 0 || len(proposals) > 20 {
		return nil, errors.New("reconcile memory: session and 1-20 proposals are required")
	}
	projectID := proposals[0].Memory.ProjectID
	seen := map[string]bool{}
	for proposalIndex := range proposals {
		proposal := &proposals[proposalIndex]
		if proposal.Memory.ProjectID != projectID || seen[proposal.Memory.Key] {
			return nil, errors.New("reconcile memory: proposals must have one project and unique keys")
		}
		if (len(proposal.EvidenceIDs) > 0 || len(proposal.EvidenceRefs) > 0) && !sameEvidenceIDs(proposal.EvidenceIDs, proposal.EvidenceRefs) {
			return nil, errors.New("reconcile memory: proposals require matching grounded user evidence")
		}
		if len(proposal.Links)+len(proposal.RetiredLinks) > 0 && len(proposal.EvidenceRefs) == 0 {
			return nil, errors.New("reconcile memory: linked proposals require grounded user evidence")
		}
		if len(proposal.EvidenceRefs) > 16 {
			return nil, errors.New("reconcile memory: at most 16 user excerpts are allowed")
		}
		for _, ref := range proposal.EvidenceRefs {
			if !validExcerpt(ref.MessageID, ref.PartID, ref.Quote, ref.StartByte, ref.EndByte) {
				return nil, errors.New("reconcile memory: invalid user evidence excerpt")
			}
		}
		if len(proposal.ContextRefs) > 16 || len(proposal.ContextRefs) > 0 && len(proposal.EvidenceRefs) == 0 {
			return nil, errors.New("reconcile memory: assistant context requires user evidence and at most 16 excerpts")
		}
		for _, ref := range proposal.ContextRefs {
			if !validExcerpt(ref.MessageID, ref.PartID, ref.Quote, ref.StartByte, ref.EndByte) {
				return nil, errors.New("reconcile memory: invalid assistant context excerpt")
			}
		}
		normalizedEvidence := map[graph.Link][]memory.EvidenceRef{}
		for link, refs := range proposal.LinkEvidence {
			normalizedEvidence[graph.NormalizeLink(link)] = refs
		}
		proposal.LinkEvidence = normalizedEvidence
		normalizedRetirementEvidence := map[graph.Link][]memory.EvidenceRef{}
		for link, refs := range proposal.RetirementEvidence {
			normalizedRetirementEvidence[graph.NormalizeLink(link)] = refs
		}
		proposal.RetirementEvidence = normalizedRetirementEvidence
		operations := map[graph.Link]bool{}
		for linkIndex := range proposal.Links {
			proposal.Links[linkIndex] = graph.NormalizeLink(proposal.Links[linkIndex])
			link := proposal.Links[linkIndex]
			if proposal.Memory.Kind != memory.Decision || !reconcileLinkOwnedBy(link, proposal.Memory.Key) {
				return nil, errors.New("reconcile memory: links must connect their grounded intent with a supported semantic relation")
			}
			if link.TargetKind == graph.KindIntent && !evidenceSubset(proposal.LinkEvidence[link], proposal.EvidenceRefs) {
				return nil, errors.New("reconcile memory: intent links require candidate-grounded user evidence")
			}
			operations[link] = true
		}
		for linkIndex := range proposal.RetiredLinks {
			proposal.RetiredLinks[linkIndex] = graph.NormalizeLink(proposal.RetiredLinks[linkIndex])
			link := proposal.RetiredLinks[linkIndex]
			if operations[link] || proposal.Memory.Kind != memory.Decision || link.TargetKind != graph.KindIntent || !reconcileLinkOwnedBy(link, proposal.Memory.Key) || !evidenceSubset(proposal.RetirementEvidence[link], proposal.EvidenceRefs) {
				return nil, errors.New("reconcile memory: retirements must target an existing grounded intent relation")
			}
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
	for _, proposal := range proposals {
		for _, links := range [][]graph.Link{proposal.Links, proposal.RetiredLinks} {
			for _, link := range links {
				if expected, tracked := proposal.ExpectedLinkStates[link]; tracked {
					current, err := graphLinkState(ctx, connection, projectID, link)
					if err != nil {
						return nil, fmt.Errorf("reconcile memory: load link state: %w", err)
					}
					if current != expected {
						return nil, ErrMemoryConflict
					}
				}
				for _, endpoint := range []struct{ kind, ref string }{{link.SourceKind, link.SourceRef}, {link.TargetKind, link.TargetRef}} {
					if endpoint.kind != graph.KindIntent || seen[endpoint.ref] {
						continue
					}
					var state string
					err := connection.QueryRowContext(ctx, `SELECT state FROM nodes WHERE project_id = ? AND kind = ? AND ref = ?`, projectID, endpoint.kind, endpoint.ref).Scan(&state)
					if errors.Is(err, sql.ErrNoRows) || err == nil && state != graph.StateActive {
						return nil, ErrMemoryConflict
					}
					if err != nil {
						return nil, fmt.Errorf("reconcile memory: load intent endpoint: %w", err)
					}
				}
			}
		}
		for _, link := range proposal.RetiredLinks {
			state, err := graphLinkState(ctx, connection, projectID, link)
			if err != nil {
				return nil, fmt.Errorf("reconcile memory: load retirement state: %w", err)
			}
			if state != graph.StateActive {
				return nil, ErrMemoryConflict
			}
		}
	}
	results := make([]SaveResult, 0, len(proposals))
	var changes []memory.ReconcileChange
	var links []memory.ReconcileLink
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
			changes = append(changes, memory.ReconcileChange{Key: proposal.Memory.Key, Action: result.Action, Before: previous, After: proposal.Memory, VersionID: result.VersionID, EvidenceIDs: proposal.EvidenceIDs, EvidenceRefs: proposal.EvidenceRefs, ContextRefs: proposal.ContextRefs})
		}
		for _, link := range proposal.Links {
			action, err := saveGraphLink(ctx, connection, projectID, link, "reconcile:"+sessionID)
			if err != nil {
				return nil, fmt.Errorf("reconcile memory: save link: %w", err)
			}
			if action != "" {
				evidenceRefs := proposal.LinkEvidence[link]
				if len(evidenceRefs) == 0 {
					evidenceRefs = proposal.EvidenceRefs
				}
				links = append(links, memory.ReconcileLink{Action: action, SourceKind: link.SourceKind, SourceRef: link.SourceRef, Relation: link.Relation, TargetKind: link.TargetKind, TargetRef: link.TargetRef, EvidenceIDs: evidenceRefIDs(evidenceRefs), EvidenceRefs: evidenceRefs, ContextRefs: proposal.ContextRefs})
			}
		}
		for _, link := range proposal.RetiredLinks {
			retired, err := retireGraphLink(ctx, connection, projectID, link)
			if err != nil {
				return nil, fmt.Errorf("reconcile memory: retire link: %w", err)
			}
			if retired {
				evidenceRefs := proposal.RetirementEvidence[link]
				links = append(links, memory.ReconcileLink{Action: "retired", SourceKind: link.SourceKind, SourceRef: link.SourceRef, Relation: link.Relation, TargetKind: link.TargetKind, TargetRef: link.TargetRef, EvidenceIDs: evidenceRefIDs(evidenceRefs), EvidenceRefs: evidenceRefs, ContextRefs: proposal.ContextRefs})
			}
		}
	}
	if len(changes) > 0 || len(links) > 0 {
		encoded, err := json.Marshal(map[string]any{"changes": changes, "links": links})
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

func validExcerpt(messageID, partID, quote string, start, end int) bool {
	return strings.TrimSpace(messageID) != "" && len(messageID) <= 128 && strings.TrimSpace(partID) != "" && len(partID) <= 128 &&
		strings.TrimSpace(quote) != "" && len([]rune(quote)) <= 1024 && start >= 0 && end > start && end-start == len(quote)
}

func sameEvidenceIDs(ids []string, refs []memory.EvidenceRef) bool {
	want := map[string]bool{}
	for _, id := range evidenceRefIDs(refs) {
		want[id] = true
	}
	if len(ids) != len(want) {
		return false
	}
	seen := map[string]bool{}
	for _, id := range ids {
		if !want[id] || seen[id] {
			return false
		}
		seen[id] = true
	}
	return true
}

func evidenceRefIDs(refs []memory.EvidenceRef) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(refs))
	for _, ref := range refs {
		if !seen[ref.MessageID] {
			seen[ref.MessageID] = true
			result = append(result, ref.MessageID)
		}
	}
	return result
}

func evidenceSubset(values, allowed []memory.EvidenceRef) bool {
	if len(values) == 0 {
		return false
	}
	available := map[memory.EvidenceRef]bool{}
	for _, value := range allowed {
		available[value] = true
	}
	seen := map[memory.EvidenceRef]bool{}
	for _, value := range values {
		if !available[value] || seen[value] {
			return false
		}
		seen[value] = true
	}
	return true
}

func reconcileLinkOwnedBy(link graph.Link, key string) bool {
	if link.SourceKind != graph.KindIntent || strings.TrimSpace(link.TargetRef) == "" {
		return false
	}
	switch link.TargetKind {
	case graph.KindMaterial:
		return link.SourceRef == key && graph.IsIntentMaterialRelation(link.Relation)
	case graph.KindIntent:
		if !graph.IsIntentIntentRelation(link.Relation) || link.SourceRef == link.TargetRef {
			return false
		}
		if link.Relation == graph.RelationConflictsWith {
			return link.SourceRef == key || link.TargetRef == key
		}
		return link.SourceRef == key
	default:
		return false
	}
}

func (s *Store) ReconciliationEvents(ctx context.Context, projectID string) ([]memory.ReconcileEvent, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT session_id, changes_json, created_at
		FROM reconciliation_events WHERE project_id = ? ORDER BY created_at DESC, id DESC
	`, projectID)
	if err != nil {
		return nil, fmt.Errorf("list reconciliation events: %w", err)
	}
	defer rows.Close()
	var events []memory.ReconcileEvent
	for rows.Next() {
		var event memory.ReconcileEvent
		var encoded string
		var timestamp int64
		if err := rows.Scan(&event.SessionID, &encoded, &timestamp); err != nil {
			return nil, fmt.Errorf("list reconciliation events: scan: %w", err)
		}
		if err := json.Unmarshal([]byte(encoded), &event); err != nil {
			return nil, fmt.Errorf("list reconciliation events: decode: %w", err)
		}
		event.OccurredAt = time.Unix(timestamp, 0).UTC().Format(time.RFC3339)
		events = append(events, event)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("list reconciliation events: %w", err)
	}
	return events, nil
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
