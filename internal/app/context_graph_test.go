package app

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/material"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/reconcile"
)

func TestContextGraphKeepsAndReconnectsMissingEvidence(t *testing.T) {
	value := "Keep a durable outcome link."
	entry, err := memory.New("demo", "intent.outcome", memory.Decision, &value, nil)
	if err != nil {
		t.Fatal(err)
	}
	intentID := graph.ReferenceID(graph.KindIntent, entry.Key)
	materialID := graph.ReferenceID(graph.KindMaterial, "file:outcome.md")
	nodes := []graph.Node{
		{ID: intentID, Label: entry.Key, Kind: graph.KindIntent, Ref: entry.Key, Owner: graph.OwnerDurable, State: graph.StateActive},
		{ID: materialID, Label: "file:outcome.md", Kind: graph.KindMaterial, Ref: "file:outcome.md", Owner: graph.OwnerDurable, State: graph.StateMissing},
	}
	edges := []graph.Edge{{SourceID: intentID, TargetID: materialID, Relation: graph.RelationRealizedBy, Owner: graph.OwnerDurable}}
	missing := newContextGraph([]memory.Memory{entry}, nodes, edges)
	if len(missing.nodes) != 2 || missing.nodes[1].State != graph.StateMissing || len(missing.edges) != 1 {
		t.Fatalf("missing evidence was hidden: %#v", missing)
	}
	nodes[1].State = graph.StateActive
	nodes[1].Owner = graph.OwnerObserved
	resolved := newContextGraph([]memory.Memory{entry}, nodes, edges)
	if len(resolved.nodes) != 2 || resolved.nodes[1].State != graph.StateActive || resolved.edges[0].TargetID != materialID {
		t.Fatalf("returned evidence did not reconnect: %#v", resolved)
	}
}

func TestContextGraphDoesNotProjectWorkspaceSessions(t *testing.T) {
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "context.db"), "demo")
	if err := service.SaveSession(context.Background(), "codex:workspace-only", "codex", "active"); err != nil {
		t.Fatal(err)
	}
	result, err := service.Graph(context.Background(), "", 100)
	if err != nil {
		t.Fatal(err)
	}
	for _, node := range result.Nodes {
		if node.ID == "codex:workspace-only" || node.Kind == "session" {
			t.Fatalf("workspace session leaked into canonical graph: %#v", node)
		}
	}
}

func TestPrepareNodeCandidateUsesKnowledgeSubkind(t *testing.T) {
	candidate := prepareNodeCandidate(graph.Node{ID: "knowledge:item", Kind: graph.KindKnowledge, Subkind: "function"})
	if candidate.Kind != "function" {
		t.Fatalf("candidate kind = %q", candidate.Kind)
	}
}

func TestTopicPathsExposeBranchesAndConnectRelatedLeaves(t *testing.T) {
	nodes := []graph.Node{
		{ID: "intent:rule", Label: "game.lol.play-rule", Kind: graph.KindIntent, Ref: "game.lol.play-rule", Owner: graph.OwnerDurable, State: graph.StateActive},
		{ID: "knowledge:items", Label: "game.lol.items", Kind: graph.KindKnowledge, Ref: "game.lol.items", Owner: graph.OwnerDurable, State: graph.StateActive},
		{ID: "knowledge:discovery", Label: "product.discovery", Kind: graph.KindKnowledge, Ref: "product.discovery", Owner: graph.OwnerDurable, State: graph.StateActive},
	}
	current := newContextGraph(nil, nodes, nil)
	branches := current.branches("game.lol", 10)
	if len(branches) != 2 || branches[0] != "game.lol.items" || branches[1] != "game.lol.play-rule" {
		t.Fatalf("unexpected topic branches: %#v", branches)
	}
	found, ok := current.find("game.lol.play-rule")
	if !ok || found.ID != "intent:rule" {
		t.Fatalf("topic path did not resolve: %#v", found)
	}
	path, err := current.path("game.lol.play-rule", "game.lol.items")
	if err != nil || len(path.Edges) != 0 || len(path.TopicPaths) != 3 || path.TopicPaths[1] != "game.lol" {
		t.Fatalf("semantic hierarchy did not connect leaves: %#v %v", path, err)
	}
}

func TestReconciliationOffersOnlyTranscriptMentionedMaterials(t *testing.T) {
	messages := []reconcile.Message{{Role: "assistant", Text: "Implemented the result in internal/app/service.go."}}
	materials := []material.Material{{URI: "file:README.md"}, {URI: "file:internal/app/service.go"}}
	refs := mentionedMaterialRefs(messages, materials)
	if len(refs) != 1 || refs[0] != "file:internal/app/service.go" {
		t.Fatalf("unexpected reconciliation materials: %#v", refs)
	}
}
