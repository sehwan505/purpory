package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/material"
)

func (s *Store) Materials(ctx context.Context, projectID string) ([]material.Material, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, uri, media_type, processor, content_hash, size, modified_at
		FROM materials WHERE project_id = ? ORDER BY uri
	`, projectID)
	if err != nil {
		return nil, fmt.Errorf("load materials: %w", err)
	}
	defer rows.Close()
	var values []material.Material
	for rows.Next() {
		var value material.Material
		if err := rows.Scan(&value.ID, &value.URI, &value.MediaType, &value.Processor, &value.Hash, &value.Size, &value.ModifiedAt); err != nil {
			return nil, fmt.Errorf("load materials: scan: %w", err)
		}
		values = append(values, value)
	}
	return values, rows.Err()
}

func (s *Store) Knowledge(ctx context.Context, projectID string) ([]graph.Node, []graph.Claim, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, label, kind, subkind, ref, owner, state, provenance, material_id, material_uri, locator, content
		FROM nodes WHERE project_id = ? AND owner = 'observed' AND state = 'active' ORDER BY id
	`, projectID)
	if err != nil {
		return nil, nil, fmt.Errorf("load knowledge: nodes: %w", err)
	}
	var nodes []graph.Node
	for rows.Next() {
		var node graph.Node
		if err := rows.Scan(&node.ID, &node.Label, &node.Kind, &node.Subkind, &node.Ref, &node.Owner, &node.State, &node.Provenance, &node.MaterialID, &node.MaterialURI, &node.Locator, &node.Content); err != nil {
			rows.Close()
			return nil, nil, fmt.Errorf("load knowledge: scan node: %w", err)
		}
		nodes = append(nodes, node)
	}
	if err := rows.Close(); err != nil {
		return nil, nil, fmt.Errorf("load knowledge: close nodes: %w", err)
	}
	claimRows, err := s.db.QueryContext(ctx, `
		SELECT material_id, source_id, target_id, target, relation
		FROM claims WHERE project_id = ? ORDER BY material_id, source_id, target_id, target, relation
	`, projectID)
	if err != nil {
		return nil, nil, fmt.Errorf("load knowledge: claims: %w", err)
	}
	defer claimRows.Close()
	var claims []graph.Claim
	for claimRows.Next() {
		var claim graph.Claim
		if err := claimRows.Scan(&claim.MaterialID, &claim.SourceID, &claim.TargetID, &claim.TargetLabel, &claim.Relation); err != nil {
			return nil, nil, fmt.Errorf("load knowledge: scan claim: %w", err)
		}
		claims = append(claims, claim)
	}
	return nodes, claims, claimRows.Err()
}

func (s *Store) SaveLink(ctx context.Context, projectID string, link graph.Link) error {
	link = graph.NormalizeLink(link)
	if err := validateLink(link); err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("save link: begin: %w", err)
	}
	defer tx.Rollback()
	if _, err := saveGraphLink(ctx, tx, projectID, link, "explicit"); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("save link: commit: %w", err)
	}
	return nil
}

func linkKind(value string) bool {
	return value == graph.KindIntent || value == graph.KindMaterial || value == graph.KindKnowledge || value == graph.KindReference
}

func validateLink(link graph.Link) error {
	if !linkKind(link.SourceKind) || !linkKind(link.TargetKind) || strings.TrimSpace(link.SourceRef) == "" ||
		strings.TrimSpace(link.TargetRef) == "" || strings.TrimSpace(link.Relation) == "" {
		return errors.New("save link: valid kinds, references, and relation are required")
	}
	if link.SourceKind == link.TargetKind && link.SourceRef == link.TargetRef {
		return errors.New("save link: self links are not supported")
	}
	if link.SourceKind == graph.KindIntent && link.TargetKind == graph.KindMaterial && !graph.IsIntentMaterialRelation(link.Relation) {
		return errors.New("save link: unsupported intent to material relation")
	}
	if link.SourceKind == graph.KindIntent && link.TargetKind == graph.KindIntent && !graph.IsIntentIntentRelation(link.Relation) {
		return errors.New("save link: unsupported intent to intent relation")
	}
	return nil
}

func (s *Store) LinkStates(ctx context.Context, projectID string) (map[graph.Link]string, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT source.kind, source.ref, edge.relation, target.kind, target.ref, edge.state
		FROM edges edge
		JOIN nodes source ON source.project_id = edge.project_id AND source.id = edge.source_id
		JOIN nodes target ON target.project_id = edge.project_id AND target.id = edge.target_id
		WHERE edge.project_id = ?
	`, projectID)
	if err != nil {
		return nil, fmt.Errorf("load link states: %w", err)
	}
	defer rows.Close()
	result := map[graph.Link]string{}
	for rows.Next() {
		var link graph.Link
		var state string
		if err := rows.Scan(&link.SourceKind, &link.SourceRef, &link.Relation, &link.TargetKind, &link.TargetRef, &state); err != nil {
			return nil, fmt.Errorf("load link states: scan: %w", err)
		}
		result[link] = state
	}
	return result, rows.Err()
}

func graphLinkState(ctx context.Context, database databaseRunner, projectID string, link graph.Link) (string, error) {
	link = graph.NormalizeLink(link)
	var state string
	err := database.QueryRowContext(ctx, `
		SELECT edge.state FROM edges edge
		JOIN nodes source ON source.project_id = edge.project_id AND source.id = edge.source_id
		JOIN nodes target ON target.project_id = edge.project_id AND target.id = edge.target_id
		WHERE edge.project_id = ? AND source.kind = ? AND source.ref = ?
		  AND target.kind = ? AND target.ref = ? AND edge.relation = ?
	`, projectID, link.SourceKind, link.SourceRef, link.TargetKind, link.TargetRef, link.Relation).Scan(&state)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return state, err
}

func saveGraphLink(ctx context.Context, database databaseRunner, projectID string, link graph.Link, provenance string) (string, error) {
	link = graph.NormalizeLink(link)
	sourceID, err := ensureGraphNode(ctx, database, projectID, link.SourceKind, link.SourceRef, provenance)
	if err != nil {
		return "", err
	}
	targetID, err := ensureGraphNode(ctx, database, projectID, link.TargetKind, link.TargetRef, provenance)
	if err != nil {
		return "", err
	}
	var owner, state string
	err = database.QueryRowContext(ctx, `
		SELECT owner, state FROM edges WHERE project_id = ? AND source_id = ? AND target_id = ? AND relation = ?
	`, projectID, sourceID, targetID, link.Relation).Scan(&owner, &state)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		if _, err := database.ExecContext(ctx, `
			INSERT INTO edges(project_id, source_id, target_id, relation, owner, provenance, state, created_at)
			VALUES (?, ?, ?, ?, 'durable', ?, 'active', unixepoch())
		`, projectID, sourceID, targetID, link.Relation, provenance); err != nil {
			return "", fmt.Errorf("save link: insert edge: %w", err)
		}
		return "added", nil
	case err != nil:
		return "", fmt.Errorf("save link: load edge: %w", err)
	case owner != graph.OwnerDurable || state != graph.StateActive:
		if _, err := database.ExecContext(ctx, `
			UPDATE edges SET owner = 'durable', provenance = ?, state = 'active',
				created_at = CASE WHEN created_at = 0 THEN unixepoch() ELSE created_at END, retired_at = NULL
			WHERE project_id = ? AND source_id = ? AND target_id = ? AND relation = ?
		`, provenance, projectID, sourceID, targetID, link.Relation); err != nil {
			return "", fmt.Errorf("save link: promote edge: %w", err)
		}
		if state == graph.StateRetired {
			return "reactivated", nil
		}
		return "added", nil
	default:
		return "", nil
	}
}

func retireGraphLink(ctx context.Context, database databaseRunner, projectID string, link graph.Link) (bool, error) {
	link = graph.NormalizeLink(link)
	result, err := database.ExecContext(ctx, `
		UPDATE edges SET state = 'retired', retired_at = unixepoch()
		WHERE project_id = ? AND owner = 'durable' AND state = 'active' AND relation = ?
		  AND source_id = (SELECT id FROM nodes WHERE project_id = ? AND kind = ? AND ref = ?)
		  AND target_id = (SELECT id FROM nodes WHERE project_id = ? AND kind = ? AND ref = ?)
	`, projectID, link.Relation, projectID, link.SourceKind, link.SourceRef, projectID, link.TargetKind, link.TargetRef)
	if err != nil {
		return false, fmt.Errorf("retire link: %w", err)
	}
	changed, err := result.RowsAffected()
	return changed > 0, err
}

func ensureGraphNode(ctx context.Context, database databaseRunner, projectID, kind, ref, provenance string) (string, error) {
	var id string
	err := database.QueryRowContext(ctx, `
		SELECT id FROM nodes WHERE project_id = ? AND kind = ? AND ref = ?
	`, projectID, kind, ref).Scan(&id)
	if err == nil {
		return id, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return "", fmt.Errorf("save link: resolve node: %w", err)
	}
	id = graph.ReferenceID(kind, ref)
	if _, err := database.ExecContext(ctx, `
		INSERT INTO nodes(project_id, id, label, kind, ref, owner, state, provenance)
		VALUES (?, ?, ?, ?, ?, 'durable', 'missing', ?)
	`, projectID, id, ref, kind, ref, provenance); err != nil {
		return "", fmt.Errorf("save link: create missing node: %w", err)
	}
	return id, nil
}

func (s *Store) Graph(ctx context.Context, projectID string) ([]graph.Node, []graph.Edge, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, label, kind, subkind, ref, owner, state, provenance, material_id, material_uri, locator, content
		FROM nodes WHERE project_id = ? ORDER BY CASE kind WHEN 'intent' THEN 0 ELSE 1 END, label, id
	`, projectID)
	if err != nil {
		return nil, nil, fmt.Errorf("load graph: nodes: %w", err)
	}
	var nodes []graph.Node
	for rows.Next() {
		var node graph.Node
		if err := rows.Scan(&node.ID, &node.Label, &node.Kind, &node.Subkind, &node.Ref, &node.Owner, &node.State, &node.Provenance, &node.MaterialID, &node.MaterialURI, &node.Locator, &node.Content); err != nil {
			rows.Close()
			return nil, nil, fmt.Errorf("load graph: scan node: %w", err)
		}
		nodes = append(nodes, node)
	}
	if err := rows.Close(); err != nil {
		return nil, nil, fmt.Errorf("load graph: close nodes: %w", err)
	}
	edgeRows, err := s.db.QueryContext(ctx, `
		SELECT source_id, target_id, relation, owner, provenance
		FROM edges WHERE project_id = ? AND state = 'active' ORDER BY source_id, target_id, relation
	`, projectID)
	if err != nil {
		return nil, nil, fmt.Errorf("load graph: edges: %w", err)
	}
	defer edgeRows.Close()
	var edges []graph.Edge
	for edgeRows.Next() {
		var edge graph.Edge
		if err := edgeRows.Scan(&edge.SourceID, &edge.TargetID, &edge.Relation, &edge.Owner, &edge.Provenance); err != nil {
			return nil, nil, fmt.Errorf("load graph: scan edge: %w", err)
		}
		edges = append(edges, edge)
	}
	return nodes, edges, edgeRows.Err()
}

// ReplaceKnowledge atomically publishes one complete project snapshot.
// ponytail: rewrite the resolved snapshot; use per-Material writes when measured database time dominates updates.
func (s *Store) ReplaceKnowledge(ctx context.Context, projectID string, materials []material.Material, nodes []graph.Node, claims []graph.Claim, edges []graph.Edge) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("replace knowledge: begin: %w", err)
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `DELETE FROM edges WHERE project_id = ? AND owner = 'observed'`, projectID); err != nil {
		return fmt.Errorf("replace knowledge: clear observed edges: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM claims WHERE project_id = ?`, projectID); err != nil {
		return fmt.Errorf("replace knowledge: clear claims: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE nodes SET owner = 'durable', state = 'missing', provenance = 'durable-link',
			material_id = '', locator = '', content = ''
		WHERE project_id = ? AND owner = 'observed' AND EXISTS (
			SELECT 1 FROM edges
			WHERE edges.project_id = nodes.project_id AND edges.owner = 'durable'
			  AND (edges.source_id = nodes.id OR edges.target_id = nodes.id)
		)
	`, projectID); err != nil {
		return fmt.Errorf("replace knowledge: retain linked nodes: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM nodes WHERE project_id = ? AND owner = 'observed'`, projectID); err != nil {
		return fmt.Errorf("replace knowledge: clear observed nodes: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM materials WHERE project_id = ?`, projectID); err != nil {
		return fmt.Errorf("replace knowledge: clear materials: %w", err)
	}
	for _, value := range materials {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO materials(project_id, id, uri, media_type, processor, content_hash, size, modified_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		`, projectID, value.ID, value.URI, value.MediaType, value.Processor, value.Hash, value.Size, value.ModifiedAt); err != nil {
			return fmt.Errorf("replace knowledge: insert material: %w", err)
		}
	}
	for _, node := range nodes {
		ref := node.Ref
		if ref == "" {
			ref = node.ID
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO nodes(project_id, id, label, kind, subkind, ref, owner, state, provenance, material_id, material_uri, locator, content)
			VALUES (?, ?, ?, ?, ?, ?, 'observed', 'active', ?, ?, ?, ?, ?)
			ON CONFLICT(project_id, id) DO UPDATE SET
				label=excluded.label, kind=excluded.kind, subkind=excluded.subkind, ref=excluded.ref,
				owner='observed', state='active', provenance=excluded.provenance,
				material_id=excluded.material_id, material_uri=excluded.material_uri,
				locator=excluded.locator, content=excluded.content
		`, projectID, node.ID, node.Label, node.Kind, node.Subkind, ref, node.MaterialURI, node.MaterialID, node.MaterialURI, node.Locator, node.Content); err != nil {
			return fmt.Errorf("replace knowledge: insert node: %w", err)
		}
	}
	for _, claim := range claims {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO claims(project_id, material_id, source_id, target_id, target, relation)
			VALUES (?, ?, ?, ?, ?, ?)
		`, projectID, claim.MaterialID, claim.SourceID, claim.TargetID, claim.TargetLabel, claim.Relation); err != nil {
			return fmt.Errorf("replace knowledge: insert claim: %w", err)
		}
	}
	for _, edge := range edges {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO edges(project_id, source_id, target_id, relation, owner, provenance, state, created_at)
			VALUES (?, ?, ?, ?, 'observed', 'update', 'active', unixepoch()) ON CONFLICT DO NOTHING
		`, projectID, edge.SourceID, edge.TargetID, edge.Relation); err != nil {
			return fmt.Errorf("replace knowledge: insert edge: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("replace knowledge: commit: %w", err)
	}
	return nil
}
