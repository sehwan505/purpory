package app

import (
	"context"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
	"github.com/sehwan505/purpory/internal/store"
)

const embeddingDimensions = 512

const semanticQueryTimeout = 2 * time.Second

type EmbeddingSyncResult struct {
	Provider string `json:"provider"`
	Model    string `json:"model"`
	Embedded int    `json:"embedded"`
	Current  int    `json:"current"`
}

type EmbeddingStatus struct {
	Provider string `json:"provider"`
	Model    string `json:"model"`
	Current  int    `json:"current"`
	Pending  int    `json:"pending"`
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
	existing, err := s.store.Embeddings(ctx, s.project.ID, embeddingIdentity(selected))
	if err != nil {
		return EmbeddingStatus{}, err
	}
	hashes := embeddingHashes(existing)
	result := EmbeddingStatus{Provider: selected.Provider, Model: selected.Model}
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
	selected, err := s.modelName(ctx, "embedding")
	if err != nil {
		return EmbeddingSyncResult{}, err
	}
	if selected.Model == "" {
		return EmbeddingSyncResult{}, fmt.Errorf("sync embeddings: no embedding model selected")
	}
	nodes, _, err := s.store.Graph(ctx, s.project.ID)
	if err != nil {
		return EmbeddingSyncResult{}, err
	}
	embedded, current, err := s.syncEmbeddingCandidates(ctx, selected, embeddingCandidates(nodes), limit)
	return EmbeddingSyncResult{Provider: selected.Provider, Model: selected.Model, Embedded: embedded, Current: current}, err
}

func (s *Service) syncNodeEmbeddings(ctx context.Context, nodeIDs []string) error {
	selected, err := s.modelName(ctx, "embedding")
	if err != nil || selected.Model == "" || selected.Source == "default" || len(nodeIDs) == 0 {
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
	_, _, err = s.syncEmbeddingCandidates(ctx, selected, candidates, 0)
	return err
}

func (s *Service) syncEmbeddingCandidates(ctx context.Context, selected ModelSelection, candidates []embeddingCandidate, limit int) (int, int, error) {
	modelID := embeddingIdentity(selected)
	existing, err := s.store.Embeddings(ctx, s.project.ID, modelID)
	if err != nil {
		return 0, 0, err
	}
	provider, err := s.embeddingProvider(ctx, selected.Provider)
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
		vectors, err := provider.Embed(ctx, selected.Model, texts, selected.Dimensions)
		if err != nil {
			return embedded, current, err
		}
		for index, candidate := range batch {
			if err := s.store.SaveEmbedding(ctx, s.project.ID, candidate.node.ID, candidate.hash, modelID, vectors[index]); err != nil {
				return embedded, current, err
			}
			embedded++
		}
	}
	return embedded, current, nil
}

func (s *Service) semanticMatches(ctx context.Context, query string, nodes []graph.Node, limit int) ([]semanticMatch, error) {
	selected, err := s.modelName(ctx, "embedding")
	if err != nil || selected.Model == "" || selected.Source == "default" {
		return nil, err
	}
	candidates := embeddingCandidates(nodes)
	existing, err := s.store.Embeddings(ctx, s.project.ID, embeddingIdentity(selected))
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
	queryContext, cancel := context.WithTimeout(ctx, semanticQueryTimeout)
	defer cancel()
	provider, err := s.embeddingProvider(ctx, selected.Provider)
	if err != nil {
		return nil, err
	}
	vectors, err := provider.Embed(queryContext, selected.Model, []string{query}, selected.Dimensions)
	if err != nil {
		return nil, nil // ponytail: dense retrieval is optional; exact and graph retrieval remain available.
	}
	var result []semanticMatch
	for id, vector := range valid {
		similarity := cosine(vectors[0], vector)
		result = append(result, semanticMatch{node: byID[id].node, score: similarity})
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

func embeddingIdentity(selected ModelSelection) string {
	if selected.Provider == providerOllama && selected.Dimensions == embeddingDimensions {
		return selected.Model // Preserve existing local embeddings and avoid a needless backfill.
	}
	return fmt.Sprintf("%s/%s#%d", selected.Provider, selected.Model, selected.Dimensions)
}

func pprSeeds(semantic []semanticMatch, exact, opened []string) map[string]float64 {
	result := map[string]float64{}
	if len(semantic) > 0 {
		maximum := semantic[0].score
		weights := make([]float64, len(semantic))
		var total float64
		for index, match := range semantic {
			weights[index] = math.Exp((match.score - maximum) * 8)
			total += weights[index]
		}
		for index, match := range semantic {
			result[match.node.ID] += weights[index] / total
		}
	}
	if len(exact) > 16 {
		exact = exact[:16]
	}
	for _, id := range exact {
		result[id] += 1 / float64(len(exact))
	}
	if len(opened) > 8 {
		opened = opened[:8]
	}
	var recentTotal float64
	for index := range opened {
		recentTotal += 1 / float64(index+1)
	}
	for index, id := range opened {
		result[id] += 0.5 / float64(index+1) / recentTotal
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
