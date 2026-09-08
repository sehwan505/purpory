package app

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/sehwan505/purpory/internal/project"
	"github.com/sehwan505/purpory/internal/store"
)

func TestResolveProjectAndExpectedOpenPreserveWorktreeAssignments(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	ctx := context.Background()
	repository := t.TempDir()
	runGit(t, repository, "init", "-q")
	runGit(t, repository, "config", "user.name", "Purpory Test")
	runGit(t, repository, "config", "user.email", "purpory@example.test")
	if err := os.WriteFile(filepath.Join(repository, "README.md"), []byte("demo\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runGit(t, repository, "add", "README.md")
	runGit(t, repository, "commit", "-qm", "initial")

	databasePath := filepath.Join(t.TempDir(), "purpory.db")
	registered, err := RegisterProject(ctx, repository, databasePath, "demo", "Demo")
	if err != nil {
		t.Fatal(err)
	}
	worktree := filepath.Join(t.TempDir(), "worktree")
	runGit(t, repository, "worktree", "add", "--detach", worktree)

	resolved, err := ResolveProject(ctx, worktree, databasePath)
	if err != nil || resolved != registered {
		t.Fatalf("resolved project = %#v, %v; want %#v", resolved, err, registered)
	}
	assertStoredViewCount(t, databasePath, registered, 1)

	if service, err := OpenExpectedProject(ctx, worktree, databasePath, "other"); err == nil {
		service.Close()
		t.Fatal("mismatched expected project was accepted")
	}
	service, err := OpenExpectedProject(ctx, worktree, databasePath, registered.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := service.SaveSessionAt(ctx, worktree, "codex:test", "codex", "active"); err != nil {
		service.Close()
		t.Fatal(err)
	}
	if err := service.Close(); err != nil {
		t.Fatal(err)
	}
	assertStoredViewCount(t, databasePath, registered, 1)

	database, err := store.Open(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	workspace, err := database.Workspace(ctx, registered)
	if err != nil || len(workspace.UnmappedSessions) != 1 || workspace.UnmappedSessions[0].ID != "codex:test" {
		t.Fatalf("expected session was not saved without refreshing the worktree: %#v, %v", workspace, err)
	}
}

func runGit(t *testing.T, root string, arguments ...string) {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", root}, arguments...)...)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v: %s", arguments, err, output)
	}
}

func assertStoredViewCount(t *testing.T, databasePath string, current project.Project, want int) {
	t.Helper()
	database, err := store.Open(context.Background(), databasePath)
	if err != nil {
		t.Fatal(err)
	}
	workspace, loadErr := database.Workspace(context.Background(), current)
	closeErr := database.Close()
	if loadErr != nil || closeErr != nil || len(workspace.Resources) != 1 || len(workspace.Resources[0].Views) != want {
		t.Fatalf("stored views = %#v, load=%v close=%v; want %d", workspace.Resources, loadErr, closeErr, want)
	}
}
