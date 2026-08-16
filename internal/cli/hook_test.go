package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
)

func TestAgentHooksTrackSession(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "purpose.txt"), []byte("Keep project intent visible."), 0o600); err != nil {
		t.Fatal(err)
	}
	service := openCLIService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	if _, err := service.Update(context.Background()); err != nil {
		t.Fatal(err)
	}
	intent := "Keep project intent visible."
	if _, err := service.Remember(context.Background(), "intent.project", memory.Decision, &intent, nil); err != nil {
		t.Fatal(err)
	}
	payload := map[string]string{
		"hook_event_name": "UserPromptSubmit", "prompt": "project intent", "session_id": "one", "cwd": root,
	}
	encoded, _ := json.Marshal(payload)
	var output bytes.Buffer
	if err := runPreflight(context.Background(), service, "codex", bytes.NewReader(encoded), &output); err != nil {
		t.Fatal(err)
	}
	workspace, err := service.Workspace(context.Background())
	if err != nil || len(workspace.Resources[0].Views[0].Sessions) != 1 || workspace.Resources[0].Views[0].Sessions[0].Status != "active" || len(workspace.Resources[0].Views[0].Sessions[0].Deliveries) == 0 || output.Len() == 0 {
		t.Fatalf("session not started: %#v %q %v", workspace, output.String(), err)
	}
	payload["hook_event_name"] = "SessionEnd"
	transcript := filepath.Join(root, "session.jsonl")
	if err := os.WriteFile(transcript, []byte("{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":\"Keep intent visible.\"}}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	payload["transcript_path"] = transcript
	t.Setenv("PURPORY_RECONCILE_DIR", filepath.Join(t.TempDir(), "reconcile"))
	encoded, _ = json.Marshal(payload)
	jobPath, err := runSessionEnd(context.Background(), service, "codex", bytes.NewReader(encoded))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(jobPath); err != nil {
		t.Fatalf("reconciliation was not queued: %v", err)
	}
	workspace, _ = service.Workspace(context.Background())
	if workspace.Resources[0].Views[0].Sessions[0].Status != "ended" {
		t.Fatalf("session not ended: %#v", workspace)
	}
}

func TestHookContextPreservesAskAndSeparatesAwareness(t *testing.T) {
	requestID := int64(17)
	clarification := "Which environment do you mean?"
	ask := hookContext(product.PrepareResult{Action: "ask", RequestID: &requestID, Clarification: &clarification})
	if !strings.Contains(ask, "INTENT ALIGNMENT SUGGESTION") || !strings.Contains(ask, "Request ID: 17") || !strings.Contains(ask, clarification) {
		t.Fatalf("ask response missing: %q", ask)
	}
	relation := "calls"
	awareness := hookContext(product.PrepareResult{Action: "retrieve", Awareness: []contextprepare.Awareness{{NodeID: "token", Key: "material.token", Label: "TokenRepository", Relation: &relation}}})
	if !strings.Contains(awareness, "RELATED CONTEXT AVAILABLE — NOT LOADED") || strings.Contains(awareness, "USE FOR THIS TURN") {
		t.Fatalf("awareness response was promoted to evidence: %q", awareness)
	}
}

func TestPreflightFailsClosed(t *testing.T) {
	root := t.TempDir()
	service := openCLIService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	var output bytes.Buffer
	if err := runPreflight(context.Background(), service, "codex", strings.NewReader(`{"hook_event_name":"UserPromptSubmit"}`), &output); err != nil {
		t.Fatal(err)
	}
	var failure hookFailure
	if err := json.Unmarshal(output.Bytes(), &failure); err != nil || failure.Decision != "block" || failure.Reason == "" {
		t.Fatalf("preflight did not fail closed: %#v %v", failure, err)
	}
}

func TestReconciliationWorkerDrainsQueue(t *testing.T) {
	root := t.TempDir()
	database := filepath.Join(t.TempDir(), "purpory.db")
	transcript := filepath.Join(root, "session.jsonl")
	if err := os.WriteFile(transcript, []byte("{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"content\":\"temporary\"}}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PURPORY_RECONCILE_DIR", filepath.Join(t.TempDir(), "reconcile"))
	service := openCLIService(t, root, database, "demo")
	job, err := service.QueueSessionEnd(context.Background(), root, "codex:worker", "codex", transcript, "exit")
	if err != nil {
		t.Fatal(err)
	}
	_ = service.Close()
	if err := drainReconciliations(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(job); !os.IsNotExist(err) {
		t.Fatalf("worker did not complete queued job: %v", err)
	}
}
