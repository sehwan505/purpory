package integration

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInstallAndUninstall(t *testing.T) {
	directory := t.TempDir()
	t.Setenv("CODEX_HOME", directory)
	path := filepath.Join(directory, "AGENTS.md")
	if err := os.WriteFile(path, []byte("# Existing\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	if action, err := Install("codex"); err != nil || action != "installed" {
		t.Fatalf("install: %q %v", action, err)
	}
	content, _ := os.ReadFile(path)
	if strings.Count(string(content), startMarker) != 1 || !strings.Contains(string(content), "# Existing") || !strings.Contains(string(content), "graph hints") {
		t.Fatalf("unexpected content: %s", content)
	}
	hooksContent, err := os.ReadFile(filepath.Join(directory, "hooks.json"))
	if err != nil {
		t.Fatal(err)
	}
	var settings map[string]any
	if err := json.Unmarshal(hooksContent, &settings); err != nil || !strings.Contains(string(hooksContent), "UserPromptSubmit") || !strings.Contains(string(hooksContent), "SessionEnd") {
		t.Fatalf("hooks not installed: %s, %v", hooksContent, err)
	}
	if action, err := Install("codex"); err != nil || action != "unchanged" {
		t.Fatalf("second install: %q %v", action, err)
	}
	if action, err := Uninstall("codex"); err != nil || action != "uninstalled" {
		t.Fatalf("uninstall: %q %v", action, err)
	}
	hooksContent, _ = os.ReadFile(filepath.Join(directory, "hooks.json"))
	if strings.Contains(string(hooksContent), "preflight codex") || strings.Contains(string(hooksContent), "session-end codex") {
		t.Fatalf("hooks not removed: %s", hooksContent)
	}
}
