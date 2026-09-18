package app

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/reconcile"
	"github.com/sehwan505/purpory/internal/store"
)

type recordingSelector struct {
	seen   []string
	remove map[string][]string
	nearby map[string][]string
}

func (s *recordingSelector) Select(_ context.Context, requests []reconcile.AdmissionRequest, maximum int) ([]reconcile.Selection, error) {
	selections := make([]reconcile.Selection, 0, min(len(requests), maximum))
	for _, request := range requests {
		s.seen = append(s.seen, request.Candidate.Key)
		if s.nearby == nil {
			s.nearby = map[string][]string{}
		}
		for _, entry := range request.Nearby {
			s.nearby[request.Candidate.Key] = append(s.nearby[request.Candidate.Key], entry.Memory.Key)
		}
		if len(selections) < maximum {
			selections = append(selections, reconcile.Selection{Key: request.Candidate.Key, RemoveKeys: s.remove[request.Candidate.Key]})
		}
	}
	return selections, nil
}

func TestAdmissionPreservesExistingMemoryUpdates(t *testing.T) {
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	value := "Keep the existing policy."
	if _, err := service.Remember(ctx, "policy.existing", memory.Decision, &value, nil); err != nil {
		t.Fatal(err)
	}
	candidates := []reconcile.Candidate{{Key: "policy.existing", Kind: memory.Decision, Value: "Update the existing policy."}}
	for index := 0; index < reconcile.MaximumNewMemories+1; index++ {
		candidates = append(candidates, reconcile.Candidate{Key: fmt.Sprintf("policy.new-%02d", index), Kind: memory.Decision, Value: "Add a durable policy."})
	}
	selector := &recordingSelector{}
	memories, err := service.store.Memories(ctx, service.project.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	admitted, err := service.admitCandidates(ctx, candidates, memories, nil, selector)
	if err != nil {
		t.Fatal(err)
	}
	if len(admitted) != reconcile.MaximumNewMemories+1 || admitted[0].Candidate.Key != "policy.existing" {
		t.Fatalf("admitted candidates = %#v", admitted)
	}
	for _, key := range selector.seen {
		if key == "policy.existing" {
			t.Fatal("existing memory update consumed the new-memory admission budget")
		}
	}
}

func TestAdmissionSkipsDuplicateContentUnderNewKey(t *testing.T) {
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	value := "Keep one durable policy."
	if _, err := service.Remember(ctx, "policy.original", memory.Decision, &value, nil); err != nil {
		t.Fatal(err)
	}
	selector := &recordingSelector{}
	memories, err := service.store.Memories(ctx, service.project.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	admitted, err := service.admitCandidates(ctx, []reconcile.Candidate{{Key: "policy.duplicate", Kind: memory.Decision, Value: value}}, memories, nil, selector)
	if err != nil || len(admitted) != 0 || len(selector.seen) != 0 {
		t.Fatalf("duplicate content was admitted: %#v, %#v, %v", admitted, selector.seen, err)
	}
}

func TestAdmissionCanReplaceOnlyNearbyMemory(t *testing.T) {
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	oldValue := "Use the legacy database policy."
	if _, err := service.Remember(ctx, "database.legacy", memory.Decision, &oldValue, nil); err != nil {
		t.Fatal(err)
	}
	candidate := reconcile.Candidate{Key: "database.policy", Kind: memory.Decision, Value: "Use the consolidated database policy."}
	selector := &recordingSelector{remove: map[string][]string{candidate.Key: {"database.legacy"}}}
	memories, err := service.store.Memories(ctx, service.project.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	admitted, err := service.admitCandidates(ctx, []reconcile.Candidate{candidate}, memories, nil, selector)
	if err != nil || len(admitted) != 1 || len(admitted[0].Remove) != 1 || admitted[0].Remove[0].Key != "database.legacy" || len(selector.nearby[candidate.Key]) != 1 {
		t.Fatalf("nearby replacement = %#v, nearby=%#v, %v", admitted, selector.nearby, err)
	}
}

func TestNearbyMemoriesBalancesRelevanceWithOldUnusedEntries(t *testing.T) {
	value := func(text string) *string { return &text }
	memories := []memory.Memory{
		{Key: "database.current", Kind: memory.Decision, Value: value("Current database policy."), CreatedAt: "2026-01-01T00:00:00Z", UpdatedAt: "2026-09-01T00:00:00Z"},
		{Key: "database.legacy", Kind: memory.Decision, Value: value("Legacy storage choice."), CreatedAt: "2020-01-01T00:00:00Z", UpdatedAt: "2020-01-01T00:00:00Z"},
		{Key: "database.secondary", Kind: memory.Decision, Value: value("Secondary database note."), CreatedAt: "2024-01-01T00:00:00Z", UpdatedAt: "2024-01-01T00:00:00Z"},
	}
	usage := map[string]store.NodeUsage{
		"intent:database.current":   {Count: 20, Sessions: 5, LastUsedAt: 100},
		"intent:database.secondary": {Count: 2, Sessions: 1, LastUsedAt: 50},
	}
	candidate := reconcile.Candidate{Key: "database.policy", Kind: memory.Decision, Value: "Current database policy."}
	nearby := nearbyMemories(candidate, memories, nil, usage, 2)
	if len(nearby) != 2 || nearby[0].Memory.Key != "database.legacy" || nearby[1].Memory.Key != "database.current" || nearby[0].Uses != 0 || nearby[1].Sessions != 5 {
		t.Fatalf("nearby lifecycle balance = %#v", nearby)
	}
}

func TestApplyChangesReplacesNearbyMemory(t *testing.T) {
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	value := "Use the old provider."
	if _, err := service.Remember(ctx, "database.provider", memory.Decision, &value, nil); err != nil {
		t.Fatal(err)
	}
	old, err := service.store.Memory(ctx, service.project.ID, "database.provider")
	if err != nil {
		t.Fatal(err)
	}
	quote := "Use the consolidated provider policy."
	candidate := reconcile.Candidate{Key: "database.policy", Kind: memory.Decision, Value: quote, EvidenceIDs: []string{"U000001"}, EvidenceRefs: []memory.EvidenceRef{{MessageID: "U000001", PartID: "U000001P001", Quote: quote, EndByte: len(quote)}}}
	result, err := service.applyChanges(ctx, "codex:replace", []reconcile.Candidate{candidate}, map[string][]memory.Memory{candidate.Key: {old}})
	if err != nil || result.Created != 1 || result.Deleted != 1 {
		t.Fatalf("replace result = %#v, %v", result, err)
	}
	if _, err := service.store.Memory(ctx, service.project.ID, "database.provider"); !errors.Is(err, sql.ErrNoRows) {
		t.Fatalf("replaced memory still exists: %v", err)
	}
}

func TestApplyCandidatesRejectsAdmissionBypass(t *testing.T) {
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	candidates := make([]reconcile.Candidate, reconcile.MaximumNewMemories+1)
	for index := range candidates {
		candidates[index] = reconcile.Candidate{Key: fmt.Sprintf("policy.%02d", index), Kind: memory.Decision, Value: "Add a durable policy."}
	}
	err := service.applyCandidates(ctx, "codex:admission-bypass", candidates)
	if err == nil || !strings.Contains(err.Error(), "exceeds limit") {
		t.Fatalf("admission bypass error = %v", err)
	}
	stored, loadErr := service.store.Memories(ctx, service.project.ID, "")
	if loadErr != nil || len(stored) != 0 {
		t.Fatalf("admission bypass wrote memories: %#v, %v", stored, loadErr)
	}
}
