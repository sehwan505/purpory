package agent

import (
	"context"
	"encoding/json"
	"os"
	"slices"
	"testing"
	"time"
)

func TestStructuredAgentCommands(t *testing.T) {
	schema := map[string]any{"type": "object", "properties": map[string]any{"ok": map[string]any{"type": "boolean"}}, "required": []string{"ok"}}
	for _, name := range []string{"codex", "claude"} {
		t.Run(name, func(t *testing.T) {
			client := &Client{name: name, path: name}
			client.run = func(_ context.Context, _ string, arguments []string, input, _ string) ([]byte, error) {
				if input == "" {
					t.Fatal("prompt was empty")
				}
				if name == "codex" {
					if !slices.Contains(arguments, "--ephemeral") || !slices.Contains(arguments, "--output-schema") {
						t.Fatalf("unsafe codex arguments: %v", arguments)
					}
					index := slices.Index(arguments, "--output-last-message")
					if index < 0 || index+1 == len(arguments) {
						t.Fatalf("codex output path missing: %v", arguments)
					}
					return nil, os.WriteFile(arguments[index+1], []byte(`{"ok":true}`), 0o600)
				}
				if !slices.Contains(arguments, "--no-session-persistence") || !slices.Contains(arguments, "--json-schema") {
					t.Fatalf("unsafe claude arguments: %v", arguments)
				}
				response, _ := json.Marshal(map[string]any{"subtype": "success", "structured_output": map[string]bool{"ok": true}})
				return response, nil
			}
			var result struct {
				OK bool `json:"ok"`
			}
			if err := client.GenerateJSON(context.Background(), "", "system", "prompt", schema, &result, 8192, time.Second); err != nil {
				t.Fatal(err)
			}
			if !result.OK {
				t.Fatal("structured result was not decoded")
			}
		})
	}
}
