package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	product "github.com/sehwan505/purpory/internal/app"
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

func TestNormalizeRememberArguments(t *testing.T) {
	got := normalizeRememberArguments([]string{"decision.database", "--kind", "decision", "--value", "Use SQLite"})
	want := []string{"--kind", "decision", "--value", "Use SQLite", "decision.database"}
	if len(got) != len(want) {
		t.Fatalf("unexpected arguments: %#v", got)
	}
	for index := range want {
		if got[index] != want[index] {
			t.Fatalf("unexpected arguments: %#v", got)
		}
	}
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
	if err != nil || len(found.Nodes) != 1 || found.Nodes[0].Content != "A project for everyone." {
		t.Fatalf("updated content missing: %#v, %v", found, err)
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
	arguments := []string{"prepare", "decision.database", "--session", "cli", "--budget", "512", "--path", "internal/store", "--json", "--no-retain-input"}
	if err := runCLI(context.Background(), service, arguments, bytes.NewReader(nil), &output); err != nil {
		t.Fatal(err)
	}
	var result product.PrepareResult
	if err := json.Unmarshal(output.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Action != "retrieve" || len(result.Deliveries) != 1 || result.DecisionID == 0 {
		t.Fatalf("unexpected prepare result: %#v", result)
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
	if err := runCLI(ctx, service, []string{"remember", "knowledge.demo", "--confirm"}, bytes.NewReader(nil), &output); err != nil || output.String() != "true\n" {
		t.Fatalf("confirm CLI failed: %q %v", output.String(), err)
	}
}
