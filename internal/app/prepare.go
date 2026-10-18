package app

import (
	"context"
	"errors"
	"strings"

	"github.com/sehwan505/purpory/internal/graph"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
)

func (s *Service) PrepareContext(ctx context.Context, request contextprepare.Request) (PrepareResult, error) {
	if strings.TrimSpace(request.SessionID) == "" {
		request.SessionID = currentSessionID("")
	}
	if strings.TrimSpace(request.ProjectID) == "" {
		request.ProjectID = s.project.ID
	}
	if strings.TrimSpace(request.WorkingDirectory) == "" {
		request.WorkingDirectory = s.currentRoot(ctx)
	}
	if request.TokenBudget == 0 {
		request.TokenBudget = 2_000
	}
	request, err := contextprepare.ValidateRequest(request)
	if err != nil {
		return PrepareResult{}, err
	}
	if request.ProjectID != s.project.ID {
		return PrepareResult{}, errors.New("prepare context: requested project is not active")
	}
	result := PrepareResult{SchemaVersion: contextprepare.SchemaVersion, Action: "skip"}
	if contextprepare.IsGreeting(request.Message) || len([]rune(request.Message)) > contextprepare.MaxQueryChars {
		return result, nil
	}
	physical, err := s.contextGraph(ctx)
	if err != nil {
		return PrepareResult{}, err
	}
	opened, err := s.store.SessionItemKeys(ctx, s.project.ID, request.SessionID)
	if err != nil {
		return PrepareResult{}, err
	}
	history, err := s.navigationNodeIDs(ctx, request.SessionID)
	if err != nil {
		return PrepareResult{}, err
	}
	semantic, err := s.semanticMatches(ctx, request.Message, physical.nodes, 8)
	if err != nil {
		return PrepareResult{}, err
	}
	exact := physical.seeds(request.Message)
	ranked := graph.TypedPPR(physical.nodes, physical.edges, pprSeeds(semantic, exact, append(history, opened...)))
	result.Hints = prepareHintMap(ranked, physical.nodes, physical.edges, semantic, exact, opened, request.TokenBudget)
	if result.Hints == nil {
		return result, nil
	}
	if err := s.SaveSessionAt(ctx, request.WorkingDirectory, request.SessionID, sessionAgent(request.SessionID), "active"); err != nil {
		return PrepareResult{}, err
	}
	result.Action = "retrieve"
	if request.SessionID != "anon" {
		events := make([]contextprepare.NavigationEvent, 0, len(result.Hints.Nodes))
		for _, node := range result.Hints.Nodes {
			events = append(events, contextprepare.NavigationEvent{Action: "deliver", TargetNodeID: node.ID})
		}
		if err := s.store.AppendNavigation(ctx, s.project.ID, request.SessionID, events); err != nil {
			return PrepareResult{}, err
		}
	}
	return result, nil
}

func prepareHintMap(ranked []graph.Rank, nodes []graph.Node, edges []graph.Edge, semantic []semanticMatch, exact, opened []string, budget int) *contextprepare.HintMap {
	openedSet := make(map[string]bool, len(opened))
	for _, id := range opened {
		openedSet[id] = true
	}
	byID := make(map[string]graph.Node, len(nodes))
	for _, node := range nodes {
		if node.Path == "" {
			node.Path = graph.TopicPath(node)
		}
		byID[node.ID] = node
	}
	hints := &contextprepare.HintMap{}
	selected := map[string]bool{}
	semanticSet := map[string]bool{}
	for _, match := range semantic {
		semanticSet[match.node.ID] = true
	}
	exactSet := map[string]bool{}
	for _, id := range exact {
		exactSet[id] = true
	}
	for _, rank := range ranked {
		if len(hints.Nodes) == 3 || openedSet[rank.NodeID] {
			continue
		}
		node, found := byID[rank.NodeID]
		if !found || node.State != graph.StateActive || strings.TrimSpace(node.Content) == "" {
			continue
		}
		match := "typed-ppr"
		if exactSet[node.ID] {
			match = "exact-seed"
		} else if semanticSet[node.ID] {
			match = "semantic-seed"
		}
		trial := *hints
		trial.Nodes = append(append([]contextprepare.HintNode(nil), hints.Nodes...), prepareHintNode(node, match))
		if contextprepare.EstimateTokens(contextprepare.RenderHintMap(&trial)) <= budget {
			hints.Nodes = trial.Nodes
			selected[node.ID] = true
		}
	}
	if len(hints.Nodes) == 0 {
		return nil
	}
	for _, edge := range edges {
		if selected[edge.SourceID] && selected[edge.TargetID] {
			trial := *hints
			trial.Edges = append(append([]contextprepare.HintEdge(nil), hints.Edges...), contextprepare.HintEdge{SourceID: edge.SourceID, TargetID: edge.TargetID, Relation: edge.Relation})
			if contextprepare.EstimateTokens(contextprepare.RenderHintMap(&trial)) <= budget {
				hints.Edges = trial.Edges
			}
		}
	}
	return hints
}

func prepareHintNode(node graph.Node, match string) contextprepare.HintNode {
	source := node.MaterialURI
	if node.Locator != "" {
		source += "#" + node.Locator
	}
	return contextprepare.HintNode{
		ID: node.ID, Path: node.Path, Label: node.Label, Kind: node.Kind, Subkind: node.Subkind,
		State: node.State, Source: source, Match: match, Provenance: node.Provenance,
	}
}

func previewText(value string) string {
	runes := []rune(strings.TrimSpace(value))
	if len(runes) <= 320 {
		return string(runes)
	}
	return string(runes[:320]) + "…"
}
