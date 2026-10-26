package store

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/material"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/prepare"
	"github.com/sehwan505/purpory/internal/project"
)

func TestModelDecisionRemovalPreservesCanonicalDataAndBackup(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "purpory.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	old := &Store{db: db, path: path}
	defer old.Close()
	if err := old.configure(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL DEFAULT (unixepoch())) STRICT"); err != nil {
		t.Fatal(err)
	}
	entries, err := migrations.ReadDir("migrations")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		version, err := strconv.Atoi(strings.SplitN(entry.Name(), "_", 2)[0])
		if err != nil {
			t.Fatal(err)
		}
		if version >= 27 {
			break
		}
		script, err := migrations.ReadFile("migrations/" + entry.Name())
		if err != nil {
			t.Fatal(err)
		}
		if err := old.applyMigration(ctx, version, string(script)); err != nil {
			t.Fatal(err)
		}
	}
	if err := old.SaveProject(ctx, project.Project{ID: "demo", Name: "Demo", Root: "/demo"}); err != nil {
		t.Fatal(err)
	}
	value := "Preserve explicit knowledge."
	entry, err := memory.New("demo", "policy.keep", memory.Decision, &value, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := old.SaveMemory(ctx, entry); err != nil {
		t.Fatal(err)
	}
	link := graph.Link{SourceKind: graph.KindIntent, SourceRef: entry.Key, Relation: graph.RelationRealizedBy, TargetKind: graph.KindMaterial, TargetRef: "file:guide.md"}
	if err := old.SaveLink(ctx, "demo", link); err != nil {
		t.Fatal(err)
	}
	if err := old.SaveEmbedding(ctx, "demo", graph.ReferenceID(graph.KindIntent, entry.Key), entry.Hash, "legacy-embed", []float64{1}); err != nil {
		t.Fatal(err)
	}
	if err := old.SaveSession(ctx, "demo", "", "codex:one", "codex", "ended"); err != nil {
		t.Fatal(err)
	}
	if err := old.AppendNavigation(ctx, "demo", "codex:one", []prepare.NavigationEvent{{Action: "explain", TargetNodeID: "intent:policy.keep"}}); err != nil {
		t.Fatal(err)
	}
	for key, value := range map[string]string{"model.gate": "old-gate", "model.reconcile": "old-model", "model.embedding": "legacy-embed"} {
		if err := old.SaveSetting(ctx, key, value); err != nil {
			t.Fatal(err)
		}
	}
	if err := old.SaveCredential(ctx, "provider.openai-codex.oauth", []byte("encrypted-token")); err != nil {
		t.Fatal(err)
	}
	if err := old.Close(); err != nil {
		t.Fatal(err)
	}

	current, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer current.Close()
	got, err := current.Memory(ctx, "demo", entry.Key)
	if err != nil || got.Hash != entry.Hash {
		t.Fatalf("memory lost: %#v %v", got, err)
	}
	versions, err := current.MemoryVersions(ctx, "demo", entry.Key)
	if err != nil || len(versions) != 1 {
		t.Fatalf("versions lost: %#v %v", versions, err)
	}
	_, edges, err := current.Graph(ctx, "demo")
	if err != nil || len(edges) != 1 {
		t.Fatalf("durable edge lost: %#v %v", edges, err)
	}
	vectors, err := current.Embeddings(ctx, "demo", "legacy-embed")
	if err != nil || len(vectors) != 1 {
		t.Fatalf("embeddings lost: %#v %v", vectors, err)
	}
	trail, err := current.Navigation(ctx, "demo", "codex:one", 8)
	if err != nil || len(trail) != 1 {
		t.Fatalf("navigation lost: %#v %v", trail, err)
	}
	var obsolete int
	if err := current.db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE name IN ('context_decisions','gate_feedback','context_requests','reconciliation_events')").Scan(&obsolete); err != nil || obsolete != 0 {
		t.Fatalf("obsolete tables retained: %d %v", obsolete, err)
	}
	if _, found, err := current.Setting(ctx, "model.gate"); err != nil || found {
		t.Fatalf("gate setting retained: %v %v", found, err)
	}
	if _, found, err := current.Credential(ctx, "provider.openai-codex.oauth"); err != nil || found {
		t.Fatalf("OAuth credential retained: %v %v", found, err)
	}
	binding, found, err := current.Setting(ctx, "model.embedding")
	if err != nil || !found || binding != `{"provider":"ollama","model":"legacy-embed","dimensions":512}` {
		t.Fatalf("embedding binding not migrated: %q %v", binding, err)
	}
	backups, err := filepath.Glob(path + ".before-027-*.bak")
	if err != nil || len(backups) != 1 {
		t.Fatalf("missing migration backup: %#v %v", backups, err)
	}
	backup, err := OpenReadOnly(ctx, backups[0])
	if err != nil {
		t.Fatal(err)
	}
	defer backup.Close()
	if value, found, err := backup.Setting(ctx, "model.gate"); err != nil || !found || value != "old-gate" {
		t.Fatalf("backup incomplete: %q %v %v", value, found, err)
	}
	if info, err := os.Stat(backups[0]); err != nil || info.Size() == 0 {
		t.Fatalf("missing backup: %v", err)
	}
}

func TestKnowledgePublishRejectsStaleProcess(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "purpory.db")
	first, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if err := first.SaveProject(ctx, project.Project{ID: "demo", Name: "Demo"}); err != nil {
		t.Fatal(err)
	}
	second, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	revision, err := second.KnowledgeRevision(ctx, "demo")
	if err != nil {
		t.Fatal(err)
	}
	materials := []material.Material{{ID: "one", URI: "file:one.md", MediaType: "text/markdown", Hash: "one"}}
	if err := first.ReplaceKnowledge(ctx, "demo", materials, nil, nil, nil, revision); err != nil {
		t.Fatal(err)
	}
	if err := second.ReplaceKnowledge(ctx, "demo", nil, nil, nil, nil, revision); err == nil {
		t.Fatal("stale process overwrote a committed snapshot")
	}
	got, err := first.Materials(ctx, "demo")
	if err != nil || len(got) != 1 || got[0].ID != "one" {
		t.Fatalf("snapshot lost: %#v %v", got, err)
	}
}

func TestWorkspaceSelectionBreaksTimestampTiesByID(t *testing.T) {
	ctx := context.Background()
	database, err := Open(ctx, filepath.Join(t.TempDir(), "purpory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	for _, id := range []string{"b", "a"} {
		if err := database.SaveProject(ctx, project.Project{ID: id, Name: id}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := database.db.ExecContext(ctx, "UPDATE projects SET updated_at = 1, created_at = 1"); err != nil {
		t.Fatal(err)
	}
	projects, err := database.Projects(ctx)
	if err != nil || projects[0].ID != "a" {
		t.Fatalf("project tie: %#v %v", projects, err)
	}
	resources := []project.Resource{{ID: "b", Provider: "folder", Label: "same", Identity: "b"}, {ID: "a", Provider: "folder", Label: "same", Identity: "a", Views: []project.View{{ID: "b", Root: "/b", Available: true}, {ID: "a", Root: "/a", Available: true}}}}
	if err := database.SaveWorkspace(ctx, "a", resources); err != nil {
		t.Fatal(err)
	}
	if _, err := database.db.ExecContext(ctx, "UPDATE views SET observed_at = 1"); err != nil {
		t.Fatal(err)
	}
	workspace, err := database.Workspace(ctx, projects[0])
	if err != nil || workspace.Resources[0].ID != "a" || workspace.Resources[0].Views[0].ID != "a" {
		t.Fatalf("view tie: %#v %v", workspace, err)
	}
}

func TestManualBatchRejectsStaleHashesWithoutPartialWrites(t *testing.T) {
	ctx := context.Background()
	database, err := Open(ctx, filepath.Join(t.TempDir(), "purpory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if err := database.SaveProject(ctx, project.Project{ID: "demo", Name: "Demo"}); err != nil {
		t.Fatal(err)
	}
	old := "before"
	for _, key := range []string{"policy.one", "policy.two"} {
		entry, err := memory.New("demo", key, memory.Note, &old, nil)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := database.SaveMemory(ctx, entry); err != nil {
			t.Fatal(err)
		}
	}
	first, err := database.Memory(ctx, "demo", "policy.one")
	if err != nil {
		t.Fatal(err)
	}
	next, stale := "after", "stale"
	changes := []memory.BatchChange{
		{Key: "policy.one", Kind: memory.Note, Value: &next, ExpectedHash: &first.Hash, ExpectedHashSet: true},
		{Key: "policy.two", Kind: memory.Note, Value: &next, ExpectedHash: &stale, ExpectedHashSet: true},
	}
	result, err := database.PreviewMemoryBatch(ctx, "demo", changes, true)
	if err != nil || result.Applied || result.Changes[1].Action != "conflict" {
		t.Fatalf("stale batch accepted: %#v %v", result, err)
	}
	unchanged, err := database.Memory(ctx, "demo", "policy.one")
	if err != nil || unchanged.Hash != first.Hash {
		t.Fatalf("partial batch write: %#v %v", unchanged, err)
	}
	changes[1].ExpectedHash = &first.Hash
	result, err = database.PreviewMemoryBatch(ctx, "demo", changes, true)
	if err != nil || !result.Applied || result.Changes[0].VersionID == 0 || result.Changes[1].VersionID == 0 {
		t.Fatalf("valid batch rejected: %#v %v", result, err)
	}
}
