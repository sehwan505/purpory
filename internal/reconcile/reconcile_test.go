package reconcile

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
)

type fakeModel struct{ calls int }

type materialModel struct {
	ref      string
	relation string
}

type contextModel struct{ candidate Candidate }

type linkModel struct{ results []LinkResult }

func (m linkModel) Link(context.Context, []LinkRequest) ([]LinkResult, error) { return m.results, nil }

func (contextModel) ContextTokens() int { return 1024 }
func (m contextModel) Extract(_ context.Context, transcript string) ([]Candidate, error) {
	if !strings.Contains(transcript, "["+m.candidate.EvidenceRefs[0].MessageID+" ") {
		return nil, nil
	}
	for _, ref := range m.candidate.ContextRefs {
		if !strings.Contains(transcript, ref.Quote) {
			return nil, nil
		}
	}
	return []Candidate{m.candidate}, nil
}
func (contextModel) Consolidate(_ context.Context, candidates []Candidate) (Candidate, error) {
	result := candidates[len(candidates)-1]
	result.EvidenceRefs = nil
	result.ContextRefs = nil
	result.SourceIDs = nil
	for _, candidate := range candidates {
		result.EvidenceRefs = append(result.EvidenceRefs, candidate.EvidenceRefs...)
		result.ContextRefs = append(result.ContextRefs, candidate.ContextRefs...)
		result.SourceIDs = append(result.SourceIDs, candidate.ID)
	}
	return result, nil
}

func (materialModel) ContextTokens() int { return 1024 }

func (m materialModel) Extract(context.Context, string) ([]Candidate, error) {
	relation := m.relation
	if relation == "" {
		relation = graph.RelationAppliesTo
	}
	return []Candidate{{Key: "intent.release", Kind: "decision", Value: "Ship tagged builds.", EvidenceRefs: []memory.EvidenceRef{{MessageID: "U000001", PartID: "U000001P001", Quote: "Ship tagged builds."}}, MaterialLinks: []MaterialLink{{Relation: relation, MaterialRef: m.ref}}}}, nil
}

func (materialModel) Consolidate(context.Context, []Candidate) (Candidate, error) {
	return Candidate{}, nil
}

func (*fakeModel) ContextTokens() int { return 1024 }

func (f *fakeModel) Extract(_ context.Context, transcript string) ([]Candidate, error) {
	f.calls++
	var evidence []memory.EvidenceRef
	lines := strings.Split(transcript, "\n")
	for index, line := range lines {
		if strings.HasPrefix(line, "[U") {
			fields := strings.Fields(line)
			if index+1 < len(lines) && len(fields) >= 2 {
				words := strings.Fields(lines[index+1])
				if len(words) > 0 {
					evidence = append(evidence, memory.EvidenceRef{MessageID: fields[0][1:], PartID: fields[1], Quote: words[0]})
				}
			}
		}
	}
	if len(evidence) == 0 {
		return nil, nil
	}
	return []Candidate{{Key: "intent.session.policy", Kind: "decision", Value: transcript[len(transcript)-1:], EvidenceRefs: evidence}}, nil
}

func (*fakeModel) Consolidate(_ context.Context, candidates []Candidate) (Candidate, error) {
	var evidence []memory.EvidenceRef
	var sources []string
	var links []MaterialLink
	var contextRefs []memory.ContextRef
	for _, candidate := range candidates {
		evidence = append(evidence, candidate.EvidenceRefs...)
		contextRefs = append(contextRefs, candidate.ContextRefs...)
		links = append(links, candidate.MaterialLinks...)
		sources = append(sources, candidate.ID)
	}
	return Candidate{Key: candidates[0].Key, Kind: candidates[0].Kind, Value: candidates[len(candidates)-1].Value, EvidenceRefs: evidence, ContextRefs: contextRefs, MaterialLinks: links, SourceIDs: sources}, nil
}

func TestTranscriptReconcilePreservesUserEvidenceAcrossChunks(t *testing.T) {
	path := filepath.Join(t.TempDir(), "session.jsonl")
	records := []map[string]any{
		{"type": "user", "message": map[string]any{"role": "user", "content": []any{map[string]any{"type": "text", "text": "keep this " + strings.Repeat("가", 3000)}}}},
		{"type": "assistant", "message": map[string]any{"role": "assistant", "content": []any{map[string]any{"type": "text", "text": "understood"}, map[string]any{"type": "file", "file_id": "artifact-1", "filename": "plan.md"}}}},
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
	candidates, err := Propose(context.Background(), messages, model, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) != 4 || len(messages[1].Parts) != 2 || messages[1].Parts[1].Kind != "attachment" || messages[1].Parts[1].Ref != "artifact-1" || messages[3].Role != "tool" || messages[3].Parts[0].Kind != "tool_result" || model.calls < 2 || len(candidates) != 1 || candidates[0].EvidenceIDs[0] != "U000001" || candidates[0].EvidenceIDs[len(candidates[0].EvidenceIDs)-1] != "U000003" {
		t.Fatalf("reconcile lost transcript evidence: messages=%#v candidates=%#v calls=%d", messages, candidates, model.calls)
	}
}

func TestTranscriptImplicitReplyUsesOnlyImmediatelyPreviousAssistant(t *testing.T) {
	records := []map[string]any{
		{"type": "assistant", "message": map[string]any{"role": "assistant", "content": "Use SQLite."}},
		{"type": "user", "message": map[string]any{"role": "user", "content": "Different question."}},
		{"type": "user", "message": map[string]any{"role": "user", "content": "좋아"}},
	}
	messages, err := ReadTranscript(writeTranscript(t, records))
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) != 3 || messages[1].ReplyToID != messages[0].ID || messages[2].ReplyToID != "" {
		t.Fatalf("implicit reply crossed an intervening event: %#v", messages)
	}
}

func TestTranscriptNormalizesStructuredUserChoice(t *testing.T) {
	records := []map[string]any{
		{"type": "assistant", "message": map[string]any{"role": "assistant", "content": []any{map[string]any{
			"type": "tool_use", "id": "choice-1", "name": "AskUserQuestion", "input": map[string]any{"questions": []any{map[string]any{
				"question": "Database?", "options": []any{map[string]any{"label": "SQLite"}, map[string]any{"label": "PostgreSQL", "description": "remote"}},
			}}},
		}}}},
		{"type": "user", "message": map[string]any{"role": "user", "content": []any{map[string]any{"type": "tool_result", "tool_use_id": "choice-1", "content": "PostgreSQL"}}}},
	}
	messages, err := ReadTranscript(writeTranscript(t, records))
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) != 2 || messages[0].Kind != "choice" || messages[1].Role != "user" || messages[1].Kind != "choice" || messages[1].ReplyToID != messages[0].ID || !strings.Contains(messages[0].Text, "PostgreSQL") || messages[1].Text != "PostgreSQL" {
		t.Fatalf("structured choice was not normalized: %#v", messages)
	}
	partID := ""
	var assistantPart Part
	for _, part := range messages[0].Parts {
		if part.Kind == "choice" && strings.Contains(part.Text, "PostgreSQL") {
			partID = part.ID
			assistantPart = part
		}
	}
	candidate := Candidate{
		Key: "database.provider", Kind: memory.Decision, Value: "Use PostgreSQL.", EvidenceRefs: []memory.EvidenceRef{{MessageID: messages[1].ID, PartID: messages[1].Parts[0].ID, Quote: "PostgreSQL"}},
		ContextRefs: []memory.ContextRef{{MessageID: messages[0].ID, PartID: partID, Quote: "PostgreSQL"}},
	}
	if values, err := Propose(context.Background(), messages, contextModel{candidate}, nil); err != nil || len(values) != 1 || values[0].EvidenceRefs[0].EndByte-values[0].EvidenceRefs[0].StartByte != len("PostgreSQL") || messages[1].Parts[0].Text[values[0].EvidenceRefs[0].StartByte:values[0].EvidenceRefs[0].EndByte] != "PostgreSQL" || assistantPart.Text[values[0].ContextRefs[0].StartByte:values[0].ContextRefs[0].EndByte] != "PostgreSQL" {
		t.Fatalf("structured choice was not accepted as user-grounded context: %#v %v", values, err)
	}
}

func TestTranscriptNormalizesCodexFunctionChoice(t *testing.T) {
	records := []map[string]any{
		{"type": "response_item", "payload": map[string]any{
			"type": "function_call", "name": "request_user_input", "call_id": "choice-1",
			"arguments": map[string]any{"questions": []any{map[string]any{"question": "Database?", "options": []any{map[string]any{"label": "SQLite"}, map[string]any{"label": "PostgreSQL"}}}}},
		}},
		{"type": "response_item", "payload": map[string]any{"type": "function_call_output", "call_id": "choice-1", "output": "PostgreSQL"}},
	}
	messages, err := ReadTranscript(writeTranscript(t, records))
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) != 2 || messages[0].Role != "assistant" || messages[0].Kind != "choice" || messages[1].Role != "user" || messages[1].Kind != "choice" || messages[1].ReplyToID != messages[0].ID {
		t.Fatalf("Codex function choice was not normalized: %#v", messages)
	}
}

func TestReconcileAcceptsOnlyExactApprovedAssistantExcerpt(t *testing.T) {
	assistant := Event{ID: "A000001", Sequence: 1, Role: "assistant", Kind: "message", Text: "Use PostgreSQL.\nUse Redis.", Parts: []Part{{ID: "A000001P001", Kind: "text", Text: "Use PostgreSQL.\nUse Redis."}}}
	user := Event{ID: "U000002", Sequence: 2, Role: "user", Kind: "message", Text: "PostgreSQL 방식으로 진행해", ReplyToID: assistant.ID, Parts: []Part{{ID: "U000002P001", Kind: "text", Text: "PostgreSQL 방식으로 진행해"}}}
	candidate := Candidate{
		Key: "database.provider", Kind: memory.Decision, Value: "Use PostgreSQL.", EvidenceRefs: []memory.EvidenceRef{{MessageID: user.ID, PartID: user.Parts[0].ID, Quote: "PostgreSQL 방식으로 진행해"}},
		ContextRefs: []memory.ContextRef{{MessageID: assistant.ID, PartID: assistant.Parts[0].ID, Quote: "Use PostgreSQL."}},
		IntentLinks: []IntentLink{{Relation: graph.RelationDependsOn, TargetRef: "invented.target"}},
	}
	accepted, err := Propose(context.Background(), []Event{assistant, user}, contextModel{candidate}, nil)
	if err != nil || len(accepted) != 1 || accepted[0].ContextRefs[0].Quote != "Use PostgreSQL." || len(accepted[0].IntentLinks) != 0 {
		t.Fatalf("approved final-answer excerpt was rejected: %#v %v", accepted, err)
	}

	invented := candidate
	invented.ContextRefs = []memory.ContextRef{{MessageID: assistant.ID, PartID: assistant.Parts[0].ID, Quote: "Use MySQL."}}
	if values, err := Propose(context.Background(), []Event{assistant, user}, contextModel{invented}, nil); err != nil || len(values) != 0 {
		t.Fatalf("invented assistant quote was accepted: %#v %v", values, err)
	}
	futureAssistant := assistant
	futureAssistant.Sequence = 3
	if values, err := Propose(context.Background(), []Event{user, futureAssistant}, contextModel{candidate}, nil); err != nil || len(values) != 0 {
		t.Fatalf("future assistant context was accepted: %#v %v", values, err)
	}

	vague := user
	vague.Text = "좋아"
	vague.Parts[0].Text = "좋아"
	if values, err := Propose(context.Background(), []Event{assistant, vague}, contextModel{candidate}, nil); err != nil || len(values) != 0 {
		t.Fatalf("ambiguous approval was accepted: %#v %v", values, err)
	}
	withoutContext := candidate
	withoutContext.ContextRefs = nil
	if values, err := Propose(context.Background(), []Event{assistant, vague}, contextModel{withoutContext}, nil); err != nil || len(values) != 0 {
		t.Fatalf("ambiguous approval created a direct candidate: %#v %v", values, err)
	}
}

func TestReconcileKeepsReplyContextAcrossChunkBoundary(t *testing.T) {
	text := "Use PostgreSQL.\n" + strings.Repeat("Implementation detail. ", 80)
	assistant := Event{ID: "A000001", Sequence: 1, Role: "assistant", Kind: "message", Text: text, Parts: []Part{{ID: "A000001P001", Kind: "text", Text: text}}}
	user := Event{ID: "U000002", Sequence: 2, Role: "user", Kind: "message", Text: "PostgreSQL 방식으로 진행해", ReplyToID: assistant.ID, Parts: []Part{{ID: "U000002P001", Kind: "text", Text: "PostgreSQL 방식으로 진행해"}}}
	candidate := Candidate{
		Key: "database.provider", Kind: memory.Decision, Value: "Use PostgreSQL.", EvidenceRefs: []memory.EvidenceRef{{MessageID: user.ID, PartID: user.Parts[0].ID, Quote: "PostgreSQL 방식으로 진행해"}},
		ContextRefs: []memory.ContextRef{{MessageID: assistant.ID, PartID: assistant.Parts[0].ID, Quote: "Use PostgreSQL."}},
	}
	accepted, err := Propose(context.Background(), []Event{assistant, user}, contextModel{candidate}, nil)
	if err != nil || len(accepted) != 1 {
		t.Fatalf("reply context was lost at a chunk boundary: %#v %v", accepted, err)
	}
}

func TestReconcileRejectsAmbiguousSourceQuote(t *testing.T) {
	message := Event{ID: "U000001", Sequence: 1, Role: "user", Kind: "message", Text: "SQLite then SQLite", Parts: []Part{{ID: "U000001P001", Kind: "text", Text: "SQLite then SQLite"}}}
	candidate := Candidate{Key: "database.provider", Kind: memory.Decision, Value: "Use SQLite.", EvidenceRefs: []memory.EvidenceRef{{MessageID: message.ID, PartID: message.Parts[0].ID, Quote: "SQLite"}}}
	if values, err := Propose(context.Background(), []Event{message}, contextModel{candidate}, nil); err != nil || len(values) != 0 {
		t.Fatalf("ambiguous source quote was accepted: %#v %v", values, err)
	}
}

func TestConnectAcceptsOnlyGroundedAvailableIntentLinks(t *testing.T) {
	evidence := memory.EvidenceRef{MessageID: "U000001", PartID: "U000001P001", Quote: "Mobile release depends on offline support.", EndByte: len("Mobile release depends on offline support.")}
	candidate := Candidate{Key: "release.mobile", Kind: memory.Decision, Value: "Ship the mobile release.", EvidenceIDs: []string{"U000001"}, EvidenceRefs: []memory.EvidenceRef{evidence}}
	requests := []LinkRequest{{Candidate: candidate, Targets: []IntentTarget{{Key: "platform.offline", Value: "Support offline mode."}}, ExpectedStates: map[string]string{graph.RelationDependsOn + "\x00platform.offline": graph.StateRetired}}}
	model := linkModel{results: []LinkResult{{SourceKey: candidate.Key, Links: []IntentLink{{Relation: graph.RelationDependsOn, TargetRef: "platform.offline", EvidenceRefs: []memory.EvidenceRef{{MessageID: evidence.MessageID, PartID: evidence.PartID, Quote: evidence.Quote}}}}}}}
	connected, err := Connect(context.Background(), []Candidate{candidate}, requests, model)
	if err != nil || len(connected[0].IntentLinks) != 1 || connected[0].IntentLinks[0].EvidenceRefs[0].EndByte != evidence.EndByte || connected[0].IntentLinks[0].ExpectedState != graph.StateRetired {
		t.Fatalf("grounded intent link was not accepted: %#v %v", connected, err)
	}
	model.results[0].Links[0].TargetRef = "platform.missing"
	connected, err = Connect(context.Background(), []Candidate{candidate}, requests, model)
	if err != nil || len(connected[0].IntentLinks) != 0 {
		t.Fatalf("unavailable intent target was not discarded: %#v %v", connected, err)
	}
}

func TestConnectAcceptsOnlyGroundedPresentedRetirements(t *testing.T) {
	evidence := memory.EvidenceRef{MessageID: "U000001", PartID: "U000001P001", Quote: "Mobile no longer depends on offline support.", EndByte: len("Mobile no longer depends on offline support.")}
	candidate := Candidate{Key: "release.mobile", Kind: memory.Decision, Value: "Ship mobile without offline support.", EvidenceIDs: []string{"U000001"}, EvidenceRefs: []memory.EvidenceRef{evidence}}
	existing := IntentLink{Relation: graph.RelationDependsOn, TargetRef: "platform.offline"}
	requests := []LinkRequest{{Candidate: candidate, Targets: []IntentTarget{{Key: "platform.offline", Value: "Support offline mode."}}, Existing: []IntentLink{existing}, ExpectedStates: map[string]string{graph.RelationDependsOn + "\x00platform.offline": graph.StateActive}}}
	model := linkModel{results: []LinkResult{{SourceKey: candidate.Key, Retirements: []IntentLink{{Relation: existing.Relation, TargetRef: existing.TargetRef, EvidenceRefs: []memory.EvidenceRef{{MessageID: evidence.MessageID, PartID: evidence.PartID, Quote: evidence.Quote}}}}}}}
	connected, err := Connect(context.Background(), []Candidate{candidate}, requests, model)
	if err != nil || len(connected[0].RetiredIntentLinks) != 1 || connected[0].RetiredIntentLinks[0].EvidenceRefs[0].EndByte != evidence.EndByte || connected[0].RetiredIntentLinks[0].ExpectedState != graph.StateActive {
		t.Fatalf("grounded retirement was not accepted: %#v %v", connected, err)
	}
	requests[0].Existing = nil
	connected, err = Connect(context.Background(), []Candidate{candidate}, requests, model)
	if err != nil || len(connected[0].RetiredIntentLinks) != 0 {
		t.Fatalf("unpresented retirement was accepted: %#v %v", connected, err)
	}
}

func TestReconcileLinksOnlyAvailableMaterials(t *testing.T) {
	messages := []Event{{ID: "U000001", Role: "user", Text: "Ship tagged builds."}}
	allowed := "file:release.md"
	candidates, err := Propose(context.Background(), messages, materialModel{ref: allowed}, []string{allowed})
	if err != nil || len(candidates) != 1 || candidates[0].MaterialLinks[0].MaterialRef != allowed || candidates[0].MaterialLinks[0].Relation != graph.RelationAppliesTo {
		t.Fatalf("available material was not retained: %#v %v", candidates, err)
	}
	if candidates, err := Propose(context.Background(), messages, materialModel{ref: "file:missing.md"}, []string{allowed}); err != nil || len(candidates) != 0 {
		t.Fatalf("unavailable material was not discarded: %#v %v", candidates, err)
	}
	if candidates, err := Propose(context.Background(), messages, materialModel{ref: allowed, relation: "evidenced_by"}, []string{allowed}); err != nil || len(candidates) != 0 {
		t.Fatalf("unsupported material relation was not discarded: %#v %v", candidates, err)
	}
}

func writeTranscript(t *testing.T, records []map[string]any) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "session.jsonl")
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
	return path
}
