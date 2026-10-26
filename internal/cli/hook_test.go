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
	if err != nil || len(workspace.Resources[0].Views[0].Sessions) != 1 || workspace.Resources[0].Views[0].Sessions[0].Status != "active" || len(workspace.Resources[0].Views[0].Sessions[0].Deliveries) != 0 || output.Len() == 0 || !strings.Contains(output.String(), `purpory explain`) || strings.Contains(output.String(), intent) {
		t.Fatalf("session not started: %#v %q %v", workspace, output.String(), err)
	}
	payload["hook_event_name"] = "SessionEnd"
	encoded, _ = json.Marshal(payload)
	if err := runSessionEnd(context.Background(), service, "codex", bytes.NewReader(encoded)); err != nil {
		t.Fatal(err)
	}
	workspace, _ = service.Workspace(context.Background())
	if workspace.Resources[0].Views[0].Sessions[0].Status != "ended" {
		t.Fatalf("session not ended: %#v", workspace)
	}
}

func TestHookContextReturnsHintMap(t *testing.T) {
	hints := hookContext(product.PrepareResult{Action: "retrieve", Hints: &contextprepare.HintMap{Nodes: []contextprepare.HintNode{{ID: "knowledge:token", Label: "TokenRepository", Kind: "knowledge", Match: "semantic"}}}})
	if !strings.Contains(hints, "MEMORY MAP — CONTENT NOT LOADED") || !strings.Contains(hints, `purpory explain`) || strings.Contains(hints, "USE FOR THIS TURN") {
		t.Fatalf("hint map was promoted to evidence: %q", hints)
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

func TestAgentSessionSupportsHermes(t *testing.T) {
	session, err := agentSession("HERMES", "one")
	if err != nil || session != "hermes:one" {
		t.Fatalf("Hermes session was not accepted: %q %v", session, err)
	}
}
