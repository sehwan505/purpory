package app

import (
	"context"
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/store"
)

type ExplorationStatus struct {
	SessionID      string `json:"sessionId"`
	Enabled        bool   `json:"enabled"`
	PendingChanges int    `json:"pendingChanges"`
}

func (s *Service) SetExplorationMode(ctx context.Context, explicitSessionID string, enabled bool) (ExplorationStatus, error) {
	sessionID, err := s.explorationSession(ctx, explicitSessionID, false)
	if err != nil {
		return ExplorationStatus{}, err
	}
	if err := s.store.SetExploration(ctx, s.project.ID, sessionID, enabled); err != nil {
		return ExplorationStatus{}, err
	}
	return s.Exploration(ctx, sessionID)
}

func (s *Service) Exploration(ctx context.Context, explicitSessionID string) (ExplorationStatus, error) {
	sessionID := currentSessionID(explicitSessionID)
	if sessionID == "anon" {
		return ExplorationStatus{}, errors.New("exploration mode requires an agent session")
	}
	enabled, err := s.store.ExplorationEnabled(ctx, s.project.ID, sessionID)
	if err != nil {
		return ExplorationStatus{}, err
	}
	count, err := s.store.AgentChangeCount(ctx, s.project.ID)
	if err != nil {
		return ExplorationStatus{}, err
	}
	return ExplorationStatus{SessionID: sessionID, Enabled: enabled, PendingChanges: count}, nil
}

func (s *Service) SetAgentKnowledge(ctx context.Context, explicitSessionID, key, value, reason string) (store.AgentChange, error) {
	sessionID, err := s.explorationSession(ctx, explicitSessionID, true)
	if err != nil {
		return store.AgentChange{}, err
	}
	value, reason = strings.TrimSpace(value), strings.TrimSpace(reason)
	if value == "" || len(value) > 1_048_576 || utf8.RuneCountInString(reason) > 4_096 {
		return store.AgentChange{}, errors.New("set agent knowledge: value is required, value must be at most 1 MiB, and reason must be at most 4096 characters")
	}
	entry, err := memory.New(s.project.ID, key, memory.Note, &value, nil)
	if err != nil {
		return store.AgentChange{}, err
	}
	change, err := s.store.SetAgentKnowledge(ctx, s.project.ID, sessionID, agentProvenance(sessionID, reason), entry)
	if err != nil {
		return store.AgentChange{}, err
	}
	if err := s.syncNodeEmbeddings(ctx, []string{memoryNodeID(entry)}); err != nil {
		return change, err
	}
	return change, nil
}

func (s *Service) DeleteAgentKnowledge(ctx context.Context, explicitSessionID, key string) (store.AgentChange, error) {
	sessionID, err := s.explorationSession(ctx, explicitSessionID, true)
	if err != nil {
		return store.AgentChange{}, err
	}
	key, err = memory.ValidateKey(key)
	if err != nil {
		return store.AgentChange{}, err
	}
	return s.store.DeleteAgentKnowledge(ctx, s.project.ID, sessionID, key)
}

func (s *Service) SetAgentLink(ctx context.Context, explicitSessionID, source, relation, target, reason string) (store.AgentChange, error) {
	sessionID, err := s.explorationSession(ctx, explicitSessionID, true)
	if err != nil {
		return store.AgentChange{}, err
	}
	reason = strings.TrimSpace(reason)
	if utf8.RuneCountInString(reason) > 4_096 {
		return store.AgentChange{}, errors.New("agent link: reason must be at most 4096 characters")
	}
	edge, err := s.resolveAgentLink(ctx, source, relation, target)
	if err != nil {
		return store.AgentChange{}, err
	}
	edge.Provenance = agentProvenance(sessionID, reason)
	return s.store.SetAgentEdge(ctx, s.project.ID, sessionID, edge)
}

func (s *Service) DeleteAgentLink(ctx context.Context, explicitSessionID, source, relation, target string) (store.AgentChange, error) {
	sessionID, err := s.explorationSession(ctx, explicitSessionID, true)
	if err != nil {
		return store.AgentChange{}, err
	}
	edge, err := s.resolveAgentLink(ctx, source, relation, target)
	if err != nil {
		return store.AgentChange{}, err
	}
	return s.store.DeleteAgentEdge(ctx, s.project.ID, sessionID, edge)
}

func (s *Service) AgentChanges(ctx context.Context, limit int) ([]store.AgentChange, error) {
	return s.store.AgentChanges(ctx, s.project.ID, limit)
}

func (s *Service) RollbackAgentChanges(ctx context.Context, explicitSessionID string) (int, error) {
	if _, err := s.explorationSession(ctx, explicitSessionID, true); err != nil {
		return 0, err
	}
	count, err := s.store.RollbackAgentChanges(ctx, s.project.ID)
	if err != nil {
		return 0, err
	}
	nodes, _, err := s.store.Graph(ctx, s.project.ID)
	if err != nil {
		return count, err
	}
	ids := make([]string, 0, len(nodes))
	for _, node := range nodes {
		ids = append(ids, node.ID)
	}
	return count, s.syncNodeEmbeddings(ctx, ids)
}

func (s *Service) CheckpointAgentChanges(ctx context.Context, explicitSessionID string) (int, error) {
	if _, err := s.explorationSession(ctx, explicitSessionID, true); err != nil {
		return 0, err
	}
	return s.store.CheckpointAgentChanges(ctx, s.project.ID)
}

func (s *Service) explorationSession(ctx context.Context, explicitSessionID string, requireEnabled bool) (string, error) {
	sessionID := currentSessionID(explicitSessionID)
	if sessionID == "anon" {
		return "", errors.New("exploration mode requires an agent session")
	}
	if err := s.SaveSessionAt(ctx, s.currentRoot(ctx), sessionID, sessionAgent(sessionID), "active"); err != nil {
		return "", err
	}
	if requireEnabled {
		enabled, err := s.store.ExplorationEnabled(ctx, s.project.ID, sessionID)
		if err != nil {
			return "", err
		}
		if !enabled {
			return "", errors.New("exploration mode is off; run `purpory explore on`")
		}
	}
	return sessionID, nil
}

func (s *Service) resolveAgentLink(ctx context.Context, source, relation, target string) (graph.Edge, error) {
	relation = strings.TrimSpace(relation)
	if !validRelationName(relation) {
		return graph.Edge{}, errors.New("agent link: relation must be a lowercase snake_case identifier")
	}
	current, err := s.contextGraph(ctx)
	if err != nil {
		return graph.Edge{}, err
	}
	sourceNode, sourceFound := current.find(source)
	targetNode, targetFound := current.find(target)
	if !sourceFound || !targetFound {
		return graph.Edge{}, errors.New("agent link: source and target must resolve to graph nodes")
	}
	if sourceNode.ID == targetNode.ID {
		return graph.Edge{}, errors.New("agent link: self links are not supported")
	}
	return graph.Edge{SourceID: sourceNode.ID, TargetID: targetNode.ID, Relation: relation}, nil
}

func validRelationName(value string) bool {
	if value == "" || len(value) > 64 || value[0] < 'a' || value[0] > 'z' {
		return false
	}
	for _, character := range value[1:] {
		if character != '_' && (character < 'a' || character > 'z') && (character < '0' || character > '9') {
			return false
		}
	}
	return true
}

func agentProvenance(sessionID, reason string) string {
	if reason == "" {
		return "agent:" + sessionID
	}
	return "agent:" + sessionID + ": " + reason
}
