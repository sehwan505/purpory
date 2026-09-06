package integration

import (
	"encoding/json"
	"errors"
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
	previousSkillPath := filepath.Join(directory, "skills", previousSkillName, "SKILL.md")
	if err := os.MkdirAll(filepath.Dir(previousSkillPath), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(previousSkillPath, []byte("previous"), 0o600); err != nil {
		t.Fatal(err)
	}
	if action, err := Install("codex"); err != nil || action != "installed" {
		t.Fatalf("install: %q %v", action, err)
	}
	content, _ := os.ReadFile(path)
	if strings.Count(string(content), startMarker) != 1 || !strings.Contains(string(content), "# Existing") || !strings.Contains(string(content), "graph hints") || strings.Contains(string(content), "explore on") {
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
	skillPath := filepath.Join(directory, "skills", skillName, "SKILL.md")
	skill, err := os.ReadFile(skillPath)
	if err != nil || !strings.Contains(string(skill), "purpory explore on") || !strings.Contains(string(skill), "complete scope") {
		t.Fatalf("curation skill not installed: %s, %v", skill, err)
	}
	if _, err := os.Stat(previousSkillPath); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("previous skill was not removed: %v", err)
	}
	metadata, err := os.ReadFile(filepath.Join(directory, "skills", skillName, "agents", "openai.yaml"))
	if err != nil || !strings.Contains(string(metadata), "allow_implicit_invocation: false") {
		t.Fatalf("curation skill metadata not installed: %s, %v", metadata, err)
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
	if _, err := os.Stat(skillPath); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("curation skill was not removed: %v", err)
	}

	claudeDirectory := t.TempDir()
	if changed, err := configureSkill(claudeDirectory, "claude", true); err != nil || !changed {
		t.Fatalf("install Claude curation skill: %v, %v", changed, err)
	}
	claudeSkill, err := os.ReadFile(filepath.Join(claudeDirectory, "skills", skillName, "SKILL.md"))
	if err != nil || !strings.Contains(string(claudeSkill), "disable-model-invocation: true") {
		t.Fatalf("Claude curation skill not installed as explicit-only: %s, %v", claudeSkill, err)
	}
	if _, err := os.Stat(filepath.Join(claudeDirectory, "skills", skillName, "agents", "openai.yaml")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("Codex metadata installed for Claude: %v", err)
	}
}
