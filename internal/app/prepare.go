package app

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
	"github.com/sehwan505/purpory/internal/project"
)

func (s *Service) Prepare(ctx context.Context, message string, tokenBudget int) (PrepareResult, error) {
	if tokenBudget <= 0 {
		tokenBudget = 2_000
	}
	return s.PrepareContext(ctx, contextprepare.Request{
		Message: message, SessionID: currentSessionID(""), ProjectID: s.project.ID,
		WorkingDirectory: s.currentRoot(ctx), TokenBudget: tokenBudget,
	})
}

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
	request.ActivePaths = normalizeActivePaths(s.currentRoot(ctx), request.ActivePaths)
	if request.ProjectID != s.project.ID {
		return PrepareResult{}, errors.New("prepare context: requested project is not active")
	}

	memories, err := s.store.Memories(ctx, s.project.ID, "")
	if err != nil {
		return PrepareResult{}, err
	}
	graphNodes, graphEdges, err := s.store.Graph(ctx, s.project.ID)
	if err != nil {
		return PrepareResult{}, err
	}
	var nodes []graph.Node
	for _, node := range graphNodes {
		if node.State == graph.StateActive {
			nodes = append(nodes, node)
		}
	}
	physicalGraph := newContextGraph(memories, graphNodes, graphEdges)
	workspace, err := s.store.Workspace(ctx, s.project)
	if err != nil {
		return PrepareResult{}, err
	}
	opened, err := s.store.SessionItemKeys(ctx, s.project.ID, request.SessionID)
	if err != nil {
		return PrepareResult{}, err
	}
	openRequests, err := s.store.OpenContextRequestCount(ctx, s.project.ID)
	if err != nil {
		return PrepareResult{}, err
	}
	catalog := prepareCatalog(s.project.ID, memories, nodes, workspace, len(opened), openRequests)
	request.OpenedNodes = mapKeys(opened)
	request.Catalog = catalog

	proposal := contextprepare.Fallback(request.Message)
	var model contextprepare.Model
	var fallback *string
	if !contextprepare.IsGreeting(request.Message) && s.gate != nil {
		provided, providerErr := s.gate.Propose(ctx, request)
		if providerErr != nil {
			reason := providerErr.Error()
			fallback = &reason
		} else if validated, validationErr := contextprepare.ValidateProposal(provided.Proposal); validationErr != nil {
			reason := validationErr.Error()
			fallback = &reason
		} else {
			proposal = validated
			modelID, revision, latency := provided.ModelID, provided.Revision, provided.LatencyMS
			model = contextprepare.Model{ID: &modelID, Revision: &revision, LatencyMS: &latency}
		}
	} else if !contextprepare.IsGreeting(request.Message) && s.gate == nil {
		reason := "gate provider is not configured"
		fallback = &reason
	}

	result := PrepareResult{SchemaVersion: contextprepare.SchemaVersion, Proposal: proposal, Model: model, Fallback: fallback}
	switch proposal.Action {
	case "skip":
		result.Action = "skip"
	case "ask":
		result.Action = "ask"
		result.Clarification = proposal.Clarification
	default:
		if err := s.prepareHints(ctx, request, physicalGraph, opened, &result); err != nil {
			return PrepareResult{}, err
		}
	}
	if result.Action == "ask" {
		need := request.Message
		if proposal.Query != nil {
			need = *proposal.Query
		}
		requestID, err := s.store.EnsureContextRequest(ctx, s.project.ID, request.SessionID, need)
		if err != nil {
			return PrepareResult{}, err
		}
		result.RequestID = &requestID
		if result.Clarification == nil {
			message := "Purpory에서 충분한 근거를 찾지 못했습니다. 필요한 결정이나 프로젝트 정보를 알려주세요."
			result.Clarification = &message
		}
	} else {
		result.Clarification = nil
	}
	inputHash := contextprepare.Hash(request.Message)
	var inputText *string
	if request.RetainInput {
		inputText = &request.Message
	}
	decisionID, err := s.store.SavePrepareDecision(ctx, contextprepare.DecisionRecord{
		ProjectID: s.project.ID, SessionID: request.SessionID, InputHash: inputHash, InputText: inputText,
		Proposal: result.Proposal, Action: result.Action, RequestID: result.RequestID,
		Hints: result.Hints, Model: result.Model, Fallback: result.Fallback,
	})
	if err != nil {
		return PrepareResult{}, err
	}
	result.DecisionID = decisionID
	return result, nil
}

func (s *Service) prepareHints(
	ctx context.Context,
	request contextprepare.Request,
	physicalGraph contextGraph,
	opened map[string]bool,
	result *PrepareResult,
) error {
	query := request.Message
	if result.Proposal.Query != nil {
		query = *result.Proposal.Query
	}
	allCandidates := prepareCandidates(physicalGraph.nodes)
	semantic, err := s.semanticMatches(ctx, query, physicalGraph.nodes, 8)
	if err != nil {
		return err
	}
	semanticSeeds := semanticCandidates(allCandidates, semantic)
	bm25, _ := contextprepare.BM25(allCandidates, query, result.Proposal.Keywords)
	result.Hints = prepareHintMap(semanticSeeds, bm25, physicalGraph.nodes, physicalGraph.edges, opened, request.TokenBudget)
	if result.Hints != nil {
		agent := sessionAgent(request.SessionID)
		if err := s.SaveSessionAt(ctx, request.WorkingDirectory, request.SessionID, agent, "active"); err != nil {
			return err
		}
		result.Action = "retrieve"
	} else if result.Proposal.ReasonCode == "GATE_UNAVAILABLE" {
		result.Action = "skip"
	} else {
		result.Action = "ask"
	}
	return nil
}

func prepareCatalog(projectID string, memories []memory.Memory, nodes []graph.Node, workspace project.Workspace, openedCount, openRequests int) contextprepare.Catalog {
	prefixes := map[string]int{}
	for _, entry := range memories {
		prefix := strings.SplitN(entry.Key, ".", 2)[0]
		prefixes[prefix]++
	}
	kinds := map[string]int{}
	for _, node := range nodes {
		kinds[node.Kind]++
	}
	return contextprepare.Catalog{
		SchemaVersion: contextprepare.ContextVersion, ProjectID: projectID,
		Counts:          contextprepare.Counts{Human: len(memories), Nodes: len(nodes), Resource: len(workspace.Resources), OpenedCount: openedCount, OpenRequests: openRequests},
		TopicNamespaces: sortedCounts(prefixes, 32), NodeKinds: sortedCounts(kinds, 32),
	}
}

func sortedCounts(values map[string]int, limit int) []contextprepare.NamespaceCount {
	result := make([]contextprepare.NamespaceCount, 0, len(values))
	for name, count := range values {
		result = append(result, contextprepare.NamespaceCount{Name: name, Count: count})
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Count != result[j].Count {
			return result[i].Count > result[j].Count
		}
		return result[i].Name < result[j].Name
	})
	return result[:min(len(result), limit)]
}

func prepareCandidates(nodes []graph.Node) []contextprepare.Candidate {
	result := make([]contextprepare.Candidate, 0, len(nodes))
	for _, node := range nodes {
		if node.State == graph.StateActive && strings.TrimSpace(node.Content) != "" {
			result = append(result, prepareNodeCandidate(node))
		}
	}
	return result
}

func prepareNodeCandidate(node graph.Node) contextprepare.Candidate {
	source := node.MaterialURI
	if node.Locator != "" {
		source += "#" + node.Locator
	}
	kind := node.Kind
	if node.Subkind != "" {
		kind = node.Subkind
	}
	return contextprepare.Candidate{
		NodeID: node.ID, Key: node.ID, Label: node.Label, Kind: kind, Source: source, Content: node.Content,
	}
}

func prepareHintMap(semantic, lexical []contextprepare.Candidate, nodes []graph.Node, edges []graph.Edge, opened map[string]bool, budget int) *contextprepare.HintMap {
	byID := make(map[string]graph.Node, len(nodes))
	for _, node := range nodes {
		if node.Path == "" {
			node.Path = graph.TopicPath(node)
		}
		byID[node.ID] = node
	}
	hints := &contextprepare.HintMap{}
	selected := map[string]bool{}
	branches := map[string]bool{}
	addAnchor := func(candidate contextprepare.Candidate, match string) bool {
		if len(hints.Nodes) == 3 || selected[candidate.NodeID] || opened[candidate.NodeID] || opened[candidate.Key] {
			return false
		}
		node, found := byID[candidate.NodeID]
		if !found {
			return false
		}
		trial := *hints
		trial.Nodes = append(append([]contextprepare.HintNode(nil), hints.Nodes...), prepareHintNode(node, match))
		if contextprepare.EstimateTokens(contextprepare.RenderHintMap(&trial)) > budget {
			return false
		}
		hints.Nodes = trial.Nodes
		selected[node.ID] = true
		branches[topicBranch(node.Path)] = true
		return true
	}
	if len(semantic) > 0 {
		addAnchor(semantic[0], "semantic")
	}
	for _, candidate := range lexical {
		if addAnchor(candidate, "bm25") {
			break
		}
	}
	alternateAdded := false
	for _, durableOnly := range []bool{true, false} {
		for _, lane := range []struct {
			items []contextprepare.Candidate
			match string
		}{{semantic, "semantic"}, {lexical, "bm25"}} {
			for _, candidate := range lane.items {
				node, found := byID[candidate.NodeID]
				if found && (!durableOnly || node.Owner == graph.OwnerDurable) && !branches[topicBranch(node.Path)] && addAnchor(candidate, lane.match+":alternate-branch") {
					alternateAdded = true
					break
				}
			}
			if alternateAdded {
				break
			}
		}
		if alternateAdded {
			break
		}
	}
	for _, lane := range []struct {
		items []contextprepare.Candidate
		match string
	}{{semantic, "semantic"}, {lexical, "bm25"}} {
		for _, candidate := range lane.items {
			addAnchor(candidate, lane.match)
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

func topicBranch(path string) string {
	branch, _, _ := strings.Cut(path, ".")
	return branch
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

func withinRoot(root, path string) bool {
	root, rootErr := filepath.Abs(root)
	path, pathErr := filepath.Abs(path)
	if rootErr != nil || pathErr != nil {
		return false
	}
	relative, err := filepath.Rel(root, path)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

func normalizeActivePaths(root string, paths []string) []string {
	result := make([]string, 0, len(paths))
	for _, value := range paths {
		path := filepath.Clean(value)
		if filepath.IsAbs(path) {
			path = canonicalPath(path)
		}
		if filepath.IsAbs(path) && withinRoot(root, path) {
			if relative, err := filepath.Rel(root, path); err == nil {
				path = relative
			}
		}
		result = append(result, contextprepare.NormalizePath(path))
	}
	return result
}

func canonicalPath(path string) string {
	current := path
	var missing []string
	for {
		if _, err := os.Stat(current); err == nil {
			break
		}
		parent := filepath.Dir(current)
		if parent == current {
			return path
		}
		missing = append(missing, filepath.Base(current))
		current = parent
	}
	resolved, err := filepath.EvalSymlinks(current)
	if err != nil {
		return path
	}
	for index := len(missing) - 1; index >= 0; index-- {
		resolved = filepath.Join(resolved, missing[index])
	}
	return resolved
}

func mapKeys[V any](values map[string]V) []string {
	result := make([]string, 0, len(values))
	for key := range values {
		result = append(result, key)
	}
	sort.Strings(result)
	return result
}

func previewText(value string) string {
	runes := []rune(strings.TrimSpace(value))
	if len(runes) <= 320 {
		return string(runes)
	}
	return string(runes[:320]) + "…"
}
