package app

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
	"github.com/sehwan505/purpory/internal/project"
	"github.com/sehwan505/purpory/internal/store"
)

type fixedObserver struct{ workspace project.Workspace }

func (f fixedObserver) Observe(context.Context, string) (project.Workspace, error) {
	return f.workspace, nil
}

type fixedGate struct{ proposal contextprepare.Proposal }

func (f fixedGate) Propose(context.Context, contextprepare.Request) (contextprepare.ProviderResult, error) {
	return contextprepare.ProviderResult{Proposal: f.proposal, ModelID: "stub/qwen", Revision: "test", LatencyMS: 7}, nil
}

func openTestService(t *testing.T, root, database, id string) *Service {
	t.Helper()
	if _, err := RegisterProject(context.Background(), root, database, id, ""); err != nil {
		t.Fatal(err)
	}
	service, err := Open(context.Background(), root, database, id)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = service.Close() })
	return service
}

func TestOpenRequiresExplicitProjectRegistration(t *testing.T) {
	root := t.TempDir()
	database := filepath.Join(t.TempDir(), "purpory.db")
	if _, err := Open(context.Background(), root, database, ""); !errors.Is(err, project.ErrNotRegistered) {
		t.Fatalf("Open error = %v, want project not registered", err)
	}
	stored, err := store.Open(context.Background(), database)
	if err != nil {
		t.Fatal(err)
	}
	defer stored.Close()
	projects, err := stored.Projects(context.Background())
	if err != nil || len(projects) != 0 {
		t.Fatalf("implicit projects were stored: %#v, %v", projects, err)
	}
}

func TestOpenResolvesRegisteredProjectFromChildDirectory(t *testing.T) {
	root := t.TempDir()
	child := filepath.Join(root, "notes")
	if err := os.Mkdir(child, 0o755); err != nil {
		t.Fatal(err)
	}
	database := filepath.Join(t.TempDir(), "purpory.db")
	if _, err := RegisterProject(context.Background(), root, database, "demo", "Demo"); err != nil {
		t.Fatal(err)
	}
	service, err := Open(context.Background(), child, database, "")
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	resolvedChild, err := filepath.EvalSymlinks(child)
	if err != nil {
		t.Fatal(err)
	}
	if status := service.Status(); status.Project.ID != "demo" || status.Project.Root != resolvedChild {
		t.Fatalf("unexpected active project: %#v", status.Project)
	}
}

func TestSelectProjectSwitchesWorkspaceWithoutMixingProjectData(t *testing.T) {
	ctx := context.Background()
	firstRoot := t.TempDir()
	secondRoot := t.TempDir()
	database := filepath.Join(t.TempDir(), "purpory.db")
	if _, err := RegisterProject(ctx, firstRoot, database, "first", "First"); err != nil {
		t.Fatal(err)
	}
	if _, err := RegisterProject(ctx, secondRoot, database, "second", "Second"); err != nil {
		t.Fatal(err)
	}
	service, err := Open(ctx, firstRoot, database, "first")
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	value := "first project only"
	if _, err := service.Remember(ctx, "intent.first", memory.Decision, &value, nil); err != nil {
		t.Fatal(err)
	}
	projects, err := service.Projects(ctx)
	if err != nil || len(projects) != 2 {
		t.Fatalf("registered projects missing: %#v %v", projects, err)
	}
	resolvedSecond, err := filepath.EvalSymlinks(secondRoot)
	if err != nil {
		t.Fatal(err)
	}
	status, err := service.SelectProject(ctx, "second")
	if err != nil || status.Project.ID != "second" || status.Project.Root != resolvedSecond {
		t.Fatalf("project did not switch: %#v %v", status, err)
	}
	workspace, err := service.Workspace(ctx)
	if err != nil || workspace.Project.ID != "second" {
		t.Fatalf("workspace did not follow selection: %#v %v", workspace, err)
	}
	memories, err := service.Memories(ctx, "")
	if err != nil || len(memories) != 0 {
		t.Fatalf("project memory leaked across selection: %#v %v", memories, err)
	}
}

func TestWorkspaceObserverKeepsCoreDomainNeutral(t *testing.T) {
	root := t.TempDir()
	want := project.Workspace{
		Project: project.Project{ID: "studio", Name: "Studio", Root: root},
		Resources: []project.Resource{{
			ID: "camera", Provider: "media", Label: "Camera", Identity: "camera:one",
			Views: []project.View{{ID: "shoot", Root: root, Available: true}},
		}},
	}
	database := filepath.Join(t.TempDir(), "purpory.db")
	if _, err := registerProject(context.Background(), root, database, "", "", fixedObserver{want}); err != nil {
		t.Fatal(err)
	}
	service, err := OpenWithObserver(context.Background(), root, database, "", fixedObserver{want})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = service.Close() })
	got, err := service.Workspace(context.Background())
	if err != nil || len(got.Resources) != 1 || got.Resources[0].Provider != "media" || got.Resources[0].Views[0].ID != "shoot" {
		t.Fatalf("domain observer was not preserved: %#v, %v", got, err)
	}
}

func TestPrepareReturnsAndAuditsHintMap(t *testing.T) {
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "context.db"), "demo")
	value := "PostgreSQL is the transactional source of truth."
	if _, err := service.Remember(context.Background(), "decision.database", memory.Decision, &value, nil); err != nil {
		t.Fatal(err)
	}
	query := "database decision"
	service.gate = fixedGate{contextprepare.Proposal{Action: "search", Query: &query, Keywords: []string{"PostgreSQL"}, ReasonCode: "PRIOR_DECISION_REFERENCED"}}
	request := contextprepare.Request{Message: "Which database did we choose?", SessionID: "codex:one", WorkingDirectory: root, TokenBudget: 512, RetainInput: true}

	result, err := service.PrepareContext(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if result.Action != "retrieve" || result.SchemaVersion != 1 || result.Hints == nil || len(result.Hints.Nodes) != 1 || result.Hints.Nodes[0].Path != "decision.database" {
		t.Fatalf("unexpected preparation: %#v", result)
	}
	if rendered := contextprepare.RenderHintMap(result.Hints); strings.Contains(rendered, value) || !strings.Contains(rendered, "purpory explain") {
		t.Fatalf("hint leaked content or was not navigable: %q", rendered)
	}
	decisions, err := service.store.PrepareDecisions(context.Background(), "demo", 10)
	if err != nil || len(decisions) != 1 || decisions[0].InputText == nil || *decisions[0].InputText != request.Message || decisions[0].Hints == nil {
		t.Fatalf("decision audit missing: %#v %v", decisions, err)
	}
}

func TestPrepareDeduplicatesAsk(t *testing.T) {
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "context.db"), "demo")
	missing := "missing deployment policy"
	service.gate = fixedGate{contextprepare.Proposal{Action: "search", Query: &missing, ReasonCode: "PROJECT_CONTEXT_REQUIRED"}}
	request := contextprepare.Request{Message: "How do we deploy?", SessionID: "agent", WorkingDirectory: root, TokenBudget: 512}
	first, err := service.PrepareContext(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	second, err := service.PrepareContext(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if first.Action != "ask" || first.RequestID == nil || second.RequestID == nil || *first.RequestID != *second.RequestID || first.Clarification == nil {
		t.Fatalf("missing context request was not deduplicated: %#v %#v", first, second)
	}
}

func TestPrepareHintMapBudgetsSemanticBM25AndPaths(t *testing.T) {
	nodes := []graph.Node{
		{ID: "intent:semantic", Label: "game.lol.play-rule", Kind: graph.KindIntent, Subkind: "decision", Ref: "game.lol.play-rule", Owner: graph.OwnerDurable, State: graph.StateActive},
		{ID: "knowledge:lexical", Label: "game.lol.items", Kind: graph.KindKnowledge, Subkind: "note", Ref: "game.lol.items", Owner: graph.OwnerDurable, State: graph.StateActive},
		{ID: "knowledge:alternate", Label: "product.discovery", Kind: graph.KindKnowledge, Subkind: "note", Ref: "product.discovery", Owner: graph.OwnerDurable, State: graph.StateActive},
		{ID: "material:file:guide.md", Label: "guide.md", Kind: graph.KindMaterial, State: graph.StateActive, MaterialURI: "file:guide.md"},
	}
	edges := []graph.Edge{{SourceID: "intent:semantic", TargetID: "knowledge:lexical", Relation: graph.RelationRealizedBy}}
	hints := prepareHintMap(
		[]contextprepare.Candidate{{NodeID: "intent:semantic"}, {NodeID: "knowledge:alternate"}},
		[]contextprepare.Candidate{{NodeID: "knowledge:lexical"}},
		nodes, edges, nil, 512,
	)
	if hints == nil || len(hints.Nodes) != 3 || hints.Nodes[0].Match != "semantic" || hints.Nodes[1].Match != "bm25" || hints.Nodes[2].Match != "semantic:alternate-branch" || hints.Nodes[0].Path != "game.lol.play-rule" || len(hints.Edges) != 1 || contextprepare.EstimateTokens(contextprepare.RenderHintMap(hints)) > 512 {
		t.Fatalf("unexpected hint map: %#v", hints)
	}
}

func TestPrepareDoesNotForceActivePathEvidence(t *testing.T) {
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "context.db"), "demo")
	source := "@repo/src/auth"
	if _, err := service.Remember(context.Background(), "intent.auth-review", memory.Decision, nil, &source); err != nil {
		t.Fatal(err)
	}
	query := "unrelated"
	service.gate = fixedGate{contextprepare.Proposal{Action: "search", Query: &query, ReasonCode: "PROJECT_CONTEXT_REQUIRED"}}
	result, err := service.PrepareContext(context.Background(), contextprepare.Request{
		Message: query, SessionID: "agent", WorkingDirectory: root,
		ActivePaths: []string{filepath.Join(root, "src", "auth", "service.go")}, TokenBudget: 512,
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Action != "ask" || result.Hints != nil {
		t.Fatalf("active path forced invalid evidence: %#v", result)
	}
}

func TestIntentGraphLinksMaterialEvidence(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "release.md"), []byte("# Release\nShip from tagged builds.\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service := openTestService(t, root, filepath.Join(t.TempDir(), "context.db"), "demo")
	if _, err := service.Update(context.Background()); err != nil {
		t.Fatal(err)
	}
	value := "Release artifacts must come from tagged builds."
	if _, err := service.Remember(context.Background(), "intent.release", memory.Decision, &value, nil); err != nil {
		t.Fatal(err)
	}
	link := graph.Link{SourceKind: "intent", SourceRef: "intent.release", Relation: graph.RelationRealizedBy, TargetKind: "material", TargetRef: "file:release.md"}
	if err := service.store.SaveLink(context.Background(), "demo", link); err != nil {
		t.Fatal(err)
	}

	contextGraph, err := service.Graph(context.Background(), "intent.release", 20)
	if err != nil || len(contextGraph.Nodes) < 2 || len(contextGraph.Edges) == 0 || contextGraph.Nodes[0].Kind != "intent" {
		t.Fatalf("intent graph missing: %#v %v", contextGraph, err)
	}
	explanation, err := service.Explain(context.Background(), "intent.release")
	if err != nil || explanation.Memory == nil || explanation.Graph == nil || len(explanation.Graph.Connections) != 1 {
		t.Fatalf("intent explanation missing evidence: %#v %v", explanation, err)
	}
	path, err := service.Path(context.Background(), "intent.release", "file:release.md")
	if err != nil || len(path.Nodes) != 2 || len(path.Edges) != 1 {
		t.Fatalf("intent path missing: %#v %v", path, err)
	}

}

func TestUpdateDiscoversMaterialsIncrementally(t *testing.T) {
	root := t.TempDir()
	readme := filepath.Join(root, "README.md")
	java := filepath.Join(root, "Demo.java")
	if err := os.WriteFile(readme, []byte("# Purpose\nProject context for everyone.\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(java, []byte("class Demo {}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service := openTestService(t, root, filepath.Join(t.TempDir(), "context.db"), "demo")

	first, err := service.Update(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if first.MaterialCount != 2 || first.Processed != 2 || first.Changes.Added != 2 {
		t.Fatalf("unexpected first update: %#v", first)
	}
	second, err := service.Update(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if second.Processed != 0 || second.Changes.Unchanged != 2 || second.EntityCount != first.EntityCount {
		t.Fatalf("unexpected unchanged update: %#v", second)
	}
	query, err := service.Query(context.Background(), "Project context for everyone", 10)
	foundSection := false
	for _, node := range query.Nodes {
		foundSection = foundSection || node.Kind == graph.KindKnowledge && node.Subkind == "section" && node.Label == "Purpose" && strings.Contains(node.Content, "Project context for everyone")
	}
	if err != nil || !foundSection {
		t.Fatalf("document context missing: %#v, %v", query, err)
	}
	contextGraph, err := service.Graph(context.Background(), "", 20)
	if err != nil || len(contextGraph.Nodes) == 0 || len(contextGraph.Edges) == 0 {
		t.Fatalf("context graph missing: %#v, %v", contextGraph, err)
	}
	if err := os.WriteFile(readme, []byte("# Intent\nDomain-neutral project context.\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(java); err != nil {
		t.Fatal(err)
	}
	third, err := service.Update(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if third.Changes.Modified != 1 || third.Changes.Removed != 1 || third.Processed != 1 {
		t.Fatalf("unexpected changed update: %#v", third)
	}
}
