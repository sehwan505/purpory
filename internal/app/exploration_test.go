package app

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/store"
)

func TestAgentExplorationUsesOneShotBaselineRollback(t *testing.T) {
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	original := "Original project knowledge."
	shared, err := memory.New("demo", "policy.shared", memory.Decision, &original, nil)
	if err != nil {
		t.Fatal(err)
	}
	originalBridge := "Original connected knowledge."
	bridge, err := memory.New("demo", "dependency.bridge", memory.Decision, &originalBridge, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.store.ReconcileMemories(ctx, "codex:reconcile", []store.MemoryProposal{{Memory: shared}, {Memory: bridge}}); err != nil {
		t.Fatal(err)
	}
	if err := service.store.SaveLink(ctx, "demo", graph.Link{SourceKind: graph.KindIntent, SourceRef: "policy.shared", Relation: graph.RelationDependsOn, TargetKind: graph.KindIntent, TargetRef: "dependency.bridge"}); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentMemory(ctx, "codex:writer", memory.Decision, "policy.shared", "Denied.", ""); err == nil {
		t.Fatal("agent memory write succeeded while exploration mode was off")
	}
	if _, err := service.SetExplorationMode(ctx, "codex:writer", true); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentMemory(ctx, "codex:writer", memory.Decision, "policy.shared", "Agent managed value.", "verified in project materials"); err != nil {
		t.Fatal(err)
	}
	unchanged, err := service.SetAgentMemory(ctx, "codex:writer", memory.Decision, "policy.shared", "Agent managed value.", "verified in project materials")
	if err != nil || unchanged.ID != 0 || unchanged.Action != "unchanged" {
		t.Fatalf("unchanged write was not reported clearly: %#v %v", unchanged, err)
	}
	if _, err := service.SetAgentMemory(ctx, "codex:writer", memory.Note, "knowledge.transient", "Connected knowledge.", "discovered while exploring"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentLink(ctx, "codex:writer", "policy.shared", "supports", "knowledge.transient", "graph improvement"); err != nil {
		t.Fatal(err)
	}
	unchanged, err = service.SetAgentLink(ctx, "codex:writer", "policy.shared", "supports", "knowledge.transient", "graph improvement")
	if err != nil || unchanged.ID != 0 || unchanged.Action != "unchanged" {
		t.Fatalf("unchanged link was not reported clearly: %#v %v", unchanged, err)
	}
	if _, err := service.DeleteAgentLink(ctx, "codex:writer", "policy.shared", "supports", "knowledge.transient"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentLink(ctx, "codex:writer", "policy.shared", "supports", "knowledge.transient", "restored after review"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.DeleteAgentMemory(ctx, "codex:writer", "dependency.bridge"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentMemory(ctx, "codex:writer", memory.Decision, "dependency.bridge", "Recreated knowledge.", "confirmed"); err != nil {
		t.Fatal(err)
	}

	current, err := service.Memory(ctx, "policy.shared")
	if err != nil || current.Value == nil || *current.Value != "Agent managed value." {
		t.Fatalf("agent did not update canonical knowledge: %#v %v", current, err)
	}
	t.Setenv("PURPORY_SESSION", "codex:reader")
	if _, err := service.Path(ctx, "policy.shared", "dependency.bridge"); err == nil {
		t.Fatal("deleting knowledge did not remove its canonical edge")
	}
	if _, err := service.SetAgentMemory(ctx, "codex:reader", memory.Note, "knowledge.denied", "Denied.", ""); err == nil {
		t.Fatal("another session wrote without enabling exploration mode")
	}
	status, err := service.Exploration(ctx, "codex:writer")
	if err != nil || !status.Enabled || status.PendingChanges < 7 {
		t.Fatalf("agent undo log missing: %#v %v", status, err)
	}

	rolledBack, err := service.RollbackAgentChanges(ctx, "codex:writer")
	if err != nil || rolledBack < 3 {
		t.Fatalf("baseline rollback failed: %d %v", rolledBack, err)
	}
	current, err = service.Memory(ctx, "policy.shared")
	if err != nil || current.Value == nil || *current.Value != original {
		t.Fatalf("baseline knowledge was not restored: %#v %v", current, err)
	}
	if _, err := service.Memory(ctx, "knowledge.transient"); err == nil {
		t.Fatal("knowledge created after baseline survived rollback")
	}
	current, err = service.Memory(ctx, "dependency.bridge")
	if err != nil || current.Value == nil || *current.Value != originalBridge {
		t.Fatalf("deleted baseline knowledge was not restored: %#v %v", current, err)
	}
	if _, err := service.Path(ctx, "policy.shared", "dependency.bridge"); err != nil {
		t.Fatalf("baseline edge was not restored: %v", err)
	}
	if history, err := service.AgentChanges(ctx, 20); err != nil || len(history) != 0 {
		t.Fatalf("rollback did not clear undo log: %#v %v", history, err)
	}
	if _, err := service.RollbackAgentChanges(ctx, "codex:writer"); err == nil {
		t.Fatal("one-shot rollback was unexpectedly reusable")
	}

	if _, err := service.SetAgentMemory(ctx, "codex:writer", memory.Decision, "policy.shared", "Accepted baseline.", "accepted after review"); err != nil {
		t.Fatal(err)
	}
	checkpointed, err := service.CheckpointAgentChanges(ctx, "codex:writer")
	if err != nil || checkpointed != 1 {
		t.Fatalf("baseline checkpoint failed: %d %v", checkpointed, err)
	}
	if _, err := service.RollbackAgentChanges(ctx, "codex:writer"); err == nil {
		t.Fatal("checkpointed change remained rollbackable")
	}
	current, err = service.Memory(ctx, "policy.shared")
	if err != nil || current.Value == nil || *current.Value != "Accepted baseline." {
		t.Fatalf("checkpoint did not retain canonical knowledge: %#v %v", current, err)
	}
	if _, err := service.SetAgentMemory(ctx, "codex:writer", memory.Decision, "policy.shared", "Pending agent value.", "pending"); err != nil {
		t.Fatal(err)
	}
	external := "Newer non-agent value."
	if _, err := service.Remember(ctx, "policy.shared", memory.Decision, &external, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := service.RollbackAgentChanges(ctx, "codex:writer"); err == nil {
		t.Fatal("rollback overwrote a newer non-agent change")
	}
	current, err = service.Memory(ctx, "policy.shared")
	if err != nil || current.Value == nil || *current.Value != external {
		t.Fatalf("rollback conflict damaged current knowledge: %#v %v", current, err)
	}
}
