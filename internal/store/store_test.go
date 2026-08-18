package store

import (
	"context"
	"database/sql"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/material"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/project"
)

func TestMigrationAddsAwarenessFollowUpColumn(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "context.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, `
		CREATE TABLE schema_migrations (
			version INTEGER PRIMARY KEY,
			applied_at INTEGER NOT NULL DEFAULT (unixepoch())
		) STRICT;
		CREATE TABLE awareness_exposures (
			project_id TEXT NOT NULL,
			session_id TEXT NOT NULL,
			node_id TEXT NOT NULL,
			key TEXT NOT NULL,
			label TEXT NOT NULL,
			kind TEXT NOT NULL,
			source TEXT NOT NULL DEFAULT '',
			reason TEXT NOT NULL,
			relation TEXT,
			shown_at INTEGER NOT NULL DEFAULT (unixepoch()),
			PRIMARY KEY (project_id, session_id, node_id)
		) STRICT;
		INSERT INTO schema_migrations(version)
		VALUES (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12), (14), (15), (16);
	`); err != nil {
		db.Close()
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	database, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	var columns int
	if err := database.db.QueryRowContext(ctx, `
		SELECT count(*) FROM pragma_table_info('awareness_exposures') WHERE name = 'followed_up_at'
	`).Scan(&columns); err != nil || columns != 1 {
		t.Fatalf("followed_up_at columns = %d, %v", columns, err)
	}
}

func TestProjectRoundTrip(t *testing.T) {
	ctx := context.Background()
	store, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Error(err)
		}
	})

	want := project.Project{ID: "demo", Name: "Purpory", Root: "/projects/purpory"}
	if err := store.SaveProject(ctx, want); err != nil {
		t.Fatal(err)
	}
	got, err := store.Project(ctx, want.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Fatalf("project mismatch: got %#v, want %#v", got, want)
	}

	_, err = store.Project(ctx, "missing")
	if err == nil {
		t.Fatal("missing project returned no error")
	}
	removed, err := store.RemoveProject(ctx, want.ID)
	if err != nil || !removed {
		t.Fatalf("remove project = %v, %v", removed, err)
	}
	if _, err := store.Project(ctx, want.ID); err == nil {
		t.Fatal("removed project remained registered")
	}
	if err := store.SaveProject(ctx, want); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Project(ctx, want.ID); err != nil {
		t.Fatalf("project was not re-registered: %v", err)
	}
}

func TestProjectEmbeddingModelIsImmutable(t *testing.T) {
	ctx := context.Background()
	database, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	for _, value := range []project.Project{{ID: "one", Name: "One", Root: "/one"}, {ID: "two", Name: "Two", Root: "/two"}} {
		if err := database.SaveProject(ctx, value); err != nil {
			t.Fatal(err)
		}
	}
	if err := database.SetProjectEmbeddingModel(ctx, "one", "embed-a"); err != nil {
		t.Fatal(err)
	}
	if err := database.SetProjectEmbeddingModel(ctx, "one", "embed-a"); err != nil {
		t.Fatalf("same model should remain valid: %v", err)
	}
	if err := database.SetProjectEmbeddingModel(ctx, "one", "embed-b"); err == nil {
		t.Fatal("project embedding model was changed")
	}
	if _, err := database.db.ExecContext(ctx, `UPDATE projects SET embedding_model = 'embed-b' WHERE id = 'one'`); err == nil {
		t.Fatal("database trigger allowed the project embedding model to change")
	}
	if err := database.SetProjectEmbeddingModel(ctx, "two", "embed-b"); err != nil {
		t.Fatalf("another project could not select its own model: %v", err)
	}
}

func TestProjectsOrdersMostRecentFirst(t *testing.T) {
	ctx := context.Background()
	database, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = database.Close() })
	for _, value := range []project.Project{{ID: "old", Name: "Old", Root: "/old"}, {ID: "new", Name: "New", Root: "/new"}} {
		if err := database.SaveProject(ctx, value); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := database.db.ExecContext(ctx, "UPDATE projects SET updated_at = CASE id WHEN 'old' THEN 1 ELSE 2 END"); err != nil {
		t.Fatal(err)
	}
	values, err := database.Projects(ctx)
	if err != nil || len(values) != 2 || values[0].ID != "new" {
		t.Fatalf("unexpected projects: %#v, %v", values, err)
	}
}

func TestProjectForWorkspaceUsesRegisteredResourceAndRejectsAmbiguity(t *testing.T) {
	ctx := context.Background()
	database, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = database.Close() })
	resource := project.Resource{ID: "repository", Provider: "git", Identity: "/shared/.git", Views: []project.View{{ID: "primary", Root: "/primary"}}}
	first := project.Project{ID: "first", Name: "First", Root: "/primary"}
	if err := database.SaveProject(ctx, first); err != nil {
		t.Fatal(err)
	}
	if err := database.SaveWorkspace(ctx, first.ID, []project.Resource{resource}); err != nil {
		t.Fatal(err)
	}
	observed := project.Workspace{Project: project.Project{Root: "/worktree"}, Resources: []project.Resource{{Provider: "git", Identity: "/shared/.git"}}}
	got, err := database.ProjectForWorkspace(ctx, observed, "")
	if err != nil || got.ID != first.ID {
		t.Fatalf("resource did not resolve project: %#v, %v", got, err)
	}

	second := project.Project{ID: "second", Name: "Second", Root: "/second"}
	if err := database.SaveProject(ctx, second); err != nil {
		t.Fatal(err)
	}
	if err := database.SaveWorkspace(ctx, second.ID, []project.Resource{resource}); err != nil {
		t.Fatal(err)
	}
	if _, err := database.ProjectForWorkspace(ctx, observed, ""); !errors.Is(err, project.ErrAmbiguous) {
		t.Fatalf("ambiguous workspace error = %v", err)
	}
	if got, err := database.ProjectForWorkspace(ctx, observed, second.ID); err != nil || got.ID != second.ID {
		t.Fatalf("explicit project did not resolve ambiguity: %#v, %v", got, err)
	}
}

func TestProjectForWorkspaceUsesMostSpecificRegisteredRoot(t *testing.T) {
	ctx := context.Background()
	directory := t.TempDir()
	database, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = database.Close() })
	parent := project.Project{ID: "parent", Name: "Parent", Root: filepath.Dir(directory)}
	specific := project.Project{ID: "specific", Name: "Specific", Root: directory}
	for _, value := range []project.Project{parent, specific} {
		if err := database.SaveProject(ctx, value); err != nil {
			t.Fatal(err)
		}
	}
	observed := project.Workspace{Project: project.Project{Root: filepath.Join(directory, "child")}}
	got, err := database.ProjectForWorkspace(ctx, observed, "")
	if err != nil || got.ID != specific.ID {
		t.Fatalf("most specific root was not selected: %#v, %v", got, err)
	}
}

func TestKnowledgeRoundTrip(t *testing.T) {
	ctx := context.Background()
	database, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = database.Close() })
	var legacyTables int
	if err := database.db.QueryRowContext(ctx, `SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'links'`).Scan(&legacyTables); err != nil || legacyTables != 0 {
		t.Fatalf("legacy links table remains: %d, %v", legacyTables, err)
	}
	current := project.Project{ID: "demo", Name: "Demo", Root: "/demo"}
	if err := database.SaveProject(ctx, current); err != nil {
		t.Fatal(err)
	}
	materials := []material.Material{{ID: "readme", URI: "file:README.md", MediaType: "text/markdown", Hash: "abc", Size: 10}}
	materialNodeID := graph.ReferenceID(graph.KindMaterial, materials[0].URI)
	sectionNodeID := graph.ReferenceID(graph.KindKnowledge, "section")
	nodes := []graph.Node{
		{ID: materialNodeID, Label: "README.md", Kind: graph.KindMaterial, Ref: materials[0].URI, MaterialID: "readme", MaterialURI: materials[0].URI},
		{ID: sectionNodeID, Label: "Purpose", Kind: graph.KindKnowledge, Subkind: "section", Ref: "section", MaterialID: "readme", MaterialURI: materials[0].URI, Locator: "line:3", Content: "Project context"},
	}
	claims := []graph.Claim{{MaterialID: "readme", SourceID: materialNodeID, TargetID: sectionNodeID, Relation: "contains"}}
	edges := []graph.Edge{{SourceID: materialNodeID, TargetID: sectionNodeID, Relation: "contains"}}
	if err := database.ReplaceKnowledge(ctx, current.ID, materials, nodes, claims, edges); err != nil {
		t.Fatal(err)
	}
	gotMaterials, err := database.Materials(ctx, current.ID)
	if err != nil || len(gotMaterials) != 1 || gotMaterials[0].URI != materials[0].URI {
		t.Fatalf("materials mismatch: %#v, %v", gotMaterials, err)
	}
	gotNodes, gotClaims, err := database.Knowledge(ctx, current.ID)
	if err != nil || len(gotNodes) != 2 || len(gotClaims) != 1 || gotNodes[1].MaterialID != "readme" {
		t.Fatalf("knowledge mismatch: %#v, %#v, %v", gotNodes, gotClaims, err)
	}
	found, err := database.SearchNodes(ctx, current.ID, "Purpose", 10)
	if err != nil || len(found) != 1 || found[0].MaterialID != "readme" {
		t.Fatalf("search mismatch: %#v, %v", found, err)
	}
}

func TestUpdateSnapshotPreservesIntentLinks(t *testing.T) {
	ctx := context.Background()
	database, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = database.Close() })
	current := project.Project{ID: "demo", Name: "Demo", Root: "/demo"}
	if err := database.SaveProject(ctx, current); err != nil {
		t.Fatal(err)
	}
	intentValue := "Accessibility is required."
	intent, err := memory.New(current.ID, "purpose.accessibility", memory.Decision, &intentValue, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.SaveMemory(ctx, intent); err != nil {
		t.Fatal(err)
	}
	link := graph.Link{SourceKind: "intent", SourceRef: "purpose.accessibility", Relation: graph.RelationAppliesTo, TargetKind: "material", TargetRef: "file:README.md"}
	if err := database.SaveLink(ctx, current.ID, link); err != nil {
		t.Fatal(err)
	}
	value := material.Material{ID: "readme", URI: "file:README.md", MediaType: "text/markdown", Processor: "markdown/v1", Hash: "abc", Size: 10}
	targetID := graph.ReferenceID(graph.KindMaterial, value.URI)
	node := graph.Node{ID: targetID, Label: "README.md", Kind: graph.KindMaterial, Ref: value.URI, MaterialID: "readme", MaterialURI: value.URI}
	if err := database.ReplaceKnowledge(ctx, current.ID, []material.Material{value}, []graph.Node{node}, nil, nil); err != nil {
		t.Fatal(err)
	}
	if err := database.ReplaceKnowledge(ctx, current.ID, nil, nil, nil, nil); err != nil {
		t.Fatal(err)
	}
	graphNodes, graphEdges, err := database.Graph(ctx, current.ID)
	if err != nil || len(graphEdges) != 1 || graphEdges[0].Owner != graph.OwnerDurable {
		t.Fatalf("intent edge was lost after update: %#v, %v", graphEdges, err)
	}
	if len(graphNodes) != 2 || graphNodes[1].ID != targetID || graphNodes[1].State != graph.StateMissing {
		t.Fatalf("missing target was not retained: %#v", graphNodes)
	}
	if err := database.ReplaceKnowledge(ctx, current.ID, []material.Material{value}, []graph.Node{node}, nil, nil); err != nil {
		t.Fatal(err)
	}
	graphNodes, graphEdges, err = database.Graph(ctx, current.ID)
	if err != nil || len(graphEdges) != 1 || graphEdges[0].TargetID != targetID || graphNodes[1].State != graph.StateActive {
		t.Fatalf("intent edge did not reconnect: %#v %#v, %v", graphNodes, graphEdges, err)
	}
}

func TestMemoryRoundTrip(t *testing.T) {
	ctx := context.Background()
	store, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	if err := store.SaveProject(ctx, project.Project{ID: "demo", Name: "Demo", Root: "/demo"}); err != nil {
		t.Fatal(err)
	}

	content := "Use SQLite"
	want, err := memory.New("demo", "decision.database", memory.Decision, &content, nil)
	if err != nil {
		t.Fatal(err)
	}
	created, err := store.SaveMemory(ctx, want)
	if err != nil {
		t.Fatal(err)
	}
	if created.Action != "created" || created.VersionID == 0 {
		t.Fatalf("unexpected create result: %#v", created)
	}
	unchanged, err := store.SaveMemory(ctx, want)
	if err != nil {
		t.Fatal(err)
	}
	if unchanged.Action != "unchanged" {
		t.Fatalf("unexpected unchanged result: %#v", unchanged)
	}
	got, err := store.Memory(ctx, want.ProjectID, want.Key)
	if err != nil {
		t.Fatal(err)
	}
	if got.Hash != want.Hash || got.Value == nil || *got.Value != content {
		t.Fatalf("memory mismatch: %#v", got)
	}
	graphNodes, _, err := store.Graph(ctx, want.ProjectID)
	if err != nil || len(graphNodes) != 1 || graphNodes[0].ID != graph.ReferenceID(graph.KindIntent, want.Key) || graphNodes[0].Owner != graph.OwnerDurable {
		t.Fatalf("memory graph node mismatch: %#v, %v", graphNodes, err)
	}
	versions, err := store.MemoryVersions(ctx, want.ProjectID, want.Key)
	if err != nil || len(versions) != 1 || versions[0].ID != created.VersionID {
		t.Fatalf("versions mismatch: %#v, %v", versions, err)
	}
	found, err := store.SearchMemories(ctx, want.ProjectID, "SQLite", 10)
	if err != nil || len(found) != 1 || found[0].Key != want.Key {
		t.Fatalf("search mismatch: %#v, %v", found, err)
	}
	if deleted, err := store.DeleteMemory(ctx, want.ProjectID, want.Key); err != nil || !deleted {
		t.Fatalf("delete memory = %v, %v", deleted, err)
	}
	graphNodes, _, err = store.Graph(ctx, want.ProjectID)
	if err != nil || len(graphNodes) != 0 {
		t.Fatalf("deleted memory remained in graph: %#v, %v", graphNodes, err)
	}
}

func TestReconcileMemoriesIsAtomicAndAudited(t *testing.T) {
	ctx := context.Background()
	database, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = database.Close() })
	current := project.Project{ID: "demo", Name: "Demo", Root: "/demo"}
	if err := database.SaveProject(ctx, current); err != nil {
		t.Fatal(err)
	}
	resource := project.Resource{ID: "resource", Provider: "folder", Label: "Demo", Identity: "/demo", Views: []project.View{{ID: "view", Root: "/demo", Available: true}}}
	if err := database.SaveWorkspace(ctx, current.ID, []project.Resource{resource}); err != nil {
		t.Fatal(err)
	}
	if err := database.SaveSession(ctx, current.ID, "view", "codex:one", "codex", "ended"); err != nil {
		t.Fatal(err)
	}
	value := "Keep intent visible."
	want, err := memory.New(current.ID, "intent.ui", memory.Decision, &value, nil)
	if err != nil {
		t.Fatal(err)
	}
	link := graph.Link{SourceKind: "intent", SourceRef: want.Key, Relation: graph.RelationRealizedBy, TargetKind: "material", TargetRef: "file:frontend.tsx"}
	results, err := database.ReconcileMemories(ctx, "codex:one", []MemoryProposal{{Memory: want, EvidenceIDs: []string{"U000001"}, Links: []graph.Link{link}}})
	if err != nil || len(results) != 1 || results[0].Action != "created" {
		t.Fatalf("unexpected reconciliation: %#v %v", results, err)
	}
	wrong := "wrong"
	changed := "Changed concurrently."
	conflict, _ := memory.New(current.ID, "intent.ui", memory.Decision, &changed, nil)
	if _, err := database.ReconcileMemories(ctx, "codex:one", []MemoryProposal{{Memory: conflict, ExpectedHash: &wrong}}); !errors.Is(err, ErrMemoryConflict) {
		t.Fatalf("expected conflict, got %v", err)
	}
	got, err := database.Memory(ctx, current.ID, want.Key)
	if err != nil || got.Hash != want.Hash {
		t.Fatalf("conflict changed memory: %#v %v", got, err)
	}
	graphNodes, graphEdges, err := database.Graph(ctx, current.ID)
	if err != nil || len(graphNodes) != 2 || len(graphEdges) != 1 || graphEdges[0].Owner != graph.OwnerDurable || graphEdges[0].Provenance != "reconcile:codex:one" {
		t.Fatalf("reconciliation graph link missing: %#v %#v %v", graphNodes, graphEdges, err)
	}
	var audit string
	if err := database.db.QueryRowContext(ctx, "SELECT changes_json FROM reconciliation_events WHERE project_id = ? AND session_id = ?", current.ID, "codex:one").Scan(&audit); err != nil || !strings.Contains(audit, `"relation":"realized_by"`) || !strings.Contains(audit, `"evidenceIds":["U000001"]`) {
		t.Fatalf("reconciliation provenance missing: %q %v", audit, err)
	}
}

func TestWorkspaceRoundTrip(t *testing.T) {
	ctx := context.Background()
	store, err := Open(ctx, filepath.Join(t.TempDir(), "context.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	current := project.Project{ID: "demo", Name: "Demo", Root: "/demo"}
	if err := store.SaveProject(ctx, current); err != nil {
		t.Fatal(err)
	}
	resource := project.Resource{ID: "resource", Provider: "git", Label: "Demo", Identity: "/git"}
	view := project.View{ID: "view", Root: "/demo", Branch: "main", Revision: "abc"}
	resource.Views = []project.View{view}
	if err := store.SaveWorkspace(ctx, current.ID, []project.Resource{resource}); err != nil {
		t.Fatal(err)
	}
	if err := store.SaveSession(ctx, current.ID, view.ID, "session", "codex", "active"); err != nil {
		t.Fatal(err)
	}
	if err := store.SaveSession(ctx, current.ID, "", "unmapped", "claude", "ended"); err != nil {
		t.Fatal(err)
	}
	workspace, err := store.Workspace(ctx, current)
	if err != nil {
		t.Fatal(err)
	}
	if len(workspace.Resources) != 1 || len(workspace.Resources[0].Views) != 1 || len(workspace.Resources[0].Views[0].Sessions) != 1 || len(workspace.UnmappedSessions) != 1 {
		t.Fatalf("unexpected workspace: %#v", workspace)
	}
}
