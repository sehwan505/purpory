package cli

import (
	"bytes"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sehwan505/purpory/internal/project"
)

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
	if code := Run([]string{"--db", database, "project", "add", root, "--id", "demo", "--name", "Demo"}, strings.NewReader(""), &output, &errorOutput); code != 0 {
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
}
