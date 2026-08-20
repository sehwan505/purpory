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
	"github.com/sehwan505/purpory/internal/project"
	"github.com/sehwan505/purpory/internal/store"
)

func TestVersionDoesNotRequireRegisteredProject(t *testing.T) {
	var output, errorOutput bytes.Buffer
	code := Run([]string{"--root", t.TempDir(), "--db", filepath.Join(t.TempDir(), "purpory.db"), "version"}, strings.NewReader(""), &output, &errorOutput)
	if code != 0 || strings.TrimSpace(output.String()) != product.Version {
		t.Fatalf("version failed: stdout=%q stderr=%q", output.String(), errorOutput.String())
	}
}

func TestProjectMustBeRegisteredBeforeNormalCommands(t *testing.T) {
	root := t.TempDir()
	database := filepath.Join(t.TempDir(), "purpory.db")
	var output, errorOutput bytes.Buffer
	if code := Run([]string{"--root", root, "--db", database, "query", "anything"}, strings.NewReader(""), &output, &errorOutput); code == 0 {
		t.Fatal("query unexpectedly created a project")
	}
	if !strings.Contains(errorOutput.String(), "project is not registered") {
		t.Fatalf("missing registration guidance: %q", errorOutput.String())
	}

	output.Reset()
	errorOutput.Reset()
	if code := Run([]string{"--db", database, "project", "add", "--id", "demo", "--name", "Demo", root}, strings.NewReader(""), &output, &errorOutput); code != 0 {
		t.Fatalf("project add failed: %s", errorOutput.String())
	}
	var registered project.Project
	if err := json.Unmarshal(output.Bytes(), &registered); err != nil || registered.ID != "demo" || registered.Name != "Demo" {
		t.Fatalf("unexpected registered project: %#v, %v", registered, err)
	}

	output.Reset()
	errorOutput.Reset()
	if code := Run([]string{"--root", root, "--db", database, "query", "anything"}, strings.NewReader(""), &output, &errorOutput); code != 0 {
		t.Fatalf("registered project was not resolved: %s", errorOutput.String())
	}

	output.Reset()
	errorOutput.Reset()
	if code := Run([]string{"--db", database, "project", "remove", "demo"}, strings.NewReader(""), &output, &errorOutput); code != 0 || strings.TrimSpace(output.String()) != "true" {
		t.Fatalf("project remove failed: stdout=%q stderr=%q", output.String(), errorOutput.String())
	}
	output.Reset()
	errorOutput.Reset()
	if code := Run([]string{"--root", root, "--db", database, "query", "anything"}, strings.NewReader(""), &output, &errorOutput); code == 0 {
		t.Fatal("removed project still accepted commands")
	}
}

func TestIntegrationDoesNotRequireRegisteredProject(t *testing.T) {
	directory := t.TempDir()
	t.Setenv("CODEX_HOME", directory)
	var output, errorOutput bytes.Buffer
	code := Run([]string{"--root", t.TempDir(), "--db", filepath.Join(t.TempDir(), "purpory.db"), "integration", "codex", "install"}, strings.NewReader(""), &output, &errorOutput)
	if code != 0 || strings.TrimSpace(output.String()) != "installed" {
		t.Fatalf("integration install failed: stdout=%q stderr=%q", output.String(), errorOutput.String())
	}
	for _, name := range []string{"AGENTS.md", "hooks.json"} {
		if _, err := os.Stat(filepath.Join(directory, name)); err != nil {
			t.Fatalf("global %s missing: %v", name, err)
		}
	}
}

func TestUnregisteredAgentHookIsNoOp(t *testing.T) {
	root := t.TempDir()
	database := filepath.Join(t.TempDir(), "purpory.db")
	payload := `{"hook_event_name":"UserPromptSubmit","prompt":"hello","session_id":"one","cwd":"` + root + `"}`
	var output, errorOutput bytes.Buffer
	if code := Run([]string{"--root", root, "--db", database, "preflight", "codex"}, strings.NewReader(payload), &output, &errorOutput); code != 0 {
		t.Fatalf("unregistered hook failed: %s", errorOutput.String())
	}
	if output.Len() != 0 || errorOutput.Len() != 0 {
		t.Fatalf("unregistered hook was not silent: stdout=%q stderr=%q", output.String(), errorOutput.String())
	}
	databaseStore, err := store.Open(context.Background(), database)
	if err != nil {
		t.Fatal(err)
	}
	defer databaseStore.Close()
	observations, err := databaseStore.Observations(context.Background())
	resolvedRoot, resolveErr := filepath.EvalSymlinks(root)
	if err != nil || resolveErr != nil || len(observations) != 1 || observations[0].Resource.Views[0].Root != resolvedRoot {
		t.Fatalf("hook observation missing: %#v %v", observations, err)
	}
	if projects, err := databaseStore.Projects(context.Background()); err != nil || len(projects) != 0 {
		t.Fatalf("hook created a project: %#v %v", projects, err)
	}
}

func TestAgentHookUsesPayloadWorkingDirectory(t *testing.T) {
	firstRoot := t.TempDir()
	secondRoot := t.TempDir()
	database := filepath.Join(t.TempDir(), "purpory.db")
	for id, root := range map[string]string{"first": firstRoot, "second": secondRoot} {
		if _, err := product.RegisterProject(context.Background(), root, database, id, ""); err != nil {
			t.Fatal(err)
		}
	}
	payload, _ := json.Marshal(map[string]string{
		"hook_event_name": "UserPromptSubmit", "prompt": "hello", "session_id": "one", "cwd": secondRoot,
	})
	var output, errorOutput bytes.Buffer
	if code := Run([]string{"--root", firstRoot, "--db", database, "preflight", "codex"}, bytes.NewReader(payload), &output, &errorOutput); code != 0 {
		t.Fatalf("hook failed: %s", errorOutput.String())
	}
	for root, want := range map[string]int{firstRoot: 0, secondRoot: 1} {
		service, err := product.Open(context.Background(), root, database, "")
		if err != nil {
			t.Fatal(err)
		}
		decisions, err := service.ContextDecisions(context.Background(), 10)
		service.Close()
		if err != nil || len(decisions) != want {
			t.Fatalf("decisions for %s = %d, want %d: %v", root, len(decisions), want, err)
		}
	}
}
