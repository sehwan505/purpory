package project

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestDiscoverFolder(t *testing.T) {
	root := t.TempDir()
	resolved, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}
	workspace, err := (Local{}).Observe(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	resource, view := workspace.Resources[0], workspace.Resources[0].Views[0]
	if resource.Provider != "folder" || view.Root != resolved || resource.ID == "" || view.ID == "" {
		t.Fatalf("unexpected workspace: %#v %#v", resource, view)
	}
}

func TestDiscoverGit(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is unavailable")
	}
	root := t.TempDir()
	if output, err := exec.Command("git", "-C", root, "init", "-q").CombinedOutput(); err != nil {
		t.Fatalf("git init: %s: %v", output, err)
	}
	resolved, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}
	workspace, err := (Local{}).Observe(context.Background(), resolved)
	if err != nil {
		t.Fatal(err)
	}
	resource, view := workspace.Resources[0], workspace.Resources[0].Views[0]
	if resource.Provider != "git" || view.Root != resolved {
		t.Fatalf("unexpected workspace: %#v %#v", resource, view)
	}
}

func TestObserveGitIncludesEveryWorktree(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is unavailable")
	}
	root := t.TempDir()
	commands := [][]string{
		{"init", "-q"},
		{"config", "user.email", "purpory@example.com"},
		{"config", "user.name", "Purpory"},
	}
	for _, arguments := range commands {
		if output, err := exec.Command("git", append([]string{"-C", root}, arguments...)...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %s: %v", arguments, output, err)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "README.md"), []byte("demo"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, arguments := range [][]string{{"add", "README.md"}, {"commit", "-qm", "initial"}} {
		if output, err := exec.Command("git", append([]string{"-C", root}, arguments...)...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %s: %v", arguments, output, err)
		}
	}
	child := filepath.Join(t.TempDir(), "feature")
	if output, err := exec.Command("git", "-C", root, "worktree", "add", "-qb", "feature", child).CombinedOutput(); err != nil {
		t.Fatalf("git worktree: %s: %v", output, err)
	}
	workspace, err := (Local{}).Observe(context.Background(), child)
	if err != nil {
		t.Fatal(err)
	}
	primary, _ := filepath.EvalSymlinks(root)
	active, _ := filepath.EvalSymlinks(child)
	if workspace.Project.ID != primary || workspace.Project.Root != active || len(workspace.Resources) != 1 || len(workspace.Resources[0].Views) != 2 {
		t.Fatalf("unexpected worktree topology: %#v", workspace)
	}
}
