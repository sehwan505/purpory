package app

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/sehwan505/purpory/internal/memory"
)

func TestContextAndMemoryLifecycle(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")

	value := "Deploy from the release workflow."
	created, err := service.Remember(ctx, "knowledge.deploy", memory.Note, &value, nil)
	if err != nil {
		t.Fatal(err)
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
	preview, err := service.MemoryBatch(ctx, batch, false)
	if err != nil || preview.Applied || preview.Changes[0].ExpectedHash == nil {
		t.Fatalf("unexpected batch preview: %#v %v", preview, err)
	}
	applied, err := service.MemoryBatch(ctx, batch, true)
	if err != nil || !applied.Applied || applied.Changes[0].VersionID <= created.VersionID {
		t.Fatalf("batch was not applied: %#v %v", applied, err)
	}
	if deleted, err := service.DeleteMemory(ctx, "knowledge.deploy"); err != nil || !deleted {
		t.Fatalf("memory was not deleted: %v %v", deleted, err)
	}
}
