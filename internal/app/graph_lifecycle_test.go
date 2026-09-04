package app

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/reconcile"
)

func TestContinuousGraphLifecycleEndToEnd(t *testing.T) {
	t.Setenv("PURPORY_SESSION", "codex:lifecycle")
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	targetValue := "Offline support is available."
	if _, err := service.Remember(ctx, "platform.offline", memory.Decision, &targetValue, nil); err != nil {
		t.Fatal(err)
	}
	value := "Mobile release depends on offline support."
	evidence := []memory.EvidenceRef{{MessageID: "U000001", PartID: "U000001P001", Quote: value, EndByte: len(value)}}
	link := reconcile.IntentLink{Relation: graph.RelationDependsOn, TargetRef: "platform.offline", EvidenceRefs: evidence}
	candidate := reconcile.Candidate{Key: "release.mobile", Kind: memory.Decision, Value: value, EvidenceIDs: []string{"U000001"}, EvidenceRefs: evidence, IntentLinks: []reconcile.IntentLink{link}}
	if err := service.applyCandidates(ctx, "codex:lifecycle", []reconcile.Candidate{candidate}); err != nil {
		t.Fatal(err)
	}
	if _, err := service.Query(ctx, "release.mobile", 5); err != nil {
		t.Fatal(err)
	}
	if _, err := service.Explain(ctx, "release.mobile"); err != nil {
		t.Fatal(err)
	}
	if path, err := service.Path(ctx, "release.mobile", "platform.offline"); err != nil || len(path.Edges) != 1 {
		t.Fatalf("active link was not traversable: %#v %v", path, err)
	}
	trail, err := service.store.Navigation(ctx, "demo", "codex:lifecycle", 8)
	if err != nil || len(trail) < 3 || trail[0].Action != "path" {
		t.Fatalf("ordered exploration was not retained: %#v %v", trail, err)
	}

	corrected := "Mobile release no longer depends on offline support."
	correctedEvidence := []memory.EvidenceRef{{MessageID: "U000002", PartID: "U000002P001", Quote: corrected, EndByte: len(corrected)}}
	candidate.Value, candidate.EvidenceIDs, candidate.EvidenceRefs = corrected, []string{"U000002"}, correctedEvidence
	candidate.IntentLinks = nil
	candidate.RetiredIntentLinks = []reconcile.IntentLink{{Relation: link.Relation, TargetRef: link.TargetRef, EvidenceRefs: correctedEvidence, ExpectedState: graph.StateActive}}
	if err := service.applyCandidates(ctx, "codex:lifecycle", []reconcile.Candidate{candidate}); err != nil {
		t.Fatal(err)
	}
	if _, err := service.Path(ctx, "release.mobile", "platform.offline"); err == nil {
		t.Fatal("retired link remained traversable")
	}

	candidate.RetiredIntentLinks = nil
	candidate.IntentLinks = []reconcile.IntentLink{{Relation: link.Relation, TargetRef: link.TargetRef, EvidenceRefs: correctedEvidence, ExpectedState: graph.StateRetired}}
	if err := service.applyCandidates(ctx, "codex:lifecycle", []reconcile.Candidate{candidate}); err != nil {
		t.Fatal(err)
	}
	if path, err := service.Path(ctx, "release.mobile", "platform.offline"); err != nil || len(path.Edges) != 1 {
		t.Fatalf("reactivated link was not traversable: %#v %v", path, err)
	}
	events, err := service.ReconciliationEvents(ctx)
	if err != nil || len(events) != 3 || events[0].Links[0].Action != "reactivated" || events[1].Links[0].Action != "retired" {
		t.Fatalf("lifecycle audit was incomplete: %#v %v", events, err)
	}
}
