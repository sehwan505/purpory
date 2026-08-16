package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/sehwan505/purpory/internal/launch"
	"github.com/sehwan505/purpory/internal/project"
	"github.com/sehwan505/purpory/internal/store"
)

func TestDesktopRootUsesKnownCurrentThenRecentProject(t *testing.T) {
	ctx := context.Background()
	directory := t.TempDir()
	current := filepath.Join(directory, "current")
	recent := filepath.Join(directory, "recent")
	for _, path := range []string{current, recent} {
		if err := os.Mkdir(path, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	databasePath := filepath.Join(directory, "current.db")
	database, err := store.Open(ctx, databasePath)
	if err != nil {
		t.Fatal(err)
	}
	if err := database.SaveProject(ctx, project.Project{ID: "current", Name: "Current", Root: current}); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}

	if got := desktopConfig(ctx, launch.Config{Root: current, DBPath: databasePath}); got.Root != current || got.ProjectID != "current" {
		t.Fatalf("known current project = %#v", got)
	}
	recentDatabasePath := filepath.Join(directory, "recent.db")
	recentDatabase, err := store.Open(ctx, recentDatabasePath)
	if err != nil {
		t.Fatal(err)
	}
	if err := recentDatabase.SaveProject(ctx, project.Project{ID: "recent", Name: "Recent", Root: recent}); err != nil {
		t.Fatal(err)
	}
	if err := recentDatabase.Close(); err != nil {
		t.Fatal(err)
	}
	if got := desktopConfig(ctx, launch.Config{Root: string(os.PathSeparator), DBPath: recentDatabasePath}); got.Root != recent || got.ProjectID != "recent" {
		t.Fatalf("recent project = %#v", got)
	}
	explicit := filepath.Join(directory, "explicit")
	if got := desktopConfig(ctx, launch.Config{Root: explicit, RootSet: true, DBPath: databasePath}); got.Root != explicit || got.ProjectID != "" {
		t.Fatalf("explicit config = %#v", got)
	}
}
