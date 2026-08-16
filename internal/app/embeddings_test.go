package app

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
)

func TestEmbeddingAndUsageRanking(t *testing.T) {
	vector := make([]float64, embeddingDimensions)
	vector[0] = 1
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/embed" {
			http.NotFound(response, request)
			return
		}
		_ = json.NewEncoder(response).Encode(map[string]any{"embeddings": [][]float64{vector}})
	}))
	defer server.Close()
	t.Setenv("PURPORY_OLLAMA_URL", server.URL)

	ctx := context.Background()
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	if _, err := service.SelectModel(ctx, "embedding", "tiny-embed"); err != nil {
		t.Fatal(err)
	}
	value := "Signed cookies hold authenticated browser sessions."
	if _, err := service.Remember(ctx, "decision.auth", memory.Decision, &value, nil); err != nil {
		t.Fatal(err)
	}
	if result, err := service.SyncEmbeddings(ctx, 10); err != nil || result.Embedded != 1 {
		t.Fatalf("embedding sync failed: %#v %v", result, err)
	}
	if status, err := service.EmbeddingStatus(ctx); err != nil || status.Current != 1 || status.Pending != 0 {
		t.Fatalf("embedding status failed: %#v %v", status, err)
	}
	query := "completely unrelated lexical text"
	service.gate = fixedGate{contextprepare.Proposal{Action: "search", Query: &query, Scopes: []string{"human"}, ReasonCode: "PRIOR_DECISION_REFERENCED"}}
	prepared, err := service.PrepareContext(ctx, contextprepare.Request{Message: query, SessionID: "semantic", WorkingDirectory: root, TokenBudget: 512})
	if err != nil || len(prepared.Deliveries) != 1 || prepared.Deliveries[0].Key != "decision.auth" {
		t.Fatalf("semantic memory was not retrieved: %#v %v", prepared, err)
	}
	if _, err := service.Explain(ctx, "decision.auth"); err != nil {
		t.Fatal(err)
	}
	usage, err := service.store.MemoryUsage(ctx, "demo")
	if err != nil || usage["decision.auth"].SelectedCount != 1 || usage["decision.auth"].ExpandedCount != 1 {
		t.Fatalf("usage was not recorded: %#v %v", usage, err)
	}
}
