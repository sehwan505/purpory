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
	link := graph.Link{SourceKind: "intent", SourceRef: entry.Key, Relation: graph.RelationRealizedBy, TargetKind: "material", TargetRef: "file:outcome.md"}
	missing := newContextGraph([]memory.Memory{entry}, nil, nil, []graph.Link{link})
	if len(missing.nodes) != 2 || missing.nodes[1].Kind != "missing" || len(missing.edges) != 1 {
		t.Fatalf("missing evidence was hidden: %#v", missing)
	}
	material := graph.Node{ID: "material", Label: "outcome.md", Kind: "material", MaterialURI: "file:outcome.md"}
	resolved := newContextGraph([]memory.Memory{entry}, []graph.Node{material}, nil, []graph.Link{link})
	if len(resolved.nodes) != 2 || resolved.nodes[1].Kind != "material" || resolved.edges[0].TargetID != material.ID {
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

func TestReconciliationOffersOnlyTranscriptMentionedMaterials(t *testing.T) {
	messages := []reconcile.Message{{Role: "assistant", Text: "Implemented the result in internal/app/service.go."}}
	materials := []material.Material{{URI: "file:README.md"}, {URI: "file:internal/app/service.go"}}
	refs := mentionedMaterialRefs(messages, materials)
	if len(refs) != 1 || refs[0] != "file:internal/app/service.go" {
		t.Fatalf("unexpected reconciliation materials: %#v", refs)
	}
}
