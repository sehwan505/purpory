package app

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
)

func TestContextAndMemoryLifecycle(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")

	missing := "deployment policy"
	service.gate = fixedGate{contextprepare.Proposal{Action: "search", Query: &missing, ReasonCode: "PROJECT_CONTEXT_REQUIRED"}}
	prepared, err := service.PrepareContext(ctx, contextprepare.Request{Message: missing, SessionID: "test", WorkingDirectory: root, TokenBudget: 512})
	if err != nil || prepared.Action != "ask" || prepared.RequestID == nil {
		t.Fatalf("request was not created: %#v %v", prepared, err)
	}
	requests, err := service.ContextRequests(ctx, "open")
	if err != nil || len(requests) != 1 {
		t.Fatalf("open request missing: %#v %v", requests, err)
	}
	value := "Deploy from the release workflow."
	created, err := service.Remember(ctx, "knowledge.deploy", memory.Note, &value, nil)
	if err != nil {
		t.Fatal(err)
	}
	resolved, err := service.ResolveContextRequest(ctx, *prepared.RequestID, "knowledge.deploy")
	if err != nil || !resolved {
		t.Fatalf("request was not resolved: %v %v", resolved, err)
	}
	expected := "retrieve"
	feedback, err := service.ContextFeedback(ctx, contextprepare.Feedback{DecisionID: prepared.DecisionID, Verdict: "incorrect", ExpectedAction: &expected, ExpectedKeys: []string{"knowledge.deploy"}})
	if err != nil || feedback.DecisionID != prepared.DecisionID {
		t.Fatalf("feedback missing: %#v %v", feedback, err)
	}
	decisions, err := service.ContextDecisions(ctx, 10)
	if err != nil || len(decisions) != 1 || decisions[0].Feedback == nil || decisions[0].Feedback.Verdict != "incorrect" {
		t.Fatalf("decision audit missing feedback: %#v %v", decisions, err)
	}

	review, err := service.CreateNeedsReview(ctx, "knowledge.deploy", "document", "runbook", "abc123", "Runbook changed")
	if err != nil {
		t.Fatal(err)
	}
	if confirmed, err := service.ConfirmMemory(ctx, "knowledge.deploy"); err != nil || !confirmed {
		t.Fatalf("memory was not confirmed: %v %v", confirmed, err)
	}
	reviews, err := service.NeedsReviews(ctx, "resolved")
	if err != nil || len(reviews) != 1 || reviews[0].ID != review.ID || reviews[0].Outcome != "keep" {
		t.Fatalf("review was not resolved by confirmation: %#v %v", reviews, err)
	}

	changed := "Deploy only from tagged releases."
	current, err := service.store.Memory(ctx, "demo", "knowledge.deploy")
	if err != nil {
		t.Fatal(err)
	}
	batch := []memory.BatchChange{{Key: "knowledge.deploy", Kind: memory.Note, Value: &changed, ExpectedHash: &current.Hash, ExpectedHashSet: true}}
	preview, err := service.ReconcileMemoryBatch(ctx, batch, false, "test")
	if err != nil || preview.Applied || preview.Changes[0].ExpectedHash == nil {
		t.Fatalf("unexpected batch preview: %#v %v", preview, err)
	}
	applied, err := service.ReconcileMemoryBatch(ctx, batch, true, "test")
	if err != nil || !applied.Applied || applied.Changes[0].VersionID <= created.VersionID {
		t.Fatalf("batch was not applied: %#v %v", applied, err)
	}
	if deleted, err := service.DeleteMemory(ctx, "knowledge.deploy"); err != nil || !deleted {
		t.Fatalf("memory was not deleted: %v %v", deleted, err)
	}
	if selected, err := service.SelectModel(ctx, "reconcile", "custom:latest"); err != nil || selected.Model != "custom:latest" {
		t.Fatalf("model was not selected: %#v %v", selected, err)
	}
	state, err := service.ModelState(ctx)
	if err != nil || state.Models[1].Model != "custom:latest" || state.Models[1].Source != "setting" {
		t.Fatalf("model selection missing from status: %#v %v", state, err)
	}
}
