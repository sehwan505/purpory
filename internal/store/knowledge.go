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

func (s *Store) Neighborhood(ctx context.Context, projectID string, seedIDs []string, depth, limit int) ([]graph.Node, []graph.Edge, error) {
	if len(seedIDs) == 0 {
		return nil, nil, nil
	}
	if depth < 0 || depth > 5 {
		depth = 2
	}
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	// ponytail: this loads one project's edges; switch to a recursive CTE when measured graphs exceed memory targets.
	edgeRows, err := s.db.QueryContext(ctx, "SELECT source_id, target_id, relation FROM edges WHERE project_id = ? ORDER BY source_id, target_id, relation", projectID)
	if err != nil {
		return nil, nil, fmt.Errorf("load neighborhood: edges: %w", err)
	}
	defer edgeRows.Close()
	var allEdges []graph.Edge
	adjacent := map[string][]string{}
	for edgeRows.Next() {
		var edge graph.Edge
		if err := edgeRows.Scan(&edge.SourceID, &edge.TargetID, &edge.Relation); err != nil {
			return nil, nil, fmt.Errorf("load neighborhood: scan edge: %w", err)
		}
		allEdges = append(allEdges, edge)
		adjacent[edge.SourceID] = append(adjacent[edge.SourceID], edge.TargetID)
		adjacent[edge.TargetID] = append(adjacent[edge.TargetID], edge.SourceID)
	}
	if err := edgeRows.Err(); err != nil {
		return nil, nil, fmt.Errorf("load neighborhood: edges: %w", err)
	}
	selected := map[string]bool{}
	frontier := append([]string(nil), seedIDs...)
	for level := 0; level <= depth && len(frontier) > 0 && len(selected) < limit; level++ {
		var next []string
		for _, id := range frontier {
			if selected[id] || len(selected) >= limit {
				continue
			}
			selected[id] = true
			next = append(next, adjacent[id]...)
		}
		frontier = next
	}
	rows, err := s.db.QueryContext(ctx, "SELECT id, label, kind, material_id, material_uri, locator, content FROM nodes WHERE project_id = ? ORDER BY label, material_uri, locator", projectID)
	if err != nil {
		return nil, nil, fmt.Errorf("load neighborhood: nodes: %w", err)
	}
	defer rows.Close()
	var nodes []graph.Node
	for rows.Next() {
		var node graph.Node
		if err := rows.Scan(&node.ID, &node.Label, &node.Kind, &node.MaterialID, &node.MaterialURI, &node.Locator, &node.Content); err != nil {
			return nil, nil, fmt.Errorf("load neighborhood: scan node: %w", err)
		}
		if selected[node.ID] {
			nodes = append(nodes, node)
		}
	}
	var edges []graph.Edge
	for _, edge := range allEdges {
		if selected[edge.SourceID] && selected[edge.TargetID] {
			edges = append(edges, edge)
		}
	}
	return nodes, edges, rows.Err()
}

func (s *Store) ExplainNode(ctx context.Context, projectID, query string) (graph.Explanation, error) {
	nodes, err := s.SearchNodes(ctx, projectID, query, 1)
	if err != nil {
		return graph.Explanation{}, err
	}
	if len(nodes) == 0 {
		return graph.Explanation{}, sql.ErrNoRows
	}
	node := nodes[0]
	rows, err := s.db.QueryContext(ctx, `
		SELECT 'out', e.relation, n.id, n.label, n.kind, n.material_id, n.material_uri, n.locator, n.content
		FROM edges e JOIN nodes n ON n.project_id = e.project_id AND n.id = e.target_id
		WHERE e.project_id = ? AND e.source_id = ?
		UNION ALL
		SELECT 'in', e.relation, n.id, n.label, n.kind, n.material_id, n.material_uri, n.locator, n.content
		FROM edges e JOIN nodes n ON n.project_id = e.project_id AND n.id = e.source_id
		WHERE e.project_id = ? AND e.target_id = ?
		ORDER BY 1, 4 LIMIT 100
	`, projectID, node.ID, projectID, node.ID)
	if err != nil {
		return graph.Explanation{}, fmt.Errorf("explain node: %w", err)
	}
	defer rows.Close()
	result := graph.Explanation{Node: node}
	for rows.Next() {
		var connection graph.Connection
		if err := rows.Scan(&connection.Direction, &connection.Relation, &connection.Node.ID, &connection.Node.Label, &connection.Node.Kind, &connection.Node.MaterialID, &connection.Node.MaterialURI, &connection.Node.Locator, &connection.Node.Content); err != nil {
			return graph.Explanation{}, fmt.Errorf("explain node: scan: %w", err)
		}
		result.Connections = append(result.Connections, connection)
	}
	return result, rows.Err()
}

func (s *Store) Path(ctx context.Context, projectID, sourceQuery, targetQuery string) (graph.Path, error) {
	sources, err := s.SearchNodes(ctx, projectID, sourceQuery, 1)
	if err != nil || len(sources) == 0 {
		return graph.Path{}, fmt.Errorf("find path source: %w", firstError(err, sql.ErrNoRows))
	}
	targets, err := s.SearchNodes(ctx, projectID, targetQuery, 1)
	if err != nil || len(targets) == 0 {
		return graph.Path{}, fmt.Errorf("find path target: %w", firstError(err, sql.ErrNoRows))
	}
	if sources[0].ID == targets[0].ID {
		return graph.Path{}, errors.New("find path: source and target resolve to the same node")
	}
	rows, err := s.db.QueryContext(ctx, "SELECT source_id, target_id, relation FROM edges WHERE project_id = ?", projectID)
	if err != nil {
		return graph.Path{}, fmt.Errorf("find path: load edges: %w", err)
	}
	defer rows.Close()
	type link struct{ next, relation, source, target string }
	adjacent := map[string][]link{}
	for rows.Next() {
		var edge graph.Edge
		if err := rows.Scan(&edge.SourceID, &edge.TargetID, &edge.Relation); err != nil {
			return graph.Path{}, fmt.Errorf("find path: scan edge: %w", err)
		}
		adjacent[edge.SourceID] = append(adjacent[edge.SourceID], link{edge.TargetID, edge.Relation, edge.SourceID, edge.TargetID})
		adjacent[edge.TargetID] = append(adjacent[edge.TargetID], link{edge.SourceID, edge.Relation, edge.SourceID, edge.TargetID})
	}
	previous := map[string]link{sources[0].ID: {}}
	queue := []string{sources[0].ID}
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		if current == targets[0].ID {
			break
		}
		for _, candidate := range adjacent[current] {
			if _, seen := previous[candidate.next]; seen {
				continue
			}
			previous[candidate.next] = link{next: current, relation: candidate.relation, source: candidate.source, target: candidate.target}
			queue = append(queue, candidate.next)
		}
	}
	if _, found := previous[targets[0].ID]; !found {
		return graph.Path{}, sql.ErrNoRows
	}
	ids := []string{targets[0].ID}
	var edges []graph.Edge
	for current := targets[0].ID; current != sources[0].ID; {
		step := previous[current]
		edges = append(edges, graph.Edge{SourceID: step.source, TargetID: step.target, Relation: step.relation})
		current = step.next
		ids = append(ids, current)
	}
	for left, right := 0, len(ids)-1; left < right; left, right = left+1, right-1 {
		ids[left], ids[right] = ids[right], ids[left]
	}
	for left, right := 0, len(edges)-1; left < right; left, right = left+1, right-1 {
		edges[left], edges[right] = edges[right], edges[left]
	}
	result := graph.Path{Edges: edges}
	for _, id := range ids {
		var node graph.Node
		if err := s.db.QueryRowContext(ctx, "SELECT id, label, kind, material_id, material_uri, locator, content FROM nodes WHERE project_id = ? AND id = ?", projectID, id).Scan(&node.ID, &node.Label, &node.Kind, &node.MaterialID, &node.MaterialURI, &node.Locator, &node.Content); err != nil {
			return graph.Path{}, fmt.Errorf("find path: load node: %w", err)
		}
		result.Nodes = append(result.Nodes, node)
	}
	return result, nil
}
