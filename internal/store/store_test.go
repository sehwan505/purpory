package store

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/material"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/project"
)

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

func TestMigrationFollowsLegacyVersions(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "context.db")
	database, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	legacyProject := project.Project{ID: "legacy", Name: "Legacy", Root: "/legacy"}
	legacyResource := project.Resource{ID: "repo", Provider: "git", Label: "Repo", Identity: "/legacy/.git", Views: []project.View{{ID: "view", Root: "/legacy", Available: true}}}
	if err := database.SaveProject(ctx, legacyProject); err != nil {
		t.Fatal(err)
	}
	if err := database.SaveWorkspace(ctx, legacyProject.ID, []project.Resource{legacyResource}); err != nil {
		t.Fatal(err)
	}
	if _, err := database.db.ExecContext(ctx, `UPDATE projects SET embedding_model = 'legacy-embed' WHERE id = ?`, legacyProject.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := database.db.ExecContext(ctx, `
		DROP TABLE view_observations;
		DROP TABLE resource_observations;
		DELETE FROM settings WHERE key = 'model.embedding';
		DELETE FROM schema_migrations WHERE version IN (17, 18, 19);
	`); err != nil {
		t.Fatal(err)
	}
	for version := 2; version <= 16; version++ {
		if _, err := database.db.ExecContext(ctx, `INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)`, version); err != nil {
			t.Fatal(err)
		}
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	database, err = Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	var tables int
	if err := database.db.QueryRowContext(ctx, `
		SELECT count(*) FROM sqlite_master
		WHERE type = 'table' AND name IN ('resource_observations', 'view_observations')
	`).Scan(&tables); err != nil || tables != 2 {
		t.Fatalf("migration 17 was not applied after legacy versions: %d %v", tables, err)
	}
	observations, err := database.Observations(ctx)
	if err != nil || len(observations) != 1 || observations[0].Resource.ID != legacyResource.ID {
		t.Fatalf("legacy Resource was not backfilled: %#v %v", observations, err)
	}
	model, found, err := database.Setting(ctx, "model.embedding")
	if err != nil || !found || model != "legacy-embed" {
		t.Fatalf("legacy embedding setting was not migrated: %q %v %v", model, found, err)
	}
}

func TestMigrateDropsLegacyDeliveryJSON(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "legacy_delivery.db")
	database, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if err := database.SaveProject(ctx, project.Project{ID: "legacy", Name: "Legacy", Root: "/legacy"}); err != nil {
		t.Fatal(err)
	}
	if _, err := database.db.ExecContext(ctx, `
		ALTER TABLE context_decisions ADD COLUMN delivery_json TEXT NOT NULL DEFAULT '{}';
		INSERT INTO context_decisions(project_id, session_id, input_hash, proposal_json, final_action, prompt_version)
		VALUES ('legacy', 'session', 'hash', '{}', 'retrieve', 'legacy');
		INSERT INTO gate_feedback(decision_id, verdict) VALUES (last_insert_rowid(), 'correct');
		DELETE FROM schema_migrations WHERE version = 20;
	`); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	database, err = Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	var count int
	if err := database.db.QueryRowContext(ctx, "SELECT count(*) FROM pragma_table_info('context_decisions') WHERE name = 'delivery_json'").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("expected delivery_json column to be dropped, got count %d", count)
	}
	if err := database.db.QueryRowContext(ctx, `
		SELECT count(*) FROM context_decisions d
		JOIN gate_feedback f ON f.decision_id = d.id
	`).Scan(&count); err != nil || count != 1 {
		t.Fatalf("expected decision feedback to be preserved, got %d: %v", count, err)
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
	observed.Project.Root = second.Root
	if got, err := database.ProjectForWorkspace(ctx, observed, ""); err != nil || got.ID != second.ID {
		t.Fatalf("working directory did not resolve shared resource: %#v, %v", got, err)
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

func TestObservedResourceCanBelongToMultipleProjects(t *testing.T) {
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
	resource := project.Resource{ID: "repo", Provider: "git", Label: "Repo", Identity: "/repo/.git", Views: []project.View{{ID: "view", Root: "/repo", Available: true}}}
	if err := database.SaveObservations(ctx, []project.Resource{resource}); err != nil {
		t.Fatal(err)
	}
	for _, projectID := range []string{"one", "one", "two"} {
		if err := database.AssignObservation(ctx, projectID, resource.ID); err != nil {
			t.Fatal(err)
		}
	}
	observations, err := database.Observations(ctx)
	if err != nil || len(observations) != 1 || len(observations[0].ProjectIDs) != 2 {
		t.Fatalf("unexpected observations: %#v %v", observations, err)
	}
	for _, current := range []project.Project{{ID: "one", Name: "One", Root: "/one"}, {ID: "two", Name: "Two", Root: "/two"}} {
		workspace, err := database.Workspace(ctx, current)
		if err != nil || len(workspace.Resources) != 1 || workspace.Resources[0].ID != resource.ID {
			t.Fatalf("resource not assigned to %s: %#v %v", current.ID, workspace, err)
		}
	}
	if err := database.SaveSession(ctx, "one", "view", "session", "codex", "ended"); err != nil {
		t.Fatal(err)
	}
	removed, err := database.UnassignResource(ctx, "one", resource.ID)
	if err != nil || !removed {
		t.Fatalf("resource was not unassigned: %v %v", removed, err)
	}
	one, err := database.Workspace(ctx, project.Project{ID: "one", Name: "One", Root: "/one"})
	if err != nil || len(one.Resources) != 0 || len(one.UnmappedSessions) != 1 {
		t.Fatalf("unassign did not preserve the session: %#v %v", one, err)
	}
	observations, err = database.Observations(ctx)
	if err != nil || len(observations[0].ProjectIDs) != 1 || observations[0].ProjectIDs[0] != "two" {
		t.Fatalf("unassign changed the wrong Project: %#v %v", observations, err)
	}
}
