// Package app coordinates product capabilities independently of Wails and CLI.
package app

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/sehwan505/purpory/internal/extract"
	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/material"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/ollama"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
	"github.com/sehwan505/purpory/internal/project"
	"github.com/sehwan505/purpory/internal/resolve"
	"github.com/sehwan505/purpory/internal/store"
)

const Version = "0.1.0"

type Service struct {
	project      project.Project
	store        *store.Store
	databasePath string
	ollama       *ollama.Client
	workspace    WorkspaceObserver
	gate         contextprepare.Provider
	update       sync.Mutex
}

type WorkspaceObserver interface {
	Observe(context.Context, string) (project.Workspace, error)
}

type Status struct {
	Version string          `json:"version"`
	Project project.Project `json:"project"`
}

type QueryResult struct {
	Seeds []graph.Node `json:"seeds"`
	Nodes []graph.Node `json:"nodes"`
	Edges []graph.Edge `json:"edges"`
	Paths []string     `json:"paths,omitempty"`
}

type GraphResult struct {
	Nodes      []graph.Node `json:"nodes"`
	Edges      []graph.Edge `json:"edges"`
	TotalNodes int          `json:"totalNodes"`
	TotalEdges int          `json:"totalEdges"`
	Truncated  bool         `json:"truncated"`
}

type PrepareResult = contextprepare.Result

type ExplainResult struct {
	Memory *memory.Memory     `json:"memory,omitempty"`
	Graph  *graph.Explanation `json:"graph,omitempty"`
}

type UpdateResult struct {
	MaterialCount int              `json:"materialCount"`
	Processed     int              `json:"extracted"`
	EntityCount   int              `json:"entityCount"`
	RelationCount int              `json:"relationCount"`
	Changes       material.Changes `json:"changes"`
	Warnings      []string         `json:"warnings,omitempty"`
}

func Open(ctx context.Context, root, databasePath, projectID string) (*Service, error) {
	return OpenWithObserver(ctx, root, databasePath, projectID, project.Local{})
}

func RegisterProject(ctx context.Context, root, databasePath, projectID, name string) (project.Project, error) {
	return registerProject(ctx, root, databasePath, projectID, name, project.Local{})
}

func registerProject(ctx context.Context, root, databasePath, projectID, name string, observer WorkspaceObserver) (project.Project, error) {
	workspace, err := observer.Observe(ctx, root)
	if err != nil {
		return project.Project{}, err
	}
	current := project.Identify(workspace.Project, projectID)
	if value := strings.TrimSpace(name); value != "" {
		current.Name = value
	}
	database, err := store.Open(ctx, databasePath)
	if err != nil {
		return project.Project{}, err
	}
	defer database.Close()
	if err := database.SaveProject(ctx, current); err != nil {
		return project.Project{}, err
	}
	if err := database.SaveWorkspace(ctx, current.ID, workspace.Resources); err != nil {
		return project.Project{}, err
	}
	return current, nil
}

func OpenWithObserver(ctx context.Context, root, databasePath, projectID string, observer WorkspaceObserver) (*Service, error) {
	if observer == nil {
		return nil, errors.New("open service: workspace observer is required")
	}
	workspace, err := observer.Observe(ctx, root)
	if err != nil {
		return nil, err
	}
	database, err := store.Open(ctx, databasePath)
	if err != nil {
		return nil, err
	}
	registered, err := database.ProjectForWorkspace(ctx, workspace, project.RequestedID(projectID))
	if err != nil {
		database.Close()
		return nil, err
	}
	current := registered
	current.Root = workspace.Project.Root
	if err := database.SaveWorkspace(ctx, current.ID, workspace.Resources); err != nil {
		database.Close()
		return nil, err
	}
	ollamaURL := strings.TrimSpace(os.Getenv("PURPORY_OLLAMA_URL"))
	if ollamaURL == "" {
		ollamaURL = "http://127.0.0.1:11434"
	}
	client, err := ollama.New(ollamaURL, 5*time.Second)
	if err != nil {
		database.Close()
		return nil, err
	}
	service := &Service{project: current, store: database, databasePath: databasePath, ollama: client, workspace: observer}
	gate, err := service.modelName(ctx, "gate")
	if err != nil {
		database.Close()
		return nil, err
	}
	service.gate = newGateProvider(client, gate.Model)
	return service, nil
}

func (s *Service) Close() error {
	return s.store.Close()
}

func (s *Service) Status() Status {
	return Status{Version: Version, Project: s.project}
}

func (s *Service) Projects(ctx context.Context) ([]project.Project, error) {
	return s.store.Projects(ctx)
}

func (s *Service) SelectProject(ctx context.Context, projectID string) (Status, error) {
	selected, err := s.store.Project(ctx, strings.TrimSpace(projectID))
	if err != nil {
		return Status{}, err
	}
	workspace, err := s.workspace.Observe(ctx, selected.Root)
	if err != nil {
		return Status{}, err
	}
	selected.Root = workspace.Project.Root
	if err := s.store.SaveWorkspace(ctx, selected.ID, workspace.Resources); err != nil {
		return Status{}, err
	}
	s.project = selected
	return s.Status(), nil
}

func (s *Service) Remember(ctx context.Context, key string, kind memory.Kind, value, source *string) (store.SaveResult, error) {
	entry, err := memory.New(s.project.ID, key, kind, value, source)
	if err != nil {
		return store.SaveResult{}, err
	}
	result, err := s.store.SaveMemory(ctx, entry)
	if err != nil {
		return store.SaveResult{}, err
	}
	if err := s.syncNodeEmbeddings(ctx, []string{memoryNodeID(entry)}); err != nil {
		return result, err
	}
	return result, nil
}

func (s *Service) Memories(ctx context.Context, prefix string) ([]memory.Memory, error) {
	return s.store.Memories(ctx, s.project.ID, strings.TrimSpace(prefix))
}

func (s *Service) MemoryVersions(ctx context.Context, key string) ([]memory.Version, error) {
	return s.store.MemoryVersions(ctx, s.project.ID, strings.TrimSpace(key))
}

func (s *Service) DeleteMemory(ctx context.Context, key string) (bool, error) {
	return s.store.DeleteMemory(ctx, s.project.ID, key)
}

func (s *Service) ConfirmMemory(ctx context.Context, key string) (bool, error) {
	return s.store.ConfirmMemory(ctx, s.project.ID, key)
}

func (s *Service) ReconcileMemoryBatch(ctx context.Context, changes []memory.BatchChange, apply bool, sessionID string) (memory.BatchResult, error) {
	if strings.TrimSpace(sessionID) == "" {
		sessionID = currentSessionID("")
	}
	result, err := s.store.PreviewMemoryBatch(ctx, s.project.ID, changes, apply, sessionID)
	if err != nil || !result.Applied {
		return result, err
	}
	var nodeIDs []string
	for _, change := range result.Changes {
		entry, loadErr := s.store.Memory(ctx, s.project.ID, change.Key)
		if loadErr != nil {
			return result, loadErr
		}
		nodeIDs = append(nodeIDs, memoryNodeID(entry))
	}
	if err := s.syncNodeEmbeddings(ctx, nodeIDs); err != nil {
		return result, err
	}
	return result, nil
}

func (s *Service) CreateNeedsReview(ctx context.Context, key, sourceType, sourceID, contentHash, reason string) (memory.Review, error) {
	return s.store.CreateNeedsReview(ctx, s.project.ID, key, sourceType, sourceID, contentHash, reason)
}

func (s *Service) NeedsReviews(ctx context.Context, status string) ([]memory.Review, error) {
	return s.store.NeedsReviews(ctx, s.project.ID, status)
}

func (s *Service) ResolveNeedsReview(ctx context.Context, reviewID int64, outcome string, resultVersionID *int64) (*memory.Review, error) {
	return s.store.ResolveNeedsReview(ctx, s.project.ID, reviewID, outcome, resultVersionID)
}

func (s *Service) ContextRequests(ctx context.Context, status string) ([]contextprepare.ContextRequest, error) {
	return s.store.ContextRequests(ctx, s.project.ID, status)
}

func (s *Service) ResolveContextRequest(ctx context.Context, requestID int64, key string) (bool, error) {
	return s.store.ResolveContextRequest(ctx, s.project.ID, requestID, key)
}

func (s *Service) ContextDecisions(ctx context.Context, limit int) ([]contextprepare.Decision, error) {
	return s.store.PrepareDecisions(ctx, s.project.ID, limit)
}

func (s *Service) ContextFeedback(ctx context.Context, feedback contextprepare.Feedback) (contextprepare.Feedback, error) {
	return s.store.SavePrepareFeedback(ctx, s.project.ID, feedback)
}

func (s *Service) Query(ctx context.Context, query string, limit int) (QueryResult, error) {
	query = strings.TrimSpace(query)
	if query == "" {
		return QueryResult{}, errors.New("query context: query is empty")
	}
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	contextGraph, err := s.contextGraph(ctx)
	if err != nil {
		return QueryResult{}, err
	}
	semantic, err := s.semanticMatches(ctx, query, contextGraph.nodes, limit)
	if err != nil {
		return QueryResult{}, err
	}
	ids := contextGraph.seeds(query)
	seeds := make([]graph.Node, 0, len(semantic))
	for _, match := range semantic {
		ids = append(ids, match.node.ID)
		seeds = append(seeds, match.node)
	}
	lexical, _ := contextprepare.BM25(prepareCandidates(contextGraph.nodes), query, nil)
	for _, candidate := range lexical {
		ids = append(ids, candidate.NodeID)
	}
	nodes, edges := contextGraph.neighborhood(ids, 2, limit*4)
	return QueryResult{Seeds: seeds, Nodes: nodes, Edges: edges, Paths: contextGraph.branches(query, limit)}, nil
}

func (s *Service) Graph(ctx context.Context, scope string, limit int) (GraphResult, error) {
	if limit <= 0 || limit > 200 {
		limit = 80
	}
	contextGraph, err := s.contextGraph(ctx)
	if err != nil {
		return GraphResult{}, err
	}
	seeds := contextGraph.seeds(scope)
	if strings.TrimSpace(scope) != "" && len(seeds) == 0 {
		return GraphResult{}, nil
	}
	nodes, edges := contextGraph.neighborhood(seeds, 2, limit)
	return GraphResult{Nodes: nodes, Edges: edges, TotalNodes: len(contextGraph.nodes), TotalEdges: len(contextGraph.edges), Truncated: len(contextGraph.nodes) > len(nodes)}, nil
}

func (s *Service) Explain(ctx context.Context, query string) (ExplainResult, error) {
	results, err := s.ExplainMany(ctx, []string{query})
	if err != nil {
		return ExplainResult{}, err
	}
	return results[0], nil
}

func (s *Service) ExplainMany(ctx context.Context, queries []string) ([]ExplainResult, error) {
	if len(queries) == 0 {
		return nil, errors.New("explain context: query is required")
	}
	contextGraph, err := s.contextGraph(ctx)
	if err != nil {
		return nil, err
	}
	nodes := make([]graph.Node, len(queries))
	for index, query := range queries {
		node, found := contextGraph.find(query)
		if !found {
			return nil, fmt.Errorf("explain context %q: %w", query, sql.ErrNoRows)
		}
		nodes[index] = node
	}
	results := make([]ExplainResult, 0, len(nodes))
	opened := make([]project.Delivery, 0, len(nodes))
	for _, node := range nodes {
		explanation := contextGraph.explanation(node)
		result := ExplainResult{Graph: &explanation}
		if entry, found := contextGraph.memoryByNode[node.ID]; found {
			result.Memory = &entry
		}
		opened = append(opened, project.Delivery{Key: node.ID, Kind: node.Kind, Label: node.Label, Source: node.Path, Preview: previewText(node.Content), Hash: contextprepare.Hash(node.ID + "\x00" + node.Content)})
		results = append(results, result)
	}
	sessionID := currentSessionID("")
	if err := s.SaveSessionAt(ctx, s.project.Root, sessionID, sessionAgent(sessionID), "active"); err != nil {
		return nil, err
	}
	if err := s.store.SaveDeliveries(ctx, s.project.ID, sessionID, opened); err != nil {
		return nil, err
	}
	return results, nil
}

func (s *Service) Path(ctx context.Context, source, target string) (graph.Path, error) {
	contextGraph, err := s.contextGraph(ctx)
	if err != nil {
		return graph.Path{}, err
	}
	return contextGraph.path(source, target)
}

func (s *Service) Update(ctx context.Context) (UpdateResult, error) {
	// ponytail: serialize whole-project snapshots; add parallel extraction only after profiling real projects.
	s.update.Lock()
	defer s.update.Unlock()
	materials, err := material.Discover(ctx, s.project.Root)
	if err != nil {
		return UpdateResult{}, err
	}
	for index := range materials {
		materials[index].Processor = extract.Processor(materials[index])
	}
	stored, err := s.store.Materials(ctx, s.project.ID)
	if err != nil {
		return UpdateResult{}, err
	}
	changes, changed := material.Diff(stored, materials)
	oldNodes, oldClaims, err := s.store.Knowledge(ctx, s.project.ID)
	if err != nil {
		return UpdateResult{}, err
	}
	currentIDs := make(map[string]bool, len(materials))
	changedIDs := make(map[string]bool, len(changed))
	for _, value := range materials {
		currentIDs[value.ID] = true
	}
	for _, value := range changed {
		changedIDs[value.ID] = true
	}
	var nodes []graph.Node
	for _, node := range oldNodes {
		if currentIDs[node.MaterialID] && !changedIDs[node.MaterialID] {
			nodes = append(nodes, node)
		}
	}
	var claims []graph.Claim
	for _, claim := range oldClaims {
		if currentIDs[claim.MaterialID] && !changedIDs[claim.MaterialID] {
			claims = append(claims, claim)
		}
	}
	result := UpdateResult{MaterialCount: len(materials), Processed: len(changed), Changes: changes}
	for _, value := range changed {
		facts, extractErr := extract.Material(ctx, s.project.Root, value)
		nodes = append(nodes, facts.Nodes...)
		claims = append(claims, facts.Claims...)
		if extractErr != nil {
			result.Warnings = append(result.Warnings, fmt.Sprintf("extract %s: %v", value.URI, extractErr))
		}
	}
	sort.Slice(nodes, func(i, j int) bool { return nodes[i].ID < nodes[j].ID })
	sort.Slice(claims, func(i, j int) bool {
		left, right := claims[i], claims[j]
		return left.MaterialID+left.SourceID+left.TargetID+left.TargetLabel+left.Relation < right.MaterialID+right.SourceID+right.TargetID+right.TargetLabel+right.Relation
	})
	edges := resolve.Claims(nodes, claims)
	if err := s.store.ReplaceKnowledge(ctx, s.project.ID, materials, nodes, claims, edges); err != nil {
		return UpdateResult{}, err
	}
	workspace, err := s.workspace.Observe(ctx, s.project.Root)
	if err != nil {
		return UpdateResult{}, err
	}
	if err := s.store.SaveWorkspace(ctx, s.project.ID, workspace.Resources); err != nil {
		return UpdateResult{}, err
	}
	result.EntityCount = len(nodes)
	result.RelationCount = len(edges)
	return result, nil
}

func (s *Service) Workspace(ctx context.Context) (project.Workspace, error) {
	return s.store.Workspace(ctx, s.project)
}

func (s *Service) SaveSession(ctx context.Context, sessionID, agent, status string) error {
	return s.SaveSessionAt(ctx, s.project.Root, sessionID, agent, status)
}

func (s *Service) SaveSessionAt(ctx context.Context, cwd, sessionID, agent, status string) error {
	workspace, err := s.workspace.Observe(ctx, cwd)
	if err != nil {
		return err
	}
	if workspace.Project.Root != s.project.Root && workspace.Project.ID != s.project.ID {
		return errors.New("save session: working directory belongs to another project")
	}
	if err := s.store.SaveWorkspace(ctx, s.project.ID, workspace.Resources); err != nil {
		return err
	}
	for _, resource := range workspace.Resources {
		for _, view := range resource.Views {
			if view.Root == workspace.Project.Root {
				return s.store.SaveSession(ctx, s.project.ID, view.ID, sessionID, agent, status)
			}
		}
	}
	return s.store.SaveSession(ctx, s.project.ID, "", sessionID, agent, status)
}

func (s *Service) ModelStatus(ctx context.Context) ollama.Status {
	return s.ollama.Status(ctx)
}

func (s *Service) Models(ctx context.Context) ([]ollama.Model, error) {
	return s.ollama.Models(ctx)
}

func (s *Service) RunModel(ctx context.Context, model, prompt string) (string, error) {
	return s.ollama.Chat(ctx, model, prompt)
}
