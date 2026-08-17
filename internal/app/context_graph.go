package app

import (
	"context"
	"database/sql"
	"errors"
	"sort"
	"strings"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
)

type contextGraph struct {
	nodes        []graph.Node
	edges        []graph.Edge
	memoryByNode map[string]memory.Memory
}

func (s *Service) contextGraph(ctx context.Context) (contextGraph, error) {
	memories, err := s.store.Memories(ctx, s.project.ID, "")
	if err != nil {
		return contextGraph{}, err
	}
	nodes, edges, err := s.store.Graph(ctx, s.project.ID)
	if err != nil {
		return contextGraph{}, err
	}
	return newContextGraph(memories, nodes, edges), nil
}

func newContextGraph(memories []memory.Memory, nodes []graph.Node, edges []graph.Edge) contextGraph {
	result := contextGraph{nodes: nodes, edges: edges, memoryByNode: map[string]memory.Memory{}}
	for _, entry := range memories {
		result.memoryByNode[memoryNodeID(entry)] = entry
	}
	sort.Slice(result.nodes, func(i, j int) bool {
		leftIntent := result.nodes[i].Kind == graph.KindIntent
		rightIntent := result.nodes[j].Kind == graph.KindIntent
		if leftIntent != rightIntent {
			return leftIntent
		}
		return result.nodes[i].Label < result.nodes[j].Label
	})
	return result
}

func (g contextGraph) find(query string) (graph.Node, bool) {
	query = strings.TrimSpace(query)
	if query == "" {
		return graph.Node{}, false
	}
	for _, node := range g.nodes {
		if node.ID == query || node.Label == query {
			return node, true
		}
	}
	for _, node := range g.nodes {
		if node.Kind == graph.KindMaterial && node.MaterialURI == query {
			return node, true
		}
	}
	for _, node := range g.nodes {
		if node.MaterialURI != "" && node.Locator != "" && node.MaterialURI+"#"+node.Locator == query {
			return node, true
		}
	}
	lower := strings.ToLower(query)
	for _, node := range g.nodes {
		if strings.Contains(strings.ToLower(strings.Join([]string{node.Label, node.Kind, node.MaterialURI, node.Locator, node.Content}, " ")), lower) {
			return node, true
		}
	}
	return graph.Node{}, false
}

func (g contextGraph) seeds(query string) []string {
	query = strings.ToLower(strings.TrimSpace(query))
	var result []string
	for _, node := range g.nodes {
		if query == "" || strings.Contains(strings.ToLower(strings.Join([]string{node.ID, node.Label, node.Kind, node.MaterialURI, node.Locator, node.Content}, " ")), query) {
			result = append(result, node.ID)
		}
	}
	return result
}

func (g contextGraph) neighborhood(seeds []string, depth, limit int) ([]graph.Node, []graph.Edge) {
	if len(seeds) == 0 || limit <= 0 {
		return nil, nil
	}
	adjacent := map[string][]string{}
	for _, edge := range g.edges {
		adjacent[edge.SourceID] = append(adjacent[edge.SourceID], edge.TargetID)
		adjacent[edge.TargetID] = append(adjacent[edge.TargetID], edge.SourceID)
	}
	selected := map[string]bool{}
	var ordered []string
	add := func(id string) {
		if !selected[id] && len(ordered) < limit {
			selected[id] = true
			ordered = append(ordered, id)
		}
	}
	// Interleave intent anchors with their evidence so small graph views stay meaningful.
	for _, seed := range seeds {
		add(seed)
		for _, neighbor := range adjacent[seed] {
			add(neighbor)
		}
	}
	frontier := append([]string(nil), ordered...)
	for level := 1; level < depth && len(frontier) > 0 && len(ordered) < limit; level++ {
		var next []string
		for _, id := range frontier {
			for _, neighbor := range adjacent[id] {
				if !selected[neighbor] {
					add(neighbor)
					next = append(next, neighbor)
				}
			}
		}
		frontier = next
	}
	byID := map[string]graph.Node{}
	for _, node := range g.nodes {
		byID[node.ID] = node
	}
	nodes := make([]graph.Node, 0, len(ordered))
	for _, id := range ordered {
		if node, found := byID[id]; found {
			nodes = append(nodes, node)
		}
	}
	var edges []graph.Edge
	for _, edge := range g.edges {
		if selected[edge.SourceID] && selected[edge.TargetID] {
			edges = append(edges, edge)
		}
	}
	return nodes, edges
}

func (g contextGraph) explanation(node graph.Node) graph.Explanation {
	byID := map[string]graph.Node{}
	for _, candidate := range g.nodes {
		byID[candidate.ID] = candidate
	}
	result := graph.Explanation{Node: node}
	for _, edge := range g.edges {
		switch node.ID {
		case edge.SourceID:
			if target, found := byID[edge.TargetID]; found {
				result.Connections = append(result.Connections, graph.Connection{Direction: "out", Relation: edge.Relation, Node: target})
			}
		case edge.TargetID:
			if source, found := byID[edge.SourceID]; found {
				result.Connections = append(result.Connections, graph.Connection{Direction: "in", Relation: edge.Relation, Node: source})
			}
		}
	}
	sort.Slice(result.Connections, func(i, j int) bool {
		return result.Connections[i].Direction+result.Connections[i].Relation+result.Connections[i].Node.Label < result.Connections[j].Direction+result.Connections[j].Relation+result.Connections[j].Node.Label
	})
	return result
}

func (g contextGraph) path(sourceQuery, targetQuery string) (graph.Path, error) {
	source, found := g.find(sourceQuery)
	if !found {
		return graph.Path{}, sql.ErrNoRows
	}
	target, found := g.find(targetQuery)
	if !found {
		return graph.Path{}, sql.ErrNoRows
	}
	if source.ID == target.ID {
		return graph.Path{}, errors.New("find path: source and target resolve to the same node")
	}
	type step struct {
		previous string
		edge     graph.Edge
	}
	adjacent := map[string][]step{}
	for _, edge := range g.edges {
		adjacent[edge.SourceID] = append(adjacent[edge.SourceID], step{previous: edge.TargetID, edge: edge})
		adjacent[edge.TargetID] = append(adjacent[edge.TargetID], step{previous: edge.SourceID, edge: edge})
	}
	previous := map[string]step{source.ID: {}}
	queue := []string{source.ID}
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		if current == target.ID {
			break
		}
		for _, candidate := range adjacent[current] {
			if _, seen := previous[candidate.previous]; seen {
				continue
			}
			previous[candidate.previous] = step{previous: current, edge: candidate.edge}
			queue = append(queue, candidate.previous)
		}
	}
	if _, found := previous[target.ID]; !found {
		return graph.Path{}, sql.ErrNoRows
	}
	ids := []string{target.ID}
	var edges []graph.Edge
	for current := target.ID; current != source.ID; {
		value := previous[current]
		edges = append(edges, value.edge)
		current = value.previous
		ids = append(ids, current)
	}
	for left, right := 0, len(ids)-1; left < right; left, right = left+1, right-1 {
		ids[left], ids[right] = ids[right], ids[left]
	}
	for left, right := 0, len(edges)-1; left < right; left, right = left+1, right-1 {
		edges[left], edges[right] = edges[right], edges[left]
	}
	byID := map[string]graph.Node{}
	for _, node := range g.nodes {
		byID[node.ID] = node
	}
	result := graph.Path{Edges: edges}
	for _, id := range ids {
		result.Nodes = append(result.Nodes, byID[id])
	}
	return result, nil
}
