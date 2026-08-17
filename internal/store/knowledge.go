package store

import (
	"context"
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
		SELECT id, label, kind, material_id, material_uri, locator, content
		FROM nodes WHERE project_id = ? ORDER BY id
	`, projectID)
	if err != nil {
		return nil, nil, fmt.Errorf("load knowledge: nodes: %w", err)
	}
	var nodes []graph.Node
	for rows.Next() {
		var node graph.Node
		if err := rows.Scan(&node.ID, &node.Label, &node.Kind, &node.MaterialID, &node.MaterialURI, &node.Locator, &node.Content); err != nil {
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
	if !linkKind(link.SourceKind) || !linkKind(link.TargetKind) || strings.TrimSpace(link.SourceRef) == "" ||
		strings.TrimSpace(link.TargetRef) == "" || strings.TrimSpace(link.Relation) == "" {
		return errors.New("save link: valid kinds, references, and relation are required")
	}
	if link.SourceKind == "intent" && link.TargetKind == "material" && !graph.IsIntentMaterialRelation(link.Relation) {
		return errors.New("save link: unsupported intent to material relation")
	}
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO links(project_id, source_kind, source_ref, relation, target_kind, target_ref)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT DO NOTHING
	`, projectID, link.SourceKind, link.SourceRef, link.Relation, link.TargetKind, link.TargetRef)
	if err != nil {
		return fmt.Errorf("save link: %w", err)
	}
	return nil
}

func (s *Store) Links(ctx context.Context, projectID string) ([]graph.Link, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT source_kind, source_ref, relation, target_kind, target_ref
		FROM links WHERE project_id = ? ORDER BY source_kind, source_ref, relation, target_kind, target_ref
	`, projectID)
	if err != nil {
		return nil, fmt.Errorf("load links: %w", err)
	}
	defer rows.Close()
	var links []graph.Link
	for rows.Next() {
		var link graph.Link
		if err := rows.Scan(&link.SourceKind, &link.SourceRef, &link.Relation, &link.TargetKind, &link.TargetRef); err != nil {
			return nil, fmt.Errorf("load links: scan: %w", err)
		}
		links = append(links, link)
	}
	return links, rows.Err()
}

func linkKind(value string) bool {
	return value == "intent" || value == "material" || value == "knowledge"
}

// ReplaceKnowledge atomically publishes one complete project snapshot.
// ponytail: rewrite the resolved snapshot; use per-Material writes when measured database time dominates updates.
func (s *Store) ReplaceKnowledge(ctx context.Context, projectID string, materials []material.Material, nodes []graph.Node, claims []graph.Claim, edges []graph.Edge) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("replace knowledge: begin: %w", err)
	}
	defer tx.Rollback()
	for _, table := range []string{"edges", "claims", "nodes", "materials"} {
		if _, err := tx.ExecContext(ctx, "DELETE FROM "+table+" WHERE project_id = ?", projectID); err != nil {
			return fmt.Errorf("replace knowledge: clear %s: %w", table, err)
		}
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
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO nodes(project_id, id, label, kind, material_id, material_uri, locator, content)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		`, projectID, node.ID, node.Label, node.Kind, node.MaterialID, node.MaterialURI, node.Locator, node.Content); err != nil {
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
			INSERT INTO edges(project_id, source_id, target_id, relation) VALUES (?, ?, ?, ?)
		`, projectID, edge.SourceID, edge.TargetID, edge.Relation); err != nil {
			return fmt.Errorf("replace knowledge: insert edge: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("replace knowledge: commit: %w", err)
	}
	return nil
}

func (s *Store) SearchNodes(ctx context.Context, projectID, query string, limit int) ([]graph.Node, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	pattern := "%" + escapeLike(strings.TrimSpace(query)) + "%"
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, label, kind, material_id, material_uri, locator, content FROM nodes
		WHERE project_id = ? AND (id = ? OR label LIKE ? ESCAPE '\' OR material_uri LIKE ? ESCAPE '\' OR content LIKE ? ESCAPE '\')
		ORDER BY CASE WHEN id = ? THEN 0 WHEN label = ? THEN 1 WHEN material_uri = ? AND kind = 'material' THEN 2 ELSE 3 END,
		         label, material_uri, locator LIMIT ?
	`, projectID, query, pattern, pattern, pattern, query, query, query, limit)
	if err != nil {
		return nil, fmt.Errorf("search nodes: %w", err)
	}
	defer rows.Close()
	var nodes []graph.Node
	for rows.Next() {
		var node graph.Node
		if err := rows.Scan(&node.ID, &node.Label, &node.Kind, &node.MaterialID, &node.MaterialURI, &node.Locator, &node.Content); err != nil {
			return nil, fmt.Errorf("search nodes: scan: %w", err)
		}
		nodes = append(nodes, node)
	}
	return nodes, rows.Err()
}
