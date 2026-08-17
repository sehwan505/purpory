package app

import (
	"context"
	"fmt"
	"math"
	"strings"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
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

func (s *Service) EmbeddingStatus(ctx context.Context) (EmbeddingStatus, error) {
	selected, err := s.modelName(ctx, "embedding")
	if err != nil {
		return EmbeddingStatus{}, err
	}
	memories, err := s.store.Memories(ctx, s.project.ID, "")
	if err != nil {
		return EmbeddingStatus{}, err
	}
	existing, err := s.store.Embeddings(ctx, s.project.ID, selected.Model)
	if err != nil {
		return EmbeddingStatus{}, err
	}
	hashes := map[string]string{}
	for _, item := range existing {
		hashes[item.NodeID] = item.ContentHash
	}
	result := EmbeddingStatus{Model: selected.Model}
	for _, entry := range memories {
		if hashes[memoryNodeID(entry)] == entry.Hash {
			result.Current++
		} else {
			result.Pending++
		}
	}
	return result, nil
}

func (s *Service) SyncEmbeddings(ctx context.Context, limit int) (EmbeddingSyncResult, error) {
	selected, err := s.modelName(ctx, "embedding")
	if err != nil {
		return EmbeddingSyncResult{}, err
	}
	if selected.Model == "" {
		return EmbeddingSyncResult{}, fmt.Errorf("sync embeddings: no embedding model selected")
	}
	memories, err := s.store.Memories(ctx, s.project.ID, "")
	if err != nil {
		return EmbeddingSyncResult{}, err
	}
	existing, err := s.store.Embeddings(ctx, s.project.ID, selected.Model)
	if err != nil {
		return EmbeddingSyncResult{}, err
	}
	hashes := map[string]string{}
	for _, item := range existing {
		hashes[item.NodeID] = item.ContentHash
	}
	if limit <= 0 || limit > 1000 {
		limit = 100
	}
	result := EmbeddingSyncResult{Model: selected.Model}
	for start := 0; start < len(memories) && result.Embedded < limit; {
		batch := memories[start:min(start+32, len(memories))]
		start += len(batch)
		var pending []memory.Memory
		var texts []string
		for _, entry := range batch {
			nodeID := memoryNodeID(entry)
			if hashes[nodeID] == entry.Hash {
				result.Current++
				continue
			}
			if result.Embedded+len(pending) >= limit {
				break
			}
			pending = append(pending, entry)
			texts = append(texts, embeddingText(entry))
		}
		if len(pending) == 0 {
			continue
		}
		vectors, err := s.ollama.Embed(ctx, selected.Model, texts, embeddingDimensions)
		if err != nil {
			return EmbeddingSyncResult{}, err
		}
		for index, entry := range pending {
			if err := s.store.SaveEmbedding(ctx, s.project.ID, memoryNodeID(entry), entry.Hash, selected.Model, vectors[index]); err != nil {
				return EmbeddingSyncResult{}, err
			}
			result.Embedded++
		}
	}
	return result, nil
}

func (s *Service) enrichMemoryRanking(ctx context.Context, query string, candidates []contextprepare.Candidate, memories []memory.Memory) error {
	usage, err := s.store.MemoryUsage(ctx, s.project.ID)
	if err != nil {
		return err
	}
	hashes := map[string]string{}
	for _, entry := range memories {
		hashes[memoryNodeID(entry)] = entry.Hash
	}
	for index := range candidates {
		item := usage[candidates[index].Key]
		candidates[index].SelectedCount = item.SelectedCount
		candidates[index].ExpandedCount = item.ExpandedCount
	}
	selected, err := s.modelName(ctx, "embedding")
	if err != nil || selected.Model == "" {
		return err
	}
	embeddings, err := s.store.Embeddings(ctx, s.project.ID, selected.Model)
	if err != nil || len(embeddings) == 0 {
		return err
	}
	valid := map[string][]float64{}
	for _, item := range embeddings {
		if hashes[item.NodeID] == item.ContentHash {
			valid[item.NodeID] = item.Vector
		}
	}
	if len(valid) == 0 {
		return nil
	}
	queryText := query
	if strings.HasPrefix(selected.Model, "qwen3-embedding") {
		queryText = "Instruct: Retrieve relevant project memory\nQuery: " + query
	}
	vectors, err := s.ollama.Embed(ctx, selected.Model, []string{queryText}, embeddingDimensions)
	if err != nil {
		return nil // ponytail: dense retrieval is optional; deterministic ranking remains available.
	}
	for index := range candidates {
		similarity := cosine(vectors[0], valid[candidates[index].NodeID])
		if similarity >= 0.6 {
			candidates[index].Score += similarity * 50
			candidates[index].Signals = append(candidates[index].Signals, fmt.Sprintf("semantic:%.3f", similarity))
		}
	}
	return nil
}

func memoryNodeID(entry memory.Memory) string {
	return graph.ReferenceID(entry.Kind.NodeKind(), entry.Key)
}

func embeddingText(entry memory.Memory) string {
	content := ""
	if entry.Value != nil {
		content = *entry.Value
	} else if entry.Source != nil {
		content = *entry.Source
	}
	return strings.Join([]string{entry.Key, string(entry.Kind), content}, "\n")
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
