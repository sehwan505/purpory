package app

import (
	"context"
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
	"github.com/sehwan505/purpory/internal/store"
)

const embeddingDimensions = 512

type EmbeddingSyncResult struct {
	Model    string `json:"model"`
	Embedded int    `json:"embedded"`
	Current  int    `json:"current"`
}

type EmbeddingStatus struct {
	Model   string `json:"model"`
	Current int    `json:"current"`
	Pending int    `json:"pending"`
}

type embeddingCandidate struct {
	node graph.Node
	text string
	hash string
}

type semanticMatch struct {
	node  graph.Node
	score float64
}

func (s *Service) EmbeddingStatus(ctx context.Context) (EmbeddingStatus, error) {
	selected, err := s.modelName(ctx, "embedding")
	if err != nil {
		return EmbeddingStatus{}, err
	}
	nodes, _, err := s.store.Graph(ctx, s.project.ID)
	if err != nil {
		return EmbeddingStatus{}, err
	}
	candidates := embeddingCandidates(nodes)
	existing, err := s.store.Embeddings(ctx, s.project.ID, selected.Model)
	if err != nil {
		return EmbeddingStatus{}, err
	}
	hashes := embeddingHashes(existing)
	result := EmbeddingStatus{Model: selected.Model}
	for _, candidate := range candidates {
		if hashes[candidate.node.ID] == candidate.hash {
			result.Current++
		} else {
			result.Pending++
		}
	}
	return result, nil
}

// SyncEmbeddings fills every missing or stale intent/knowledge embedding.
// A positive limit bounds one invocation; zero processes the whole project.
func (s *Service) SyncEmbeddings(ctx context.Context, limit int) (EmbeddingSyncResult, error) {
	selected, err := s.lockEmbeddingModel(ctx)
	if err != nil {
		return EmbeddingSyncResult{}, err
	}
	nodes, _, err := s.store.Graph(ctx, s.project.ID)
	if err != nil {
		return EmbeddingSyncResult{}, err
	}
	embedded, current, err := s.syncEmbeddingCandidates(ctx, selected.Model, embeddingCandidates(nodes), limit)
	return EmbeddingSyncResult{Model: selected.Model, Embedded: embedded, Current: current}, err
}

func (s *Service) lockEmbeddingModel(ctx context.Context) (ModelSelection, error) {
	selected, err := s.modelName(ctx, "embedding")
	if err != nil {
		return ModelSelection{}, err
	}
	if selected.Model == "" {
		return ModelSelection{}, fmt.Errorf("sync embeddings: no embedding model selected")
	}
	if selected.Source != "project" {
		if err := s.store.SetProjectEmbeddingModel(ctx, s.project.ID, selected.Model); err != nil {
			return ModelSelection{}, err
		}
		selected.Source = "project"
	}
	return selected, nil
}

func (s *Service) syncNodeEmbeddings(ctx context.Context, nodeIDs []string) error {
	model, configured, err := s.store.ProjectEmbeddingModel(ctx, s.project.ID)
	if err != nil || !configured || len(nodeIDs) == 0 {
		return err
	}
	wanted := map[string]bool{}
	for _, id := range nodeIDs {
		wanted[id] = true
	}
	nodes, _, err := s.store.Graph(ctx, s.project.ID)
	if err != nil {
		return err
	}
	var candidates []embeddingCandidate
	for _, candidate := range embeddingCandidates(nodes) {
		if wanted[candidate.node.ID] {
			candidates = append(candidates, candidate)
		}
	}
	_, _, err = s.syncEmbeddingCandidates(ctx, model, candidates, 0)
	return err
}

func (s *Service) syncEmbeddingCandidates(ctx context.Context, model string, candidates []embeddingCandidate, limit int) (int, int, error) {
	existing, err := s.store.Embeddings(ctx, s.project.ID, model)
	if err != nil {
		return 0, 0, err
	}
	hashes := embeddingHashes(existing)
	current := 0
	pending := make([]embeddingCandidate, 0, len(candidates))
	for _, candidate := range candidates {
		if hashes[candidate.node.ID] == candidate.hash {
			current++
		} else {
			pending = append(pending, candidate)
		}
	}
	if limit > 0 && len(pending) > limit {
		pending = pending[:limit]
	}
	embedded := 0
	for start := 0; start < len(pending); start += 32 {
		batch := pending[start:min(start+32, len(pending))]
		texts := make([]string, len(batch))
		for index, candidate := range batch {
			texts[index] = candidate.text
		}
		vectors, err := s.ollama.Embed(ctx, model, texts, embeddingDimensions)
		if err != nil {
			return embedded, current, err
		}
		for index, candidate := range batch {
			if err := s.store.SaveEmbedding(ctx, s.project.ID, candidate.node.ID, candidate.hash, model, vectors[index]); err != nil {
				return embedded, current, err
			}
			embedded++
		}
	}
	return embedded, current, nil
}

func (s *Service) semanticMatches(ctx context.Context, query string, nodes []graph.Node, limit int) ([]semanticMatch, error) {
	model, configured, err := s.store.ProjectEmbeddingModel(ctx, s.project.ID)
	if err != nil || !configured {
		return nil, err
	}
	candidates := embeddingCandidates(nodes)
	existing, err := s.store.Embeddings(ctx, s.project.ID, model)
	if err != nil {
		return nil, err
	}
	byID := map[string]embeddingCandidate{}
	for _, candidate := range candidates {
		byID[candidate.node.ID] = candidate
	}
	valid := map[string][]float64{}
	for _, item := range existing {
		if candidate, found := byID[item.NodeID]; found && candidate.hash == item.ContentHash {
			valid[item.NodeID] = item.Vector
		}
	}
	if len(valid) == 0 {
		return nil, nil
	}
	queryText := query
	if strings.HasPrefix(model, "qwen3-embedding") {
		queryText = "Instruct: Retrieve relevant project context\nQuery: " + query
	}
	vectors, err := s.ollama.Embed(ctx, model, []string{queryText}, embeddingDimensions)
	if err != nil {
		return nil, nil // ponytail: dense retrieval is optional; lexical and graph retrieval remain available.
	}
	var result []semanticMatch
	for id, vector := range valid {
		similarity := cosine(vectors[0], vector)
		if similarity >= 0.6 {
			result = append(result, semanticMatch{node: byID[id].node, score: similarity})
		}
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].score != result[j].score {
			return result[i].score > result[j].score
		}
		return result[i].node.ID < result[j].node.ID
	})
	if limit > 0 && len(result) > limit {
		result = result[:limit]
	}
	return result, nil
}

func semanticCandidates(all []contextprepare.Candidate, matches []semanticMatch) []contextprepare.Candidate {
	byID := map[string]contextprepare.Candidate{}
	for _, candidate := range all {
		byID[candidate.NodeID] = candidate
	}
	result := make([]contextprepare.Candidate, 0, len(matches))
	for _, match := range matches {
		candidate, available := byID[match.node.ID]
		if !available || !deliverableCandidate(candidate) {
			continue
		}
		candidate.Score = math.Round(match.score*1_000_000) / 1_000_000
		candidate.Signals = []string{fmt.Sprintf("semantic:%.3f", match.score)}
		result = append(result, candidate)
	}
	return result
}

func embeddingCandidates(nodes []graph.Node) []embeddingCandidate {
	result := make([]embeddingCandidate, 0, len(nodes))
	for _, node := range nodes {
		if node.State != graph.StateActive || (node.Kind != graph.KindIntent && node.Kind != graph.KindKnowledge) {
			continue
		}
		text := strings.Join([]string{node.Label, node.Kind, node.Subkind, node.MaterialURI, node.Locator, node.Content}, "\n")
		result = append(result, embeddingCandidate{node: node, text: text, hash: contextprepare.Hash(text)})
	}
	return result
}

func embeddingHashes(items []store.Embedding) map[string]string {
	result := make(map[string]string, len(items))
	for _, item := range items {
		result[item.NodeID] = item.ContentHash
	}
	return result
}

func memoryNodeID(entry memory.Memory) string {
	return graph.ReferenceID(entry.Kind.NodeKind(), entry.Key)
}

func cosine(left, right []float64) float64 {
	if len(left) == 0 || len(left) != len(right) {
		return 0
	}
	var dot, leftNorm, rightNorm float64
	for index := range left {
		dot += left[index] * right[index]
		leftNorm += left[index] * left[index]
		rightNorm += right[index] * right[index]
	}
	if leftNorm == 0 || rightNorm == 0 {
		return 0
	}
	return dot / math.Sqrt(leftNorm*rightNorm)
}
