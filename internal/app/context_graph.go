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
	for index := range result.nodes {
		result.nodes[index].Path = graph.TopicPath(result.nodes[index])
	}
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
		if node.ID == query || node.Label == query || node.Path == query {
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
		if query == "" || strings.Contains(strings.ToLower(strings.Join([]string{node.ID, node.Path, node.Label, node.Kind, node.MaterialURI, node.Locator, node.Content}, " ")), query) {
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
	if parent := topicParent(node.Path); parent != "" {
		result.Paths = g.branches(parent, 12)
	}
	return result
}

func (g contextGraph) branches(prefix string, limit int) []string {
	prefix = strings.TrimSuffix(strings.TrimSpace(prefix), ".")
	seen := map[string]bool{}
	var result []string
	for _, node := range g.nodes {
		path := node.Path
		if path == "" || path != prefix && !strings.HasPrefix(path, prefix+".") {
			continue
		}
		branch := path
		if rest := strings.TrimPrefix(path, prefix+"."); path != prefix {
			if segment, _, found := strings.Cut(rest, "."); found {
				branch = prefix + "." + segment
			}
		}
		if !seen[branch] {
			seen[branch] = true
			result = append(result, branch)
		}
	}
	sort.Strings(result)
	if limit > 0 && len(result) > limit {
		result = result[:limit]
	}
	return result
}

func topicParent(path string) string {
	last := strings.LastIndex(path, ".")
	if last < 0 {
		return ""
	}
	return path[:last]
}

func topicBridge(source, target string) []string {
	left, right := strings.Split(source, "."), strings.Split(target, ".")
	common := 0
	for common < len(left) && common < len(right) && left[common] == right[common] {
		common++
	}
	if common == 0 {
		return nil
	}
	var result []string
	for end := len(left); end >= common; end-- {
		result = append(result, strings.Join(left[:end], "."))
	}
	for end := common + 1; end <= len(right); end++ {
		result = append(result, strings.Join(right[:end], "."))
	}
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
		bridge := topicBridge(source.Path, target.Path)
		if len(bridge) == 0 {
			return graph.Path{}, sql.ErrNoRows
		}
		return graph.Path{Nodes: []graph.Node{source, target}, TopicPaths: bridge}, nil
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
	result := graph.Path{Edges: edges, TopicPaths: topicBridge(source.Path, target.Path)}
	for _, id := range ids {
		result.Nodes = append(result.Nodes, byID[id])
	}
	return result, nil
}
