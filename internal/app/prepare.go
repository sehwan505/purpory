package app

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

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
		WorkingDirectory: s.project.Root, TokenBudget: tokenBudget,
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
		request.WorkingDirectory = s.project.Root
	}
	if request.TokenBudget == 0 {
		request.TokenBudget = 2_000
	}
	request, err := contextprepare.ValidateRequest(request)
	if err != nil {
		return PrepareResult{}, err
	}
	request.ActivePaths = normalizeActivePaths(s.project.Root, request.ActivePaths)
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
		if node.Owner == graph.OwnerObserved && node.State == graph.StateActive {
			nodes = append(nodes, node)
		}
	}
	physicalGraph := newContextGraph(memories, graphNodes, graphEdges)
	workspace, err := s.store.Workspace(ctx, s.project)
	if err != nil {
		return PrepareResult{}, err
	}
	prior, err := s.store.SessionDeliveryHashes(ctx, s.project.ID, request.SessionID)
	if err != nil {
		return PrepareResult{}, err
	}
	openRequests, err := s.store.OpenContextRequestCount(ctx, s.project.ID)
	if err != nil {
		return PrepareResult{}, err
	}
	catalog := prepareCatalog(s.project.ID, memories, nodes, workspace, len(prior), openRequests)
	request.PriorKeys = mapKeys(prior)
	request.Catalog = catalog
	request.Orientation = prepareOrientation(s.project.Root, request.Message, memories, prior)

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

	result := PrepareResult{
		SchemaVersion: contextprepare.SchemaVersion, Proposal: proposal, Model: model, Fallback: fallback,
		Deliveries: []contextprepare.Delivery{}, Omitted: []contextprepare.Omitted{}, Awareness: []contextprepare.Awareness{},
		Context: contextprepare.Context{Catalog: catalog},
	}
	switch proposal.Action {
	case "skip":
		result.Action = "skip"
	case "ask":
		result.Action = "ask"
		result.Clarification = proposal.Clarification
	default:
		if err := s.searchAndDeliver(ctx, request, nodes, physicalGraph, memories, workspace, prior, &result); err != nil {
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
	if result.Action == "retrieve" && len(result.Awareness) > 0 {
		if err := s.store.SaveAwarenessExposures(ctx, s.project.ID, request.SessionID, result.Awareness); err != nil {
			return PrepareResult{}, err
		}
	}
	inputHash := contextprepare.Hash(request.Message)
	var inputText *string
	if request.RetainInput {
		inputText = &request.Message
	}
	decisionID, err := s.store.SavePrepareDecision(ctx, contextprepare.DecisionRecord{
		ProjectID: s.project.ID, SessionID: request.SessionID, InputHash: inputHash, InputText: inputText,
		Proposal: result.Proposal, Action: result.Action, Deliveries: result.Deliveries, RequestID: result.RequestID,
		Model: result.Model, Fallback: result.Fallback,
	})
	if err != nil {
		return PrepareResult{}, err
	}
	result.DecisionID = decisionID
	return result, nil
}

func (s *Service) searchAndDeliver(
	ctx context.Context,
	request contextprepare.Request,
	nodes []graph.Node,
	physicalGraph contextGraph,
	memories []memory.Memory,
	workspace project.Workspace,
	prior map[string]string,
	result *PrepareResult,
) error {
	query := request.Message
	if result.Proposal.Query != nil {
		query = *result.Proposal.Query
	}
	allCandidates := prepareCandidates(s.project.Root, nil, memories, nodes, workspace)
	for _, node := range nodes {
		if node.Kind == "material" && node.Content == "" {
			allCandidates = append(allCandidates, prepareNodeCandidate(node))
		}
	}
	semantic, err := s.semanticMatches(ctx, query, physicalGraph.nodes, 0)
	if err != nil {
		return err
	}
	searchable := make([]contextprepare.Candidate, 0, len(allCandidates))
	for _, candidate := range allCandidates {
		if deliverableCandidate(candidate) {
			searchable = append(searchable, candidate)
		}
	}
	semanticSeeds := semanticCandidates(allCandidates, semantic)
	bm25, terms := contextprepare.BM25(searchable, query, result.Proposal.Keywords)
	semanticLane := expandGraphCandidates(semanticSeeds, allCandidates, physicalGraph.edges, 2)
	bm25Lane := expandGraphCandidates(bm25, allCandidates, physicalGraph.edges, 2)
	ranked := uniqueCandidates(semanticSeeds, bm25, semanticLane, bm25Lane)
	search := &contextprepare.Search{Query: query, Scopes: normalizedScopes(result.Proposal.Scopes), Terms: terms, Candidates: ranked}
	result.Context.Search = search

	deliveries, omitted, sessionDeliveries := deliverCandidates(ranked, prior, request.TokenBudget)
	result.Deliveries, result.Omitted = deliveries, omitted

	if len(sessionDeliveries) > 0 {
		agent := sessionAgent(request.SessionID)
		if err := s.SaveSessionAt(ctx, request.WorkingDirectory, request.SessionID, agent, "active"); err != nil {
			return err
		}
		if err := s.store.SaveDeliveries(ctx, s.project.ID, request.SessionID, sessionDeliveries); err != nil {
			return err
		}
		nodeIDs := make([]string, len(deliveries))
		for index, item := range deliveries {
			nodeIDs[index] = item.NodeID
		}
		if err := s.store.MarkAwarenessFollowUps(ctx, s.project.ID, request.SessionID, nodeIDs); err != nil {
			return err
		}
		for _, item := range deliveries {
			if strings.HasPrefix(item.Key, "material.") || strings.HasPrefix(item.Key, "resource.") {
				continue
			}
			if err := s.store.RecordMemoryUsage(ctx, s.project.ID, item.Key, "selected"); err != nil {
				return err
			}
		}
	}
	var rendered strings.Builder
	for _, item := range deliveries {
		rendered.WriteString(strings.TrimSpace(item.Rendered))
		rendered.WriteString("\n")
	}
	result.Context.Rendered = rendered.String()
	for _, item := range deliveries {
		result.Context.EstimatedTokens += item.EstimatedTokens
	}
	if result.Context.Rendered != "" {
		hash := contextprepare.Hash(result.Context.Rendered)
		result.Context.Hash = &hash
	}
	if len(deliveries) > 0 || len(result.Awareness) > 0 {
		result.Action = "retrieve"
	} else if hasOmission(omitted, "already-delivered") || result.Proposal.ReasonCode == "GATE_UNAVAILABLE" {
		result.Action = "skip"
	} else {
		result.Action = "ask"
	}
	return nil
}

func prepareCatalog(projectID string, memories []memory.Memory, nodes []graph.Node, workspace project.Workspace, priorCount, openRequests int) contextprepare.Catalog {
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
		Counts:          contextprepare.Counts{Human: len(memories), Nodes: len(nodes), Resource: len(workspace.Resources), PriorCount: priorCount, OpenRequests: openRequests},
		TopicNamespaces: sortedCounts(prefixes, 32), NodeKinds: sortedCounts(kinds, 32),
	}
}

func prepareOrientation(root, message string, memories []memory.Memory, prior map[string]string) []contextprepare.Orientation {
	candidates := prepareCandidates(root, []string{"human", "session"}, memories, nil, project.Workspace{})
	ranked, _ := contextprepare.Rank(candidates, message, nil, nil, mapKeys(prior))
	result := make([]contextprepare.Orientation, 0, 2)
	seen := map[string]bool{}
	for _, candidate := range ranked {
		result = append(result, contextprepare.Orientation{Key: candidate.Key, Label: candidate.Label, Kind: candidate.Kind, Source: candidate.Source, Preview: previewText(candidate.Content)})
		seen[candidate.Key] = true
		if len(result) == 2 {
			return result
		}
	}
	byKey := map[string]contextprepare.Candidate{}
	for _, candidate := range candidates {
		byKey[candidate.Key] = candidate
	}
	for _, key := range mapKeys(prior) {
		candidate, found := byKey[key]
		if !found || seen[key] {
			continue
		}
		result = append(result, contextprepare.Orientation{Key: candidate.Key, Label: candidate.Label, Kind: candidate.Kind, Source: candidate.Source, Preview: previewText(candidate.Content)})
		if len(result) == 2 {
			break
		}
	}
	return result
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

func prepareCandidates(root string, scopes []string, memories []memory.Memory, nodes []graph.Node, workspace project.Workspace) []contextprepare.Candidate {
	selected := scopeSet(scopes)
	// ponytail: rank the current project snapshot in memory; move scoring into SQL only after project-size profiling says this is the bottleneck.
	result := make([]contextprepare.Candidate, 0, len(memories)+len(nodes)+len(workspace.Resources))
	if selected["human"] || selected["session"] {
		for _, entry := range memories {
			content, mode := resolveMemory(root, entry)
			source := ""
			if entry.Source != nil {
				source = *entry.Source
			}
			updated, _ := time.Parse(time.RFC3339, entry.UpdatedAt)
			result = append(result, contextprepare.Candidate{
				NodeID: memoryNodeID(entry), Key: entry.Key,
				Namespace: "memory", Label: entry.Key, Kind: string(entry.Kind), Origin: "human", Source: source, Content: content, Mode: mode,
				UpdatedAt: updated.Unix(),
			})
		}
	}
	if selected["material"] {
		for _, node := range nodes {
			if node.Kind == "material" && node.Content == "" {
				continue
			}
			result = append(result, prepareNodeCandidate(node))
		}
	}
	if selected["resource"] {
		for _, resource := range workspace.Resources {
			result = append(result, contextprepare.Candidate{
				NodeID: resource.ID, Key: "resource." + resource.ID, Namespace: "resource", Label: resource.Label,
				Kind: resource.Provider, Origin: "observed", Source: resource.Identity, Content: resource.Identity, Mode: "resource",
			})
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
		NodeID: node.ID, Key: "material." + node.ID[:min(20, len(node.ID))], Namespace: "material",
		Label: node.Label, Kind: kind, Origin: "structural", Source: source, Content: node.Content, Mode: "context-graph",
	}
}

func deliverableCandidate(candidate contextprepare.Candidate) bool {
	return candidate.Namespace != "resource" && strings.TrimSpace(candidate.Content) != ""
}

func expandGraphCandidates(seeds, all []contextprepare.Candidate, edges []graph.Edge, depth int) []contextprepare.Candidate {
	byID := map[string]contextprepare.Candidate{}
	for _, candidate := range all {
		byID[candidate.NodeID] = candidate
	}
	type link struct{ id, relation string }
	adjacent := map[string][]link{}
	for _, edge := range edges {
		adjacent[edge.SourceID] = append(adjacent[edge.SourceID], link{edge.TargetID, edge.Relation})
		adjacent[edge.TargetID] = append(adjacent[edge.TargetID], link{edge.SourceID, edge.Relation})
	}
	for id := range adjacent {
		sort.Slice(adjacent[id], func(i, j int) bool {
			return adjacent[id][i].relation+adjacent[id][i].id < adjacent[id][j].relation+adjacent[id][j].id
		})
	}
	type step struct {
		id      string
		score   float64
		signals []string
	}
	seen := map[string]bool{}
	frontier := make([]step, 0, len(seeds))
	result := make([]contextprepare.Candidate, 0, len(seeds))
	for _, seed := range seeds {
		if seen[seed.NodeID] {
			continue
		}
		seen[seed.NodeID] = true
		frontier = append(frontier, step{seed.NodeID, seed.Score, seed.Signals})
		result = append(result, seed)
	}
	for level := 0; level < depth && len(frontier) > 0; level++ {
		var next []step
		for position := 0; ; position++ {
			added := false
			for _, current := range frontier {
				if position >= len(adjacent[current.id]) {
					continue
				}
				added = true
				neighbor := adjacent[current.id][position]
				if seen[neighbor.id] {
					continue
				}
				seen[neighbor.id] = true
				signals := append(append([]string(nil), current.signals...), "path:"+neighbor.relation)
				next = append(next, step{neighbor.id, current.score / 2, signals})
				candidate, found := byID[neighbor.id]
				if !found || !deliverableCandidate(candidate) {
					continue
				}
				candidate.Score = current.score / 2
				candidate.Signals = signals
				result = append(result, candidate)
			}
			if !added {
				break
			}
		}
		frontier = next
	}
	return result
}

func uniqueCandidates(lanes ...[]contextprepare.Candidate) []contextprepare.Candidate {
	seen := map[string]bool{}
	var result []contextprepare.Candidate
	for _, lane := range lanes {
		for _, candidate := range lane {
			if seen[candidate.NodeID] {
				continue
			}
			seen[candidate.NodeID] = true
			result = append(result, candidate)
		}
	}
	return result
}

func deliverCandidates(candidates []contextprepare.Candidate, prior map[string]string, budget int) ([]contextprepare.Delivery, []contextprepare.Omitted, []project.Delivery) {
	remaining := budget
	deliveries := []contextprepare.Delivery{}
	omitted := []contextprepare.Omitted{}
	var sessionDeliveries []project.Delivery
	for _, candidate := range candidates {
		rendered := renderCandidate(candidate)
		hash := contextprepare.Hash(rendered)
		if prior[candidate.Key] == hash {
			omitted = append(omitted, contextprepare.Omitted{Key: candidate.Key, Reason: "already-delivered"})
			continue
		}
		tokens := contextprepare.EstimateTokens(rendered)
		truncated := false
		if tokens > remaining {
			if len(deliveries) > 0 || remaining < contextprepare.MinTokenBudget {
				omitted = append(omitted, contextprepare.Omitted{NodeID: candidate.NodeID, Key: candidate.Key, Reason: "token-budget", EstimatedTokens: tokens})
				continue
			}
			rendered, truncated = contextprepare.Truncate(rendered, remaining)
			tokens = contextprepare.EstimateTokens(rendered)
			hash = contextprepare.Hash(rendered)
		}
		delivery := contextprepare.Delivery{
			NodeID: candidate.NodeID, Key: candidate.Key, Kind: candidate.Kind, Origin: candidate.Origin,
			Mode: candidate.Mode, Truncated: truncated, Score: candidate.Score, Signals: candidate.Signals,
			EstimatedTokens: tokens, Hash: hash, Rendered: rendered,
		}
		deliveries = append(deliveries, delivery)
		sessionDeliveries = append(sessionDeliveries, project.Delivery{Key: candidate.Key, Kind: candidate.Kind, Label: candidate.Label, Source: candidate.Source, Preview: previewText(rendered), Hash: hash})
		remaining -= tokens
	}
	return deliveries, omitted, sessionDeliveries
}

func renderCandidate(candidate contextprepare.Candidate) string {
	var b strings.Builder
	fmt.Fprintf(&b, "## %s\n\n", candidate.Label)
	if candidate.Source != "" {
		fmt.Fprintf(&b, "Source: %s\n", candidate.Source)
	}
	if candidate.Kind != "" {
		fmt.Fprintf(&b, "Kind: %s\n", candidate.Kind)
	}
	if candidate.Content != "" {
		b.WriteString("\n")
		b.WriteString(strings.TrimSpace(candidate.Content))
		b.WriteString("\n")
	}
	return b.String()
}

func resolveMemory(root string, entry memory.Memory) (string, string) {
	if entry.Value != nil {
		return *entry.Value, "inline"
	}
	if entry.Source == nil {
		return "", "unresolved"
	}
	source := *entry.Source
	if strings.HasPrefix(source, "http://") || strings.HasPrefix(source, "https://") {
		return source, "external"
	}
	reference := strings.SplitN(source, "#", 2)[0]
	reference = strings.TrimPrefix(reference, "@repo/")
	reference = strings.TrimPrefix(reference, "@root/")
	path := reference
	if !filepath.IsAbs(path) {
		path = filepath.Join(root, filepath.FromSlash(path))
	}
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil || !withinRoot(root, resolved) {
		return source, "unresolved"
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return source, "unresolved"
	}
	if info.IsDir() {
		var files []string
		_ = filepath.WalkDir(resolved, func(path string, entry fs.DirEntry, walkErr error) error {
			if walkErr != nil || len(files) >= 250 {
				return walkErr
			}
			if entry.IsDir() {
				return nil
			}
			relative, err := filepath.Rel(root, path)
			if err == nil {
				files = append(files, filepath.ToSlash(relative))
			}
			return nil
		})
		sort.Strings(files)
		return strings.Join(files, "\n"), "pointer-dir"
	}
	file, err := os.Open(resolved)
	if err != nil {
		return source, "unresolved"
	}
	defer file.Close()
	content, err := io.ReadAll(io.LimitReader(file, (1<<20)+1))
	if err != nil {
		return source, "unresolved"
	}
	if len(content) > 1<<20 {
		return string(content[:1<<20]) + "\n\n[truncated after 1048576 bytes]", "pointer-file"
	}
	return string(content), "pointer-file"
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

func normalizedScopes(scopes []string) []string {
	if len(scopes) == 0 {
		return []string{"human", "material", "resource", "session"}
	}
	return scopes
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

func scopeSet(scopes []string) map[string]bool {
	result := map[string]bool{}
	for _, scope := range normalizedScopes(scopes) {
		result[scope] = true
	}
	return result
}

func hasOmission(items []contextprepare.Omitted, reason string) bool {
	for _, item := range items {
		if item.Reason == reason {
			return true
		}
	}
	return false
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
