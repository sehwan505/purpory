package reconcile

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeModel struct{ calls int }

func (*fakeModel) ContextTokens() int { return 1024 }

func (f *fakeModel) Extract(_ context.Context, transcript string) ([]Candidate, error) {
	f.calls++
	var evidence []string
	for _, line := range strings.Split(transcript, "\n") {
		if strings.HasPrefix(line, "[U") {
			evidence = append(evidence, strings.Fields(line)[0][1:])
		}
	}
	if len(evidence) == 0 {
		return nil, nil
	}
	return []Candidate{{Key: "intent.session.policy", Kind: "decision", Value: transcript[len(transcript)-1:], EvidenceIDs: evidence}}, nil
}

func (*fakeModel) Consolidate(_ context.Context, candidates []Candidate) (Candidate, error) {
	var evidence, sources []string
	for _, candidate := range candidates {
		evidence = append(evidence, candidate.EvidenceIDs...)
		sources = append(sources, candidate.ID)
	}
	return Candidate{Key: candidates[0].Key, Kind: candidates[0].Kind, Value: candidates[len(candidates)-1].Value, EvidenceIDs: evidence, SourceIDs: sources}, nil
}

func TestTranscriptReconcilePreservesUserEvidenceAcrossChunks(t *testing.T) {
	path := filepath.Join(t.TempDir(), "session.jsonl")
	records := []map[string]any{
		{"type": "user", "message": map[string]any{"role": "user", "content": []any{map[string]any{"type": "text", "text": "keep this " + strings.Repeat("가", 3000)}}}},
		{"type": "assistant", "message": map[string]any{"role": "assistant", "content": "understood"}},
		{"type": "response_item", "payload": map[string]any{"type": "message", "role": "user", "content": []any{map[string]any{"type": "input_text", "text": "later correction"}}}},
		{"type": "user", "message": map[string]any{"role": "user", "content": []any{map[string]any{"type": "tool_result", "text": "ignore tool output"}}}},
	}
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	encoder := json.NewEncoder(file)
	for _, record := range records {
		if err := encoder.Encode(record); err != nil {
			t.Fatal(err)
		}
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	messages, err := ReadTranscript(path)
	if err != nil {
		t.Fatal(err)
	}
	model := &fakeModel{}
	candidates, err := Propose(context.Background(), messages, model)
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) != 3 || model.calls < 2 || len(candidates) != 1 || candidates[0].EvidenceIDs[0] != "U000001" || candidates[0].EvidenceIDs[len(candidates[0].EvidenceIDs)-1] != "U000003" {
		t.Fatalf("reconcile lost transcript evidence: messages=%#v candidates=%#v calls=%d", messages, candidates, model.calls)
	}
}
