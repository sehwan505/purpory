package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
)

func openCLIService(t *testing.T, root, database, id string) *product.Service {
	t.Helper()
	if _, err := product.RegisterProject(context.Background(), root, database, id, ""); err != nil {
		t.Fatal(err)
	}
	service, err := product.Open(context.Background(), root, database, id)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = service.Close() })
	return service
}

func TestUpdateJSON(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "brief.txt"), []byte("A project for everyone."), 0o600); err != nil {
		t.Fatal(err)
	}
	service := openCLIService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	var output bytes.Buffer
	if err := runCLI(context.Background(), service, []string{"update", "--json"}, bytes.NewReader(nil), &output); err != nil {
		t.Fatal(err)
	}
	var result product.UpdateResult
	if err := json.Unmarshal(output.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.MaterialCount != 1 || result.Processed != 1 || result.EntityCount != 1 {
		t.Fatalf("unexpected result: %#v", result)
	}
	found, err := service.Query(context.Background(), "project for everyone", 10)
	if err != nil || len(found.Nodes) != 1 || found.Nodes[0].Content != "" {
		t.Fatalf("query did not return a content-free reference: %#v, %v", found, err)
	}
	explained, err := service.Explain(context.Background(), found.Nodes[0].ID)
	if err != nil || explained.Graph == nil || explained.Graph.Node.Content != "A project for everyone." {
		t.Fatalf("selected evidence missing: %#v, %v", explained, err)
	}
}

func TestEmbedCLIBackfillsAllMissingNodes(t *testing.T) {
	vector := make([]float64, 512)
	vector[0] = 1
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
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
	defer server.Close()
	t.Setenv("PURPORY_OLLAMA_URL", server.URL)

	ctx := context.Background()
	service := openCLIService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	for key, kind := range map[string]memory.Kind{"intent.one": memory.Decision, "knowledge.two": memory.Note} {
		value := "project context"
		if _, err := service.Remember(ctx, key, kind, &value, nil); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := service.SelectModel(ctx, "embedding", "tiny-embed"); err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	if err := runCLI(ctx, service, []string{"embed"}, bytes.NewReader(nil), &output); err != nil {
		t.Fatal(err)
	}
	var result product.EmbeddingSyncResult
	if err := json.Unmarshal(output.Bytes(), &result); err != nil || result.Embedded != 2 {
		t.Fatalf("embed CLI did not backfill all nodes: %#v %v", result, err)
	}
}

func TestModelSelectCLIAcceptsProvider(t *testing.T) {
	service := openCLIService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	var output bytes.Buffer
	if err := runCLI(context.Background(), service, []string{"model", "select", "embedding", "openai", "text-embedding-3-small"}, bytes.NewReader(nil), &output); err != nil {
		t.Fatal(err)
	}
	var selected product.ModelSelection
	if err := json.Unmarshal(output.Bytes(), &selected); err != nil || selected.Provider != "openai" || selected.Model != "text-embedding-3-small" || selected.Dimensions != 512 {
		t.Fatalf("provider selection = %#v, %v", selected, err)
	}
}

func TestPrepareCLIOptions(t *testing.T) {
	root := t.TempDir()
	service := openCLIService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	value := "Use SQLite for local context."
	if _, err := service.Remember(context.Background(), "decision.database", memory.Decision, &value, nil); err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	arguments := []string{"prepare", "--session", "cli", "--budget", "512", "--path", "internal/store", "--json", "--no-retain-input", "decision.database"}
	if err := runCLI(context.Background(), service, arguments, bytes.NewReader(nil), &output); err != nil {
		t.Fatal(err)
	}
	var result product.PrepareResult
	if err := json.Unmarshal(output.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Action != "retrieve" || result.Hints == nil || len(result.Hints.Nodes) == 0 || result.DecisionID == 0 {
		t.Fatalf("unexpected prepare result: %#v", result)
	}
}

func TestExplainCLIAcceptsMultipleNodes(t *testing.T) {
	ctx := context.Background()
	service := openCLIService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	for key, kind := range map[string]memory.Kind{"intent.one": memory.Decision, "knowledge.two": memory.Note} {
		value := key + " content"
		if _, err := service.Remember(ctx, key, kind, &value, nil); err != nil {
			t.Fatal(err)
		}
	}
	var output bytes.Buffer
	if err := runCLI(ctx, service, []string{"explain", "--json", "intent.one", "knowledge.two"}, bytes.NewReader(nil), &output); err != nil {
		t.Fatal(err)
	}
	var results []product.ExplainResult
	if err := json.Unmarshal(output.Bytes(), &results); err != nil {
		t.Fatal(err)
	}
	if len(results) != 2 || results[0].Memory == nil || results[0].Memory.Key != "intent.one" || results[1].Memory == nil || results[1].Memory.Key != "knowledge.two" {
		t.Fatalf("unexpected explanations: %#v", results)
	}
}

func TestExplorationCLIIsBoundedAndProgressive(t *testing.T) {
	ctx := context.Background()
	service := openCLIService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	secret := strings.Repeat("selected evidence ", 2_000)
	other := "neighbor content must stay unloaded"
	for key, value := range map[string]string{"knowledge.selected": secret, "knowledge.other": other} {
		value := value
		if _, err := service.Remember(ctx, key, memory.Note, &value, nil); err != nil {
			t.Fatal(err)
		}
	}

	var output bytes.Buffer
	if err := runCLI(ctx, service, []string{"query", "knowledge"}, bytes.NewReader(nil), &output); err != nil {
		t.Fatal(err)
	}
	if output.Len() > queryCharacterBudget+1 || strings.Contains(output.String(), secret) || strings.Contains(output.String(), other) || !strings.Contains(output.String(), "CONTENT NOT LOADED") {
		t.Fatalf("query leaked or exceeded its budget: %d bytes\n%s", output.Len(), output.String())
	}
	compactBytes := output.Len()
	output.Reset()
	if err := runCLI(ctx, service, []string{"query", "--json", "knowledge"}, bytes.NewReader(nil), &output); err != nil {
		t.Fatal(err)
	}
	if output.Len() <= compactBytes || strings.Contains(output.String(), secret) || strings.Contains(output.String(), other) {
		t.Fatalf("machine-readable query leaked content or was unexpectedly small: compact=%d json=%d", compactBytes, output.Len())
	}
	t.Logf("query delivery: %d bytes (~%d tokens), navigation JSON: %d bytes (~%d tokens)", compactBytes, (compactBytes+3)/4, output.Len(), (output.Len()+3)/4)

	output.Reset()
	if err := runCLI(ctx, service, []string{"explain", "knowledge.selected"}, bytes.NewReader(nil), &output); err != nil {
		t.Fatal(err)
	}
	if len([]rune(output.String())) > evidenceCharBudget+1 || !strings.Contains(output.String(), "selected evidence") || !strings.Contains(output.String(), "[truncated;") {
		t.Fatalf("explain did not return bounded selected evidence: %d runes", len([]rune(output.String())))
	}
}

func TestProgressiveRenderersDoNotLoadConnectedContent(t *testing.T) {
	selected := graph.Node{ID: "knowledge:selected", Path: "knowledge.selected", Label: "Selected", Kind: graph.KindKnowledge, Owner: graph.OwnerDurable, State: graph.StateActive, Content: "selected evidence"}
	connected := graph.Node{ID: "knowledge:connected", Path: "knowledge.connected", Label: "Connected", Kind: graph.KindKnowledge, Owner: graph.OwnerDurable, State: graph.StateActive, Content: "connected content must stay unloaded"}
	edge := graph.Edge{SourceID: selected.ID, TargetID: connected.ID, Relation: "related_to"}
	explanation := renderExplanations([]product.ExplainResult{{Graph: &graph.Explanation{Node: selected, Connections: []graph.Connection{{Direction: "out", Relation: edge.Relation, Node: connected}}}}})
	path := renderPath(graph.Path{Nodes: []graph.Node{selected, connected}, Edges: []graph.Edge{edge}})
	if !strings.Contains(explanation, selected.Content) || strings.Contains(explanation, connected.Content) || strings.Contains(path, selected.Content) || strings.Contains(path, connected.Content) {
		t.Fatalf("progressive renderers loaded unopened content:\n%s\n%s", explanation, path)
	}
}

func TestMemoryLifecycleCLI(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	service := openCLIService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	value := "first"
	if _, err := service.Remember(ctx, "knowledge.demo", memory.Note, &value, nil); err != nil {
		t.Fatal(err)
	}
	values, err := service.Memories(ctx, "knowledge.demo")
	if err != nil {
		t.Fatal(err)
	}
	next := "second"
	changes := []memory.BatchChange{{Key: "knowledge.demo", Kind: memory.Note, Value: &next, ExpectedHash: &values[0].Hash, ExpectedHashSet: true}}
	encoded, _ := json.Marshal(changes)
	var output bytes.Buffer
	if err := runCLI(ctx, service, []string{"remember", "--batch", "-", "--apply", "--session", "cli"}, bytes.NewReader(encoded), &output); err != nil {
		t.Fatal(err)
	}
	var result memory.BatchResult
	if err := json.Unmarshal(output.Bytes(), &result); err != nil || !result.Applied {
		t.Fatalf("batch CLI did not apply: %#v %v", result, err)
	}
	output.Reset()
	if err := runCLI(ctx, service, []string{"remember", "--confirm", "knowledge.demo"}, bytes.NewReader(nil), &output); err != nil || output.String() != "true\n" {
		t.Fatalf("confirm CLI failed: %q %v", output.String(), err)
	}
}

func TestKnowledgeCRUDCLI(t *testing.T) {
	ctx := context.Background()
	service := openCLIService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	var output bytes.Buffer
	for _, arguments := range [][]string{
		{"knowledge", "set", "product.goal", "first"},
		{"knowledge", "set", "product.goal", "second"},
		{"knowledge", "get", "product.goal"},
		{"knowledge", "list", "product"},
	} {
		output.Reset()
		if err := runCLI(ctx, service, arguments, bytes.NewReader(nil), &output); err != nil {
			t.Fatalf("%v: %v", arguments, err)
		}
	}
	var values []memory.Memory
	if err := json.Unmarshal(output.Bytes(), &values); err != nil || len(values) != 1 || values[0].Value == nil || *values[0].Value != "second" {
		t.Fatalf("knowledge list = %#v, %v", values, err)
	}
	output.Reset()
	if err := runCLI(ctx, service, []string{"knowledge", "delete", "product.goal"}, bytes.NewReader(nil), &output); err != nil || output.String() != "true\n" {
		t.Fatalf("knowledge delete = %q, %v", output.String(), err)
	}
}
