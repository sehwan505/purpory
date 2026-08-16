// Package reconcile turns agent transcripts into durable, user-grounded memory candidates.
package reconcile

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"unicode/utf8"

	"github.com/sehwan505/purpory/internal/memory"
)

const maximumMessageBytes = 16 << 20

type Message struct {
	ID   string `json:"id"`
	Role string `json:"role"`
	Text string `json:"text"`
}

type Candidate struct {
	ID          string      `json:"id,omitempty"`
	Key         string      `json:"key"`
	Kind        memory.Kind `json:"kind"`
	Value       string      `json:"value"`
	EvidenceIDs []string    `json:"evidenceIds"`
	SourceIDs   []string    `json:"sourceIds,omitempty"`
}

type Model interface {
	ContextTokens() int
	Extract(context.Context, string) ([]Candidate, error)
	Consolidate(context.Context, []Candidate) (Candidate, error)
}

func ReadTranscript(path string) ([]Message, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("read transcript: %w", err)
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64<<10), maximumMessageBytes)
	var messages []Message
	for line := 1; scanner.Scan(); line++ {
		if strings.TrimSpace(scanner.Text()) == "" {
			continue
		}
		var record map[string]any
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			return nil, fmt.Errorf("read transcript: invalid JSON on line %d: %w", line, err)
		}
		role, text := message(record)
		if role == "" || text == "" {
			continue
		}
		prefix := "A"
		if role == "user" {
			prefix = "U"
		}
		messages = append(messages, Message{ID: fmt.Sprintf("%s%06d", prefix, len(messages)+1), Role: role, Text: text})
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read transcript: %w", err)
	}
	return messages, nil
}

func Propose(ctx context.Context, messages []Message, model Model) ([]Candidate, error) {
	if model == nil || model.ContextTokens() < 1024 {
		return nil, errors.New("reconcile transcript: valid model is required")
	}
	budget := model.ContextTokens()/2 - 512
	if budget < 128 {
		budget = 128
	}
	var candidates []Candidate
	sequence := 0
	for _, chunk := range chunks(messages, budget) {
		extracted, err := model.Extract(ctx, chunk.text)
		if err != nil {
			return nil, fmt.Errorf("reconcile transcript: extract: %w", err)
		}
		for _, candidate := range extracted {
			candidate.EvidenceIDs = unique(candidate.EvidenceIDs)
			if err := validate(candidate, chunk.userIDs); err != nil {
				return nil, err
			}
			sequence++
			candidate.ID = fmt.Sprintf("C%06d", sequence)
			candidates = append(candidates, candidate)
		}
	}
	grouped := map[string][]Candidate{}
	var keys []string
	for _, candidate := range candidates {
		if _, found := grouped[candidate.Key]; !found {
			keys = append(keys, candidate.Key)
		}
		grouped[candidate.Key] = append(grouped[candidate.Key], candidate)
	}
	result := make([]Candidate, 0, len(keys))
	for _, key := range keys {
		candidate, err := reduce(ctx, model, grouped[key])
		if err != nil {
			return nil, err
		}
		candidate.ID = ""
		candidate.SourceIDs = nil
		result = append(result, candidate)
	}
	return result, nil
}

type chunk struct {
	text    string
	userIDs map[string]bool
}

func chunks(messages []Message, tokenBudget int) []chunk {
	maximum := tokenBudget * 4
	var records []Message
	for _, item := range messages {
		header := fmt.Sprintf("[%s %s]\n", item.ID, strings.ToUpper(item.Role))
		for _, piece := range split(item.Text, maximum-len(header)-2) {
			records = append(records, Message{ID: item.ID, Role: item.Role, Text: header + piece})
		}
	}
	var result []chunk
	var current []string
	users := map[string]bool{}
	flush := func() {
		if len(current) > 0 {
			result = append(result, chunk{text: strings.Join(current, "\n\n"), userIDs: users})
			current = nil
			users = map[string]bool{}
		}
	}
	for _, record := range records {
		separator := 0
		if len(current) > 0 {
			separator = 2
		}
		size := separator + len(record.Text)
		for _, value := range current {
			size += len(value)
		}
		if len(current) > 0 && size > maximum {
			flush()
		}
		current = append(current, record.Text)
		if record.Role == "user" {
			users[record.ID] = true
		}
	}
	flush()
	return result
}

func split(value string, maximum int) []string {
	if maximum < 4 {
		maximum = 4
	}
	var result []string
	for len(value) > maximum {
		end := maximum
		for end > 0 && !utf8.RuneStart(value[end]) {
			end--
		}
		result = append(result, value[:end])
		value = value[end:]
	}
	return append(result, value)
}

func reduce(ctx context.Context, model Model, candidates []Candidate) (Candidate, error) {
	for len(candidates) > 1 {
		var next []Candidate
		for index := 0; index < len(candidates); index += 2 {
			if index+1 == len(candidates) {
				next = append(next, candidates[index])
				continue
			}
			pair := candidates[index : index+2]
			candidate, err := model.Consolidate(ctx, pair)
			if err != nil {
				return Candidate{}, fmt.Errorf("reconcile transcript: consolidate: %w", err)
			}
			allowedEvidence := map[string]bool{}
			allowedSources := map[string]bool{}
			for _, item := range pair {
				allowedSources[item.ID] = true
				for _, evidence := range item.EvidenceIDs {
					allowedEvidence[evidence] = true
				}
			}
			candidate.EvidenceIDs = unique(candidate.EvidenceIDs)
			if err := validate(candidate, allowedEvidence); err != nil {
				return Candidate{}, err
			}
			if !sameSet(candidate.SourceIDs, allowedSources) {
				return Candidate{}, errors.New("reconcile transcript: consolidation omitted candidates")
			}
			candidate.ID = fmt.Sprintf("R%s", pair[1].ID)
			next = append(next, candidate)
		}
		candidates = next
	}
	return candidates[0], nil
}

func validate(candidate Candidate, allowedEvidence map[string]bool) error {
	value := candidate.Value
	if len([]rune(value)) > 4096 {
		return errors.New("reconcile transcript: candidate value exceeds 4096 characters")
	}
	if _, err := memory.New("validation", candidate.Key, candidate.Kind, &value, nil); err != nil {
		return fmt.Errorf("reconcile transcript: candidate: %w", err)
	}
	if len(candidate.EvidenceIDs) == 0 {
		return errors.New("reconcile transcript: candidate has no user evidence")
	}
	for _, evidence := range candidate.EvidenceIDs {
		if !allowedEvidence[evidence] {
			return errors.New("reconcile transcript: candidate cites non-user evidence")
		}
	}
	return nil
}

func sameSet(values []string, allowed map[string]bool) bool {
	if len(values) != len(allowed) {
		return false
	}
	seen := map[string]bool{}
	for _, value := range values {
		if !allowed[value] || seen[value] {
			return false
		}
		seen[value] = true
	}
	return true
}

func unique(values []string) []string {
	seen := map[string]bool{}
	result := values[:0]
	for _, value := range values {
		if !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	return result
}

func message(record map[string]any) (string, string) {
	candidates := []map[string]any{record}
	if value, ok := record["payload"].(map[string]any); ok {
		candidates = append(candidates, value)
		if nested, ok := value["message"].(map[string]any); ok {
			candidates = append(candidates, nested)
		}
	}
	if value, ok := record["message"].(map[string]any); ok {
		candidates = append(candidates, value)
	}
	role := ""
	for _, candidate := range candidates {
		value, _ := candidate["role"].(string)
		if value == "user" || value == "assistant" {
			role = value
			break
		}
	}
	if role == "" {
		for _, candidate := range candidates {
			value, _ := candidate["type"].(string)
			switch strings.ToLower(value) {
			case "user", "user_message":
				role = "user"
			case "assistant", "assistant_message":
				role = "assistant"
			}
		}
	}
	if role == "" {
		return "", ""
	}
	for index := len(candidates) - 1; index >= 0; index-- {
		for _, field := range []string{"content", "text", "message"} {
			if text := extractText(candidates[index][field]); text != "" {
				return role, text
			}
		}
	}
	return "", ""
}

func extractText(value any) string {
	switch typed := value.(type) {
	case string:
		return strings.TrimSpace(typed)
	case []any:
		var parts []string
		for _, raw := range typed {
			item, ok := raw.(map[string]any)
			if !ok {
				if text, ok := raw.(string); ok && strings.TrimSpace(text) != "" {
					parts = append(parts, strings.TrimSpace(text))
				}
				continue
			}
			kind, _ := item["type"].(string)
			if kind != "" && kind != "text" && kind != "input_text" && kind != "output_text" {
				continue
			}
			if text, ok := item["text"].(string); ok && strings.TrimSpace(text) != "" {
				parts = append(parts, strings.TrimSpace(text))
			}
		}
		return strings.Join(parts, "\n")
	}
	return ""
}
