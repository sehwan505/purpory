package app

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
)

func TestAgentExplorationUsesOneShotBaselineRollback(t *testing.T) {
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	original := "Original project knowledge."
	if _, err := service.Remember(ctx, "knowledge.shared", memory.Note, &original, nil); err != nil {
		t.Fatal(err)
	}
	originalBridge := "Original connected knowledge."
	if _, err := service.Remember(ctx, "knowledge.bridge", memory.Note, &originalBridge, nil); err != nil {
		t.Fatal(err)
	}
	if err := service.store.SaveLink(ctx, "demo", graph.Link{SourceKind: graph.KindKnowledge, SourceRef: "knowledge.shared", Relation: "related_to", TargetKind: graph.KindKnowledge, TargetRef: "knowledge.bridge"}); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentKnowledge(ctx, "codex:writer", "knowledge.shared", "Denied.", ""); err == nil {
		t.Fatal("agent knowledge write succeeded while exploration mode was off")
	}
	if _, err := service.SetExplorationMode(ctx, "codex:writer", true); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentKnowledge(ctx, "codex:writer", "knowledge.shared", "Agent managed value.", "verified in project materials"); err != nil {
		t.Fatal(err)
	}
	unchanged, err := service.SetAgentKnowledge(ctx, "codex:writer", "knowledge.shared", "Agent managed value.", "verified in project materials")
	if err != nil || unchanged.ID != 0 || unchanged.Action != "unchanged" {
		t.Fatalf("unchanged write was not reported clearly: %#v %v", unchanged, err)
	}
	if _, err := service.SetAgentKnowledge(ctx, "codex:writer", "knowledge.transient", "Connected knowledge.", "discovered while exploring"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentLink(ctx, "codex:writer", "knowledge.shared", "supports", "knowledge.transient", "graph improvement"); err != nil {
		t.Fatal(err)
	}
	unchanged, err = service.SetAgentLink(ctx, "codex:writer", "knowledge.shared", "supports", "knowledge.transient", "graph improvement")
	if err != nil || unchanged.ID != 0 || unchanged.Action != "unchanged" {
		t.Fatalf("unchanged link was not reported clearly: %#v %v", unchanged, err)
	}
	if _, err := service.DeleteAgentLink(ctx, "codex:writer", "knowledge.shared", "supports", "knowledge.transient"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentLink(ctx, "codex:writer", "knowledge.shared", "supports", "knowledge.transient", "restored after review"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.DeleteAgentKnowledge(ctx, "codex:writer", "knowledge.bridge"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.SetAgentKnowledge(ctx, "codex:writer", "knowledge.bridge", "Recreated knowledge.", "confirmed"); err != nil {
		t.Fatal(err)
	}

	current, err := service.Memory(ctx, "knowledge.shared")
	if err != nil || current.Value == nil || *current.Value != "Agent managed value." {
		t.Fatalf("agent did not update canonical knowledge: %#v %v", current, err)
	}
	t.Setenv("PURPORY_SESSION", "codex:reader")
	if _, err := service.Path(ctx, "knowledge.shared", "knowledge.bridge"); err == nil {
		t.Fatal("deleting knowledge did not remove its canonical edge")
	}
	if _, err := service.SetAgentKnowledge(ctx, "codex:reader", "knowledge.denied", "Denied.", ""); err == nil {
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
	current, err = service.Memory(ctx, "knowledge.shared")
	if err != nil || current.Value == nil || *current.Value != original {
		t.Fatalf("baseline knowledge was not restored: %#v %v", current, err)
	}
	if _, err := service.Memory(ctx, "knowledge.transient"); err == nil {
		t.Fatal("knowledge created after baseline survived rollback")
	}
	current, err = service.Memory(ctx, "knowledge.bridge")
	if err != nil || current.Value == nil || *current.Value != originalBridge {
		t.Fatalf("deleted baseline knowledge was not restored: %#v %v", current, err)
	}
	if _, err := service.Path(ctx, "knowledge.shared", "knowledge.bridge"); err != nil {
		t.Fatalf("baseline edge was not restored: %v", err)
	}
	if history, err := service.AgentChanges(ctx, 20); err != nil || len(history) != 0 {
		t.Fatalf("rollback did not clear undo log: %#v %v", history, err)
	}
	if _, err := service.RollbackAgentChanges(ctx, "codex:writer"); err == nil {
		t.Fatal("one-shot rollback was unexpectedly reusable")
	}

	if _, err := service.SetAgentKnowledge(ctx, "codex:writer", "knowledge.shared", "Accepted baseline.", "accepted after review"); err != nil {
		t.Fatal(err)
	}
	checkpointed, err := service.CheckpointAgentChanges(ctx, "codex:writer")
	if err != nil || checkpointed != 1 {
		t.Fatalf("baseline checkpoint failed: %d %v", checkpointed, err)
	}
	if _, err := service.RollbackAgentChanges(ctx, "codex:writer"); err == nil {
		t.Fatal("checkpointed change remained rollbackable")
	}
	current, err = service.Memory(ctx, "knowledge.shared")
	if err != nil || current.Value == nil || *current.Value != "Accepted baseline." {
		t.Fatalf("checkpoint did not retain canonical knowledge: %#v %v", current, err)
	}
	if _, err := service.SetAgentKnowledge(ctx, "codex:writer", "knowledge.shared", "Pending agent value.", "pending"); err != nil {
		t.Fatal(err)
	}
	external := "Newer non-agent value."
	if _, err := service.Remember(ctx, "knowledge.shared", memory.Note, &external, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := service.RollbackAgentChanges(ctx, "codex:writer"); err == nil {
		t.Fatal("rollback overwrote a newer non-agent change")
	}
	current, err = service.Memory(ctx, "knowledge.shared")
	if err != nil || current.Value == nil || *current.Value != external {
		t.Fatalf("rollback conflict damaged current knowledge: %#v %v", current, err)
	}
}
