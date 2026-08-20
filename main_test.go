package main

import (
	"context"
	"path/filepath"
	"testing"

	product "github.com/sehwan505/purpory/internal/app"
)

func TestDesktopOpensBeforeFirstProjectAndRestoresSelection(t *testing.T) {
	ctx := context.Background()
	databasePath := filepath.Join(t.TempDir(), "purpory.db")
	service, err := product.OpenDesktop(ctx, databasePath, "")
	if err != nil {
		t.Fatal(err)
	}
	if status := service.Status(); status.Project.ID != "" {
		t.Fatalf("unexpected initial Project: %#v", status.Project)
	}
	if runs, err := service.Reconciliations(ctx); err != nil || len(runs) != 0 {
		t.Fatalf("empty desktop could not load: %#v %v", runs, err)
	}
	created, err := service.CreateProject(ctx, "First Project")
	if err != nil || created.Project.ID == "" || created.Project.Root != "" {
		t.Fatalf("rootless Project was not created: %#v %v", created, err)
	}
	if err := service.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := product.OpenDesktop(ctx, databasePath, "")
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	if status := reopened.Status(); status.Project.ID != created.Project.ID {
		t.Fatalf("last Project was not restored: %#v", status.Project)
	}
}
