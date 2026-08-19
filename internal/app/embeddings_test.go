package app

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
	"github.com/sehwan505/purpory/internal/reconcile"
	"github.com/sehwan505/purpory/internal/store"
)

func useEmbeddingServer(t *testing.T) {
	t.Helper()
	vector := make([]float64, embeddingDimensions)
	vector[0] = 1
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/embed" {
			http.NotFound(response, request)
			return
		}
		var body struct {
			Input []string `json:"input"`
		}
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
			http.Error(response, err.Error(), http.StatusBadRequest)
			return
		}
		vectors := make([][]float64, len(body.Input))
		for index := range vectors {
			vectors[index] = vector
		}
		_ = json.NewEncoder(response).Encode(map[string]any{"embeddings": vectors})
	}))
	t.Cleanup(server.Close)
	t.Setenv("PURPORY_OLLAMA_URL", server.URL)
}

func useSelectiveEmbeddingServer(t *testing.T) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		var body struct {
			Input []string `json:"input"`
		}
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
			http.Error(response, err.Error(), http.StatusBadRequest)
			return
		}
		vectors := make([][]float64, len(body.Input))
		for index, input := range body.Input {
			vectors[index] = make([]float64, embeddingDimensions)
			switch {
			case input == "fallback-marker":
				vectors[index][0] = 1
			case strings.Contains(input, "Dense-only"):
				vectors[index][0] = 0.5
				vectors[index][1] = 0.8660254037844386
			default:
				vectors[index][1] = 1
			}
		}
		_ = json.NewEncoder(response).Encode(map[string]any{"embeddings": vectors})
	}))
	t.Cleanup(server.Close)
	t.Setenv("PURPORY_OLLAMA_URL", server.URL)
}

func TestEmbeddingBackfillAndSemanticRanking(t *testing.T) {
	useEmbeddingServer(t)
	ctx := context.Background()
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	value := "Signed cookies hold authenticated browser sessions."
	if _, err := service.Remember(ctx, "decision.auth", memory.Decision, &value, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SelectModel(ctx, "embedding", "tiny-embed"); err != nil {
		t.Fatal(err)
	}
	if result, err := service.SyncEmbeddings(ctx, 0); err != nil || result.Embedded != 1 {
		t.Fatalf("embedding sync failed: %#v %v", result, err)
	}
	if status, err := service.EmbeddingStatus(ctx); err != nil || status.Current != 1 || status.Pending != 0 {
		t.Fatalf("embedding status failed: %#v %v", status, err)
	}
	query := "completely unrelated lexical text"
	found, err := service.Query(ctx, query, 10)
	if err != nil || len(found.Seeds) != 1 || found.Seeds[0].ID != "intent:decision.auth" {
		t.Fatalf("semantic graph seed missing: %#v %v", found, err)
	}
	service.gate = fixedGate{contextprepare.Proposal{Action: "search", Query: &query, ReasonCode: "PRIOR_DECISION_REFERENCED"}}
	prepared, err := service.PrepareContext(ctx, contextprepare.Request{Message: query, SessionID: "semantic", WorkingDirectory: root, TokenBudget: 512})
	if err != nil || prepared.Hints == nil || len(prepared.Hints.Nodes) != 1 || prepared.Hints.Nodes[0].Path != "decision.auth" {
		t.Fatalf("semantic memory was not retrieved: %#v %v", prepared, err)
	}
	if _, err := service.Explain(ctx, "decision.auth"); err != nil {
		t.Fatal(err)
	}
}

func TestPrepareFillsRemainingEmbeddingBudgetWithBM25(t *testing.T) {
	useSelectiveEmbeddingServer(t)
	ctx := context.Background()
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	for key, value := range map[string]string{
		"knowledge.dense":   "Dense-only project context.",
		"knowledge.lexical": "fallback-marker is the exact operational keyword.",
	} {
		value := value
		if _, err := service.Remember(ctx, key, memory.Note, &value, nil); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := service.SelectModel(ctx, "embedding", "qwen3-embedding:test"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SyncEmbeddings(ctx, 0); err != nil {
		t.Fatal(err)
	}
	query := "fallback-marker"
	service.gate = fixedGate{contextprepare.Proposal{Action: "search", Query: &query, ReasonCode: "PROJECT_CONTEXT_REQUIRED"}}
	result, err := service.PrepareContext(ctx, contextprepare.Request{Message: query, SessionID: "hybrid", WorkingDirectory: root, TokenBudget: 512})
	if err != nil || result.Hints == nil || len(result.Hints.Nodes) != 2 || result.Hints.Nodes[0].ID != "knowledge:knowledge.dense" || result.Hints.Nodes[0].Match != "semantic" || result.Hints.Nodes[1].ID != "knowledge:knowledge.lexical" || result.Hints.Nodes[1].Match != "bm25" {
		t.Fatalf("embedding-first BM25 fill failed: %#v %v", result, err)
	}
}

func TestReconcileEmbedsIntentAndKnowledgeWithLockedProjectModel(t *testing.T) {
	useEmbeddingServer(t)
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	selected, err := service.SelectModel(ctx, "embedding", "tiny-embed")
	if err != nil || selected.Source != "project" {
		t.Fatalf("project model was not selected: %#v %v", selected, err)
	}
	if _, err := service.SelectModel(ctx, "embedding", "tiny-embed"); err != nil {
		t.Fatalf("selecting the locked model should be idempotent: %v", err)
	}
	if _, err := service.SelectModel(ctx, "embedding", "other-embed"); err == nil || !strings.Contains(err.Error(), "locked") {
		t.Fatalf("embedding model changed after project lock: %v", err)
	}

	candidates := []reconcile.Candidate{
		{Key: "intent.database", Kind: memory.Decision, Value: "Use SQLite.", EvidenceIDs: []string{"U000001"}},
		{Key: "knowledge.index", Kind: memory.Note, Value: "The index is rebuilt incrementally.", EvidenceIDs: []string{"U000002"}},
	}
	if err := service.applyCandidates(ctx, "reconcile:test", candidates); err != nil {
		t.Fatalf("reconciliation failed: %v", err)
	}
	embeddings, err := service.store.Embeddings(ctx, "demo", "tiny-embed")
	if err != nil || len(embeddings) != 2 {
		t.Fatalf("reconciled embeddings missing: %#v %v", embeddings, err)
	}
	ids := map[string]bool{}
	for _, item := range embeddings {
		ids[item.NodeID] = true
	}
	if !ids["intent:intent.database"] || !ids["knowledge:knowledge.index"] {
		t.Fatalf("intent and knowledge were not both embedded: %#v", ids)
	}
}

func TestSemanticMapProgressesAcrossSessionCalls(t *testing.T) {
	useEmbeddingServer(t)
	ctx := context.Background()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "guide.md"), []byte("# Deployment\nShip signed artifacts from tags.\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service := openTestService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	if _, err := service.Update(ctx); err != nil {
		t.Fatal(err)
	}
	for index := 0; index < 10; index++ {
		value := "Semantic project fact"
		key := "knowledge.topic-" + string(rune('a'+index))
		if _, err := service.Remember(ctx, key, memory.Note, &value, nil); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := service.SelectModel(ctx, "embedding", "tiny-embed"); err != nil {
		t.Fatal(err)
	}
	before, err := service.EmbeddingStatus(ctx)
	if err != nil || before.Pending < 11 {
		t.Fatalf("graph embedding candidates missing: %#v %v", before, err)
	}
	synced, err := service.SyncEmbeddings(ctx, 0)
	if err != nil || synced.Embedded != before.Pending {
		t.Fatalf("full graph backfill failed: %#v %v", synced, err)
	}

	query := "lexically unrelated request"
	found, err := service.Query(ctx, query, 100)
	observedSeed := false
	for _, seed := range found.Seeds {
		observedSeed = observedSeed || seed.Kind == graph.KindKnowledge && seed.Owner == graph.OwnerObserved
	}
	if err != nil || !observedSeed {
		t.Fatalf("observed knowledge was not a semantic map seed: %#v %v", found.Seeds, err)
	}

}

func TestHintExplorationUsesPathsAndSkipsOnlyOpenedNodes(t *testing.T) {
	useEmbeddingServer(t)
	t.Setenv("PURPORY_SESSION", "codex:explore")
	ctx := context.Background()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "guide.md"), []byte("# Rules\nDestroy the opposing nexus.\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service := openTestService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	if _, err := service.Update(ctx); err != nil {
		t.Fatal(err)
	}
	value := "롤의 승리 조건과 기본 플레이 규칙"
	entry, err := memory.New("demo", "game.lol.play-rule", memory.Decision, &value, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.store.ReconcileMemories(ctx, "codex:explore", []store.MemoryProposal{{
		Memory: entry, EvidenceIDs: []string{"U000001"}, Links: []graph.Link{{
			SourceKind: graph.KindIntent, SourceRef: entry.Key, Relation: graph.RelationRealizedBy,
			TargetKind: graph.KindMaterial, TargetRef: "file:guide.md",
		}},
	}}); err != nil {
		t.Fatal(err)
	}
	for key, value := range map[string]string{
		"game.lol.items":    "Items available in League of Legends",
		"product.discovery": "Explore a different product direction",
	} {
		value := value
		if _, err := service.Remember(ctx, key, memory.Note, &value, nil); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := service.SelectModel(ctx, "embedding", "tiny-embed"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SyncEmbeddings(ctx, 0); err != nil {
		t.Fatal(err)
	}
	query := "롤 플레이 규칙"
	service.gate = fixedGate{contextprepare.Proposal{Action: "search", Query: &query, ReasonCode: "PROJECT_CONTEXT_REQUIRED"}}
	request := contextprepare.Request{Message: query, SessionID: "codex:explore", WorkingDirectory: root, TokenBudget: 512}
	first, err := service.PrepareContext(ctx, request)
	if err != nil || first.Hints == nil || len(first.Hints.Nodes) == 0 || len(first.Hints.Nodes) > 3 {
		t.Fatalf("first hint map failed: %#v %v", first, err)
	}
	rendered := contextprepare.RenderHintMap(first.Hints)
	t.Logf("first hook hint map:\n%s", rendered)
	if !strings.Contains(rendered, "game.") || strings.Contains(rendered, "승리 조건") {
		t.Fatalf("hint map was not a content-free semantic signpost: %q", rendered)
	}
	opened := first.Hints.Nodes[0]
	if _, err := service.Explain(ctx, opened.Path); err != nil {
		t.Fatal(err)
	}
	second, err := service.PrepareContext(ctx, request)
	if err != nil || second.Hints == nil {
		t.Fatalf("second hint map failed: %#v %v", second, err)
	}
	for _, hint := range second.Hints.Nodes {
		if hint.ID == opened.ID {
			t.Fatalf("opened path was suggested again: %#v", second.Hints)
		}
	}
	branch, err := service.Query(ctx, "game.lol", 10)
	if err != nil || len(branch.Paths) != 2 {
		t.Fatalf("topic branch was not browsable: %#v %v", branch.Paths, err)
	}
	path, err := service.Path(ctx, "game.lol.play-rule", "game.lol.items")
	if err != nil || len(path.TopicPaths) == 0 {
		t.Fatalf("topic leaves were not connectable: %#v %v", path, err)
	}
	crossEdge, err := service.Path(ctx, "game.lol.play-rule", "file:guide.md")
	if err != nil || len(crossEdge.Edges) != 1 || crossEdge.Edges[0].Relation != graph.RelationRealizedBy {
		t.Fatalf("physical cross-edge was not traversable: %#v %v", crossEdge, err)
	}
	t.Logf("topic path: %#v; physical edge: %#v", path.TopicPaths, crossEdge.Edges)
	english, err := service.Query(ctx, "product", 10)
	if err != nil || len(english.Paths) != 1 || english.Paths[0] != "product.discovery" {
		t.Fatalf("English branch exploration failed: %#v %v", english.Paths, err)
	}
}
