package app

import (
	"context"
	"database/sql"
	"errors"
	"sort"
	"strings"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/resolve"
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
	nodes, claims, err := s.store.Knowledge(ctx, s.project.ID)
	if err != nil {
		return contextGraph{}, err
	}
	links, err := s.store.Links(ctx, s.project.ID)
	if err != nil {
		return contextGraph{}, err
	}
	return newContextGraph(memories, nodes, resolve.Claims(nodes, claims), links), nil
}

func newContextGraph(memories []memory.Memory, nodes []graph.Node, edges []graph.Edge, links []graph.Link) contextGraph {
	result := contextGraph{memoryByNode: map[string]memory.Memory{}}
	intentRefs := map[string]string{}
	materialRefs := map[string]string{}
	knowledgeRefs := map[string]string{}
	for _, entry := range memories {
		node := memoryGraphNode(entry)
		result.nodes = append(result.nodes, node)
		result.memoryByNode[node.ID] = entry
		switch entry.Kind {
		case memory.Decision:
			intentRefs[entry.Key] = node.ID
		case memory.Note:
			knowledgeRefs[entry.Key] = node.ID
		case memory.Reference:
			materialRefs[entry.Key] = node.ID
		}
	}
	sort.Slice(result.nodes, func(i, j int) bool {
		leftIntent := result.nodes[i].Kind == "intent"
		rightIntent := result.nodes[j].Kind == "intent"
		if leftIntent != rightIntent {
			return leftIntent
		}
		return result.nodes[i].Label < result.nodes[j].Label
	})
	for _, node := range nodes {
		result.nodes = append(result.nodes, node)
		knowledgeRefs[node.ID] = node.ID
		if node.MaterialURI != "" && node.Locator != "" {
			knowledgeRefs[node.MaterialURI+"#"+node.Locator] = node.ID
		}
		if node.Kind == "material" {
			materialRefs[node.ID] = node.ID
			materialRefs[node.MaterialURI] = node.ID
		}
	}
	result.edges = append(result.edges, edges...)
	seenNodes := map[string]bool{}
	for _, node := range result.nodes {
		seenNodes[node.ID] = true
	}
	seenEdges := map[string]bool{}
	for _, edge := range result.edges {
		seenEdges[edge.SourceID+"\x00"+edge.TargetID+"\x00"+edge.Relation] = true
	}
	resolve := func(kind, ref string) string {
		var id string
		switch kind {
		case "intent":
			id = intentRefs[ref]
		case "material":
			id = materialRefs[ref]
		case "knowledge":
			id = knowledgeRefs[ref]
		}
		if id != "" {
			return id
		}
		id = "missing:" + kind + ":" + ref
		if !seenNodes[id] {
			seenNodes[id] = true
			result.nodes = append(result.nodes, graph.Node{ID: id, Label: ref, Kind: "missing", Content: "Unresolved " + kind + " reference"})
		}
		return id
	}
	for _, link := range links {
		edge := graph.Edge{SourceID: resolve(link.SourceKind, link.SourceRef), TargetID: resolve(link.TargetKind, link.TargetRef), Relation: link.Relation}
		key := edge.SourceID + "\x00" + edge.TargetID + "\x00" + edge.Relation
		if !seenEdges[key] {
			seenEdges[key] = true
			result.edges = append(result.edges, edge)
		}
	}
	return result
}

func memoryGraphNode(entry memory.Memory) graph.Node {
	kind := "knowledge"
	switch entry.Kind {
	case memory.Decision:
		kind = "intent"
	case memory.Reference:
		kind = "reference"
	}
	content := ""
	if entry.Value != nil {
		content = *entry.Value
	} else if entry.Source != nil {
		content = *entry.Source
	}
	return graph.Node{ID: memoryNodeID(entry), Label: entry.Key, Kind: kind, Content: content}
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
		if node.Kind == "material" && node.MaterialURI == query {
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
