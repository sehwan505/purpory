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

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
)

const maximumMessageBytes = 16 << 20

var errInvalidCandidate = errors.New("invalid reconciliation candidate")

type Event struct {
	ID        string `json:"id"`
	Sequence  int    `json:"sequence"`
	Role      string `json:"role"`
	Kind      string `json:"kind"`
	Text      string `json:"text,omitempty"`
	ReplyToID string `json:"replyToId,omitempty"`
	CallID    string `json:"callId,omitempty"`
	Parts     []Part `json:"parts,omitempty"`
}

type Part struct {
	ID   string `json:"id"`
	Kind string `json:"kind"`
	Text string `json:"text,omitempty"`
	Name string `json:"name,omitempty"`
	Ref  string `json:"ref,omitempty"`
}

type Candidate struct {
	ID                 string               `json:"id,omitempty"`
	Key                string               `json:"key"`
	Kind               memory.Kind          `json:"kind"`
	Value              string               `json:"value"`
	EvidenceIDs        []string             `json:"evidenceIds,omitempty"`
	EvidenceRefs       []memory.EvidenceRef `json:"evidenceRefs"`
	ContextRefs        []memory.ContextRef  `json:"contextRefs,omitempty"`
	MaterialLinks      []MaterialLink       `json:"materialLinks,omitempty"`
	IntentLinks        []IntentLink         `json:"intentLinks,omitempty"`
	RetiredIntentLinks []IntentLink         `json:"retiredIntentLinks,omitempty"`
	SourceIDs          []string             `json:"sourceIds,omitempty"`
}

type MaterialLink struct {
	Relation    string `json:"relation"`
	MaterialRef string `json:"materialRef"`
}

type IntentLink struct {
	Relation      string               `json:"relation"`
	TargetRef     string               `json:"targetRef"`
	EvidenceRefs  []memory.EvidenceRef `json:"evidenceRefs"`
	ExpectedState string               `json:"-"`
}

type IntentTarget struct {
	Key   string `json:"key"`
	Value string `json:"value"`
}

type LinkRequest struct {
	Candidate      Candidate         `json:"candidate"`
	Targets        []IntentTarget    `json:"targets"`
	Existing       []IntentLink      `json:"existing,omitempty"`
	ExpectedStates map[string]string `json:"-"`
}

type LinkResult struct {
	SourceKey   string       `json:"sourceKey"`
	Links       []IntentLink `json:"links"`
	Retirements []IntentLink `json:"retirements"`
}

type Model interface {
	ContextTokens() int
	Extract(context.Context, string) ([]Candidate, error)
	Consolidate(context.Context, []Candidate) (Candidate, error)
}

type Linker interface {
	Link(context.Context, []LinkRequest) ([]LinkResult, error)
}

func ReadTranscript(path string) ([]Event, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("read transcript: %w", err)
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64<<10), maximumMessageBytes)
	var messages []Event
	sourceIDs := map[string]string{}
	choiceCalls := map[string]string{}
	for line := 1; scanner.Scan(); line++ {
		if strings.TrimSpace(scanner.Text()) == "" {
			continue
		}
		var record map[string]any
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			return nil, fmt.Errorf("read transcript: invalid JSON on line %d: %w", line, err)
		}
		decoded := decodeMessage(record)
		if decoded.role == "" || len(decoded.parts) == 0 {
			continue
		}
		if decoded.kind == "tool_result" {
			if target := choiceCalls[decoded.callID]; target != "" {
				decoded.role, decoded.kind, decoded.replyToID = "user", "choice", target
				for index := range decoded.parts {
					if decoded.parts[index].Kind == "tool_result" {
						decoded.parts[index].Kind = "choice"
					}
				}
			} else {
				decoded.role = "tool"
			}
		}
		sequence := len(messages) + 1
		id := fmt.Sprintf("%s%06d", rolePrefix(decoded.role), sequence)
		for index := range decoded.parts {
			decoded.parts[index].ID = fmt.Sprintf("%sP%03d", id, index+1)
		}
		replyToID := decoded.replyToID
		if replyToID == "" {
			replyToID = sourceIDs[decoded.replySourceID]
		}
		if replyToID == "" && decoded.role == "user" && len(messages) > 0 && messages[len(messages)-1].Role == "assistant" {
			replyToID = messages[len(messages)-1].ID
		}
		message := Event{
			ID: id, Sequence: sequence, Role: decoded.role, Kind: decoded.kind,
			Text: joinPartText(decoded.parts), ReplyToID: replyToID, CallID: decoded.callID, Parts: decoded.parts,
		}
		messages = append(messages, message)
		if decoded.sourceID != "" {
			sourceIDs[decoded.sourceID] = id
		}
		if decoded.role == "assistant" {
			if decoded.choiceCall && decoded.callID != "" {
				choiceCalls[decoded.callID] = id
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read transcript: %w", err)
	}
	return messages, nil
}

func Propose(ctx context.Context, messages []Event, model Model, materialRefs []string) ([]Candidate, error) {
	if model == nil || model.ContextTokens() < 1024 {
		return nil, errors.New("reconcile transcript: valid model is required")
	}
	budget := model.ContextTokens()/2 - 512
	if budget < 128 {
		budget = 128
	}
	var candidates []Candidate
	sequence := 0
	allowedMaterials := set(materialRefs)
	for _, chunk := range chunks(messages, budget) {
		extracted, err := model.Extract(ctx, chunk.text)
		if err != nil {
			return nil, fmt.Errorf("reconcile transcript: extract: %w", err)
		}
		for _, candidate := range extracted {
			candidate.IntentLinks = nil // Intent link operations are accepted only from the bounded Pass B contract.
			candidate.RetiredIntentLinks = nil
			candidate.EvidenceRefs, err = groundEvidenceRefs(candidate.EvidenceRefs, chunk.userParts)
			if err != nil {
				continue
			}
			candidate.EvidenceIDs = evidenceIDs(candidate.EvidenceRefs)
			candidate.ContextRefs, err = groundContextRefs(candidate.ContextRefs, chunk.contextParts)
			if err != nil {
				continue
			}
			candidate.MaterialLinks = uniqueLinks(candidate.MaterialLinks)
			if err := validate(candidate, chunk.userIDs, chunk.contextParts, allowedMaterials); err != nil {
				if errors.Is(err, errInvalidCandidate) {
					continue
				}
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
			if errors.Is(err, errInvalidCandidate) {
				continue
			}
			return nil, err
		}
		candidate.ID = ""
		candidate.SourceIDs = nil
		result = append(result, candidate)
	}
	return result, nil
}

// Connect adds only grounded, bounded links to existing or same-batch Intents.
func Connect(ctx context.Context, candidates []Candidate, requests []LinkRequest, linker Linker) ([]Candidate, error) {
	if len(requests) == 0 {
		return candidates, nil
	}
	if linker == nil {
		return nil, errors.New("reconcile intents: linker is required")
	}
	byKey := make(map[string]int, len(candidates))
	allowed := make(map[string]map[string]bool, len(requests))
	existing := make(map[string]map[string]bool, len(requests))
	expected := make(map[string]map[string]string, len(requests))
	for index, candidate := range candidates {
		byKey[candidate.Key] = index
	}
	for _, request := range requests {
		index, found := byKey[request.Candidate.Key]
		if !found || candidates[index].Kind != memory.Decision || request.Candidate.Value != candidates[index].Value || len(request.Targets) == 0 {
			return nil, errors.New("reconcile intents: invalid link request")
		}
		if allowed[request.Candidate.Key] != nil {
			return nil, errors.New("reconcile intents: duplicate link request")
		}
		allowed[request.Candidate.Key] = map[string]bool{}
		for _, target := range request.Targets {
			if target.Key == "" || target.Key == request.Candidate.Key {
				return nil, errors.New("reconcile intents: invalid target")
			}
			allowed[request.Candidate.Key][target.Key] = true
		}
		existing[request.Candidate.Key] = map[string]bool{}
		expected[request.Candidate.Key] = request.ExpectedStates
		for _, link := range request.Existing {
			if !graph.IsIntentIntentRelation(link.Relation) || !allowed[request.Candidate.Key][link.TargetRef] {
				return nil, errors.New("reconcile intents: invalid existing link")
			}
			existing[request.Candidate.Key][link.Relation+"\x00"+link.TargetRef] = true
		}
	}
	results, err := linker.Link(ctx, requests)
	if err != nil {
		return nil, fmt.Errorf("reconcile intents: link: %w", err)
	}
	seenSources := map[string]bool{}
	for _, result := range results {
		index, found := byKey[result.SourceKey]
		if !found || allowed[result.SourceKey] == nil || seenSources[result.SourceKey] {
			continue
		}
		seenSources[result.SourceKey] = true
		availableEvidence := map[excerptKey]memory.EvidenceRef{}
		for _, ref := range candidates[index].EvidenceRefs {
			availableEvidence[excerpt(ref.MessageID, ref.PartID, ref.Quote)] = ref
		}
		links := make([]IntentLink, 0, len(result.Links))
		linkIndexes := map[string]int{}
		valid := true
		for _, link := range result.Links {
			if !graph.IsIntentIntentRelation(link.Relation) || !allowed[result.SourceKey][link.TargetRef] {
				valid = false
				break
			}
			link.EvidenceRefs, err = restoreEvidenceSubset(link.EvidenceRefs, availableEvidence)
			if err != nil {
				valid = false
				break
			}
			link.ExpectedState = expected[result.SourceKey][link.Relation+"\x00"+link.TargetRef]
			key := link.Relation + "\x00" + link.TargetRef
			if prior, found := linkIndexes[key]; found {
				links[prior].EvidenceRefs = mergeEvidenceRefs(links[prior].EvidenceRefs, link.EvidenceRefs)
				continue
			}
			linkIndexes[key] = len(links)
			links = append(links, link)
		}
		retirements := make([]IntentLink, 0, len(result.Retirements))
		retirementIndexes := map[string]int{}
		for _, link := range result.Retirements {
			key := link.Relation + "\x00" + link.TargetRef
			_, adding := linkIndexes[key]
			if !graph.IsIntentIntentRelation(link.Relation) || !existing[result.SourceKey][key] || adding {
				valid = false
				break
			}
			link.EvidenceRefs, err = restoreEvidenceSubset(link.EvidenceRefs, availableEvidence)
			if err != nil {
				valid = false
				break
			}
			link.ExpectedState = expected[result.SourceKey][key]
			if prior, found := retirementIndexes[key]; found {
				retirements[prior].EvidenceRefs = mergeEvidenceRefs(retirements[prior].EvidenceRefs, link.EvidenceRefs)
				continue
			}
			retirementIndexes[key] = len(retirements)
			retirements = append(retirements, link)
		}
		if !valid || len(links)+len(retirements) > 4 {
			continue
		}
		candidates[index].IntentLinks = links
		candidates[index].RetiredIntentLinks = retirements
	}
	return candidates, nil
}

type chunk struct {
	text         string
	userIDs      map[string]userEvidence
	userParts    map[string]contextPart
	contextParts map[string]contextPart
}

type userEvidence struct {
	sequence  int
	text      string
	kind      string
	replyToID string
}

type contextPart struct {
	messageID string
	sequence  int
	text      string
	startByte int
}

type transcriptRecord struct {
	text, messageID, partID, role, kind, replyToID, messageText string
	sequence                                                    int
	startByte                                                   int
	visibleText                                                 string
}

func chunks(messages []Event, tokenBudget int) []chunk {
	maximum := tokenBudget * 4
	var records []transcriptRecord
	for index, item := range messages {
		if item.Role != "user" && item.Role != "assistant" {
			continue
		}
		sequence := item.Sequence
		if sequence <= 0 {
			sequence = index + 1
		}
		parts := item.Parts
		if len(parts) == 0 && strings.TrimSpace(item.Text) != "" {
			parts = []Part{{ID: item.ID + "P001", Kind: "text", Text: item.Text}}
		}
		for _, part := range parts {
			if strings.TrimSpace(part.Text) == "" || part.Kind != "text" && part.Kind != "choice" {
				continue
			}
			header := fmt.Sprintf("[%s %s %s %s]\n", item.ID, part.ID, strings.ToUpper(item.Role), strings.ToUpper(part.Kind))
			startByte := 0
			for _, piece := range split(part.Text, maximum-len(header)-2) {
				records = append(records, transcriptRecord{
					text: header + piece, messageID: item.ID, partID: part.ID, role: item.Role,
					kind: item.Kind, replyToID: item.ReplyToID, messageText: item.Text,
					sequence: sequence, visibleText: piece, startByte: startByte,
				})
				startByte += len(piece)
			}
		}
	}
	var result []chunk
	var current []string
	users := map[string]userEvidence{}
	userParts := map[string]contextPart{}
	contextParts := map[string]contextPart{}
	flush := func() {
		if len(current) > 0 {
			result = append(result, chunk{text: strings.Join(current, "\n\n"), userIDs: users, userParts: userParts, contextParts: contextParts})
			current = nil
			users = map[string]userEvidence{}
			userParts = map[string]contextPart{}
			contextParts = map[string]contextPart{}
		}
	}
	for _, record := range records {
		separator := 0
		if len(current) > 0 {
			separator = 2
		}
		size := separator + len(record.text)
		for _, value := range current {
			size += len(value)
		}
		if len(current) > 0 && size > maximum {
			flush()
		}
		current = append(current, record.text)
		if record.role == "user" {
			users[record.messageID] = userEvidence{sequence: record.sequence, text: record.messageText, kind: record.kind, replyToID: record.replyToID}
			userParts[record.partID] = contextPart{messageID: record.messageID, sequence: record.sequence, text: record.visibleText, startByte: record.startByte}
		} else {
			contextParts[record.partID] = contextPart{messageID: record.messageID, sequence: record.sequence, text: record.visibleText, startByte: record.startByte}
		}
	}
	flush()
	return replyContextChunks(result, messages, maximum)
}

func replyContextChunks(result []chunk, messages []Event, maximum int) []chunk {
	byID := make(map[string]Event, len(messages))
	for _, message := range messages {
		byID[message.ID] = message
	}
	for _, user := range messages {
		assistant, found := byID[user.ReplyToID]
		if user.Role != "user" || !found || assistant.Role != "assistant" {
			continue
		}
		userText := renderMessage(user)
		// ponytail: adoption replies are normally short; pair long user replies only if real transcripts need it.
		if userText == "" || len(userText) > maximum/2 {
			continue
		}
		parts := assistant.Parts
		if len(parts) == 0 && strings.TrimSpace(assistant.Text) != "" {
			parts = []Part{{ID: assistant.ID + "P001", Kind: "text", Text: assistant.Text}}
		}
		for _, part := range parts {
			if strings.TrimSpace(part.Text) == "" || part.Kind != "text" && part.Kind != "choice" || chunkContains(result, user.ID, part.ID, part.Text) {
				continue
			}
			header := fmt.Sprintf("[%s %s ASSISTANT %s]\n", assistant.ID, part.ID, strings.ToUpper(part.Kind))
			room := maximum - len(header) - len(userText) - 2
			if room < 128 {
				continue
			}
			userSequence, assistantSequence := user.Sequence, assistant.Sequence
			if userSequence <= 0 {
				userSequence = indexOfMessage(messages, user.ID) + 1
			}
			if assistantSequence <= 0 {
				assistantSequence = indexOfMessage(messages, assistant.ID) + 1
			}
			startByte := 0
			for _, piece := range split(part.Text, room) {
				userParts := map[string]contextPart{}
				for _, userPart := range user.Parts {
					if userPart.Kind == "text" || userPart.Kind == "choice" {
						userParts[userPart.ID] = contextPart{messageID: user.ID, sequence: userSequence, text: userPart.Text}
					}
				}
				if len(userParts) == 0 && strings.TrimSpace(user.Text) != "" {
					userParts[user.ID+"P001"] = contextPart{messageID: user.ID, sequence: userSequence, text: user.Text}
				}
				result = append(result, chunk{
					text: header + piece + "\n\n" + userText,
					userIDs: map[string]userEvidence{user.ID: {
						sequence: userSequence, text: user.Text, kind: user.Kind, replyToID: user.ReplyToID,
					}},
					userParts:    userParts,
					contextParts: map[string]contextPart{part.ID: {messageID: assistant.ID, sequence: assistantSequence, text: piece, startByte: startByte}},
				})
				startByte += len(piece)
			}
		}
	}
	return result
}

func renderMessage(message Event) string {
	parts := message.Parts
	if len(parts) == 0 && strings.TrimSpace(message.Text) != "" {
		parts = []Part{{ID: message.ID + "P001", Kind: "text", Text: message.Text}}
	}
	var records []string
	for _, part := range parts {
		if strings.TrimSpace(part.Text) == "" || part.Kind != "text" && part.Kind != "choice" {
			continue
		}
		records = append(records, fmt.Sprintf("[%s %s %s %s]\n%s", message.ID, part.ID, strings.ToUpper(message.Role), strings.ToUpper(part.Kind), part.Text))
	}
	return strings.Join(records, "\n\n")
}

func chunkContains(values []chunk, userID, partID, fullText string) bool {
	for _, value := range values {
		if value.userIDs[userID].sequence > 0 {
			if part, found := value.contextParts[partID]; found && part.text == fullText {
				return true
			}
		}
	}
	return false
}

func indexOfMessage(messages []Event, id string) int {
	for index, message := range messages {
		if message.ID == id {
			return index
		}
	}
	return -1
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
			allowedEvidenceRefs := map[excerptKey]memory.EvidenceRef{}
			allowedSources := map[string]bool{}
			allowedLinks := map[MaterialLink]bool{}
			allowedContext := map[excerptKey]memory.ContextRef{}
			for _, item := range pair {
				allowedSources[item.ID] = true
				for _, evidence := range item.EvidenceIDs {
					allowedEvidence[evidence] = true
				}
				for _, ref := range item.EvidenceRefs {
					allowedEvidenceRefs[excerpt(ref.MessageID, ref.PartID, ref.Quote)] = ref
				}
				for _, ref := range item.ContextRefs {
					allowedContext[excerpt(ref.MessageID, ref.PartID, ref.Quote)] = ref
				}
				for _, link := range item.MaterialLinks {
					allowedLinks[link] = true
				}
			}
			candidate.EvidenceRefs, err = restoreEvidenceRefs(candidate.EvidenceRefs, allowedEvidenceRefs)
			if err != nil {
				return Candidate{}, err
			}
			candidate.EvidenceIDs = evidenceIDs(candidate.EvidenceRefs)
			candidate.ContextRefs, err = restoreContextRefs(candidate.ContextRefs, allowedContext)
			if err != nil {
				return Candidate{}, err
			}
			candidate.MaterialLinks = uniqueLinks(candidate.MaterialLinks)
			candidate.IntentLinks = nil
			if err := validateCore(candidate, nil); err != nil {
				return Candidate{}, err
			}
			if !sameSet(candidate.EvidenceIDs, allowedEvidence) {
				return Candidate{}, invalidCandidate("consolidation omitted or invented evidence", nil)
			}
			for _, link := range candidate.MaterialLinks {
				if !allowedLinks[link] {
					return Candidate{}, invalidCandidate("consolidation invented material link", nil)
				}
			}
			if len(candidate.MaterialLinks) != len(allowedLinks) {
				return Candidate{}, invalidCandidate("consolidation omitted material link", nil)
			}
			if !sameSet(candidate.SourceIDs, allowedSources) {
				return Candidate{}, invalidCandidate("consolidation omitted candidates", nil)
			}
			candidate.ID = fmt.Sprintf("R%s", pair[1].ID)
			next = append(next, candidate)
		}
		candidates = next
	}
	return candidates[0], nil
}

func validate(candidate Candidate, allowedEvidence map[string]userEvidence, allowedContext map[string]contextPart, allowedMaterials map[string]bool) error {
	if err := validateCore(candidate, allowedMaterials); err != nil {
		return err
	}
	for _, evidence := range candidate.EvidenceIDs {
		if allowedEvidence[evidence].sequence == 0 {
			return invalidCandidate("candidate cites non-user evidence", nil)
		}
	}
	if len(candidate.EvidenceRefs) > 16 {
		return invalidCandidate("candidate cites more than 16 user excerpts", nil)
	}
	explicitEvidence := false
	for _, evidence := range candidate.EvidenceIDs {
		user := allowedEvidence[evidence]
		explicitEvidence = explicitEvidence || user.kind == "choice" || !vagueApproval(user.text)
	}
	if !explicitEvidence {
		return invalidCandidate("candidate cites only ambiguous approval", nil)
	}
	if len(candidate.ContextRefs) > 16 {
		return invalidCandidate("candidate cites more than 16 assistant excerpts", nil)
	}
	for _, ref := range candidate.ContextRefs {
		part, found := allowedContext[ref.PartID]
		quote := strings.TrimSpace(ref.Quote)
		if !found || part.messageID != ref.MessageID {
			return invalidCandidate("candidate cites unavailable assistant context", nil)
		}
		if quote == "" || len([]rune(quote)) > 1024 || !strings.Contains(part.text, quote) {
			return invalidCandidate("candidate assistant quote is not an exact excerpt", nil)
		}
		approved := false
		for _, evidence := range candidate.EvidenceIDs {
			user := allowedEvidence[evidence]
			approved = approved || user.sequence > part.sequence && user.replyToID == part.messageID && (user.kind == "choice" || !vagueApproval(user.text))
		}
		if !approved {
			return invalidCandidate("candidate assistant context has no later user evidence", nil)
		}
	}
	return nil
}

func vagueApproval(value string) bool {
	value = strings.ToLower(strings.Trim(strings.TrimSpace(value), ".,!?'\"`~… \t\r\n"))
	switch value {
	case "좋아", "좋아요", "좋습니다", "네", "예", "응", "ㅇㅇ", "ㅇㅋ", "ok", "okay", "yes", "sure":
		return true
	}
	return false
}

func validateCore(candidate Candidate, allowedMaterials map[string]bool) error {
	value := candidate.Value
	if len([]rune(value)) > 4096 {
		return invalidCandidate("candidate value exceeds 4096 characters", nil)
	}
	if _, err := memory.New("validation", candidate.Key, candidate.Kind, &value, nil); err != nil {
		return invalidCandidate("candidate", err)
	}
	if len(candidate.EvidenceRefs) == 0 || len(candidate.EvidenceIDs) == 0 {
		return invalidCandidate("candidate has no user evidence", nil)
	}
	if candidate.Kind != memory.Decision && len(candidate.MaterialLinks) > 0 {
		return invalidCandidate("only intent candidates can link materials", nil)
	}
	for _, link := range candidate.MaterialLinks {
		if !graph.IsIntentMaterialRelation(link.Relation) {
			return invalidCandidate("candidate uses unsupported material relation", nil)
		}
		if allowedMaterials != nil && !allowedMaterials[link.MaterialRef] {
			return invalidCandidate("candidate cites unavailable material", nil)
		}
	}
	return nil
}

func invalidCandidate(message string, cause error) error {
	if cause != nil {
		return fmt.Errorf("%w: %s: %v", errInvalidCandidate, message, cause)
	}
	return fmt.Errorf("%w: %s", errInvalidCandidate, message)
}

func set(values []string) map[string]bool {
	result := make(map[string]bool, len(values))
	for _, value := range values {
		result[value] = true
	}
	return result
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

func uniqueLinks(values []MaterialLink) []MaterialLink {
	seen := map[MaterialLink]bool{}
	result := make([]MaterialLink, 0, len(values))
	for _, value := range values {
		if !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	return result
}

type excerptKey struct{ messageID, partID, quote string }

func excerpt(messageID, partID, quote string) excerptKey {
	return excerptKey{strings.TrimSpace(messageID), strings.TrimSpace(partID), strings.TrimSpace(quote)}
}

func groundEvidenceRefs(values []memory.EvidenceRef, parts map[string]contextPart) ([]memory.EvidenceRef, error) {
	result := make([]memory.EvidenceRef, 0, len(values))
	seen := map[excerptKey]bool{}
	for _, value := range values {
		key := excerpt(value.MessageID, value.PartID, value.Quote)
		part, found := parts[key.partID]
		start, end, err := locateExcerpt(key, part, found)
		if err != nil {
			return nil, err
		}
		if !seen[key] {
			seen[key] = true
			result = append(result, memory.EvidenceRef{MessageID: key.messageID, PartID: key.partID, Quote: key.quote, StartByte: start, EndByte: end})
		}
	}
	return result, nil
}

func groundContextRefs(values []memory.ContextRef, parts map[string]contextPart) ([]memory.ContextRef, error) {
	result := make([]memory.ContextRef, 0, len(values))
	seen := map[excerptKey]bool{}
	for _, value := range values {
		key := excerpt(value.MessageID, value.PartID, value.Quote)
		part, found := parts[key.partID]
		start, end, err := locateExcerpt(key, part, found)
		if err != nil {
			return nil, err
		}
		if !seen[key] {
			seen[key] = true
			result = append(result, memory.ContextRef{MessageID: key.messageID, PartID: key.partID, Quote: key.quote, StartByte: start, EndByte: end})
		}
	}
	return result, nil
}

func locateExcerpt(key excerptKey, part contextPart, found bool) (int, int, error) {
	if !found || part.messageID != key.messageID || key.quote == "" || len([]rune(key.quote)) > 1024 {
		return 0, 0, invalidCandidate("candidate cites unavailable source excerpt", nil)
	}
	index := strings.Index(part.text, key.quote)
	if index < 0 || strings.Contains(part.text[index+len(key.quote):], key.quote) {
		return 0, 0, invalidCandidate("candidate source quote is missing or ambiguous", nil)
	}
	start := part.startByte + index
	return start, start + len(key.quote), nil
}

func evidenceIDs(values []memory.EvidenceRef) []string {
	result := make([]string, 0, len(values))
	for _, value := range values {
		result = append(result, value.MessageID)
	}
	return unique(result)
}

func restoreEvidenceRefs(values []memory.EvidenceRef, allowed map[excerptKey]memory.EvidenceRef) ([]memory.EvidenceRef, error) {
	result := make([]memory.EvidenceRef, 0, len(values))
	seen := map[excerptKey]bool{}
	for _, value := range values {
		key := excerpt(value.MessageID, value.PartID, value.Quote)
		grounded, found := allowed[key]
		if !found {
			return nil, invalidCandidate("consolidation invented user evidence", nil)
		}
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, grounded)
	}
	if len(result) != len(allowed) {
		return nil, invalidCandidate("consolidation omitted user evidence", nil)
	}
	return result, nil
}

func restoreEvidenceSubset(values []memory.EvidenceRef, allowed map[excerptKey]memory.EvidenceRef) ([]memory.EvidenceRef, error) {
	if len(values) == 0 {
		return nil, invalidCandidate("intent link has no user evidence", nil)
	}
	result := make([]memory.EvidenceRef, 0, len(values))
	seen := map[excerptKey]bool{}
	for _, value := range values {
		key := excerpt(value.MessageID, value.PartID, value.Quote)
		grounded, found := allowed[key]
		if !found {
			return nil, invalidCandidate("intent link invented user evidence", nil)
		}
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, grounded)
	}
	return result, nil
}

func mergeEvidenceRefs(left, right []memory.EvidenceRef) []memory.EvidenceRef {
	seen := map[excerptKey]bool{}
	result := make([]memory.EvidenceRef, 0, len(left)+len(right))
	for _, values := range [][]memory.EvidenceRef{left, right} {
		for _, value := range values {
			key := excerpt(value.MessageID, value.PartID, value.Quote)
			if !seen[key] {
				seen[key] = true
				result = append(result, value)
			}
		}
	}
	return result
}

func restoreContextRefs(values []memory.ContextRef, allowed map[excerptKey]memory.ContextRef) ([]memory.ContextRef, error) {
	result := make([]memory.ContextRef, 0, len(values))
	seen := map[excerptKey]bool{}
	for _, value := range values {
		key := excerpt(value.MessageID, value.PartID, value.Quote)
		grounded, found := allowed[key]
		if !found {
			return nil, invalidCandidate("consolidation invented assistant context", nil)
		}
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, grounded)
	}
	if len(result) != len(allowed) {
		return nil, invalidCandidate("consolidation omitted assistant context", nil)
	}
	return result, nil
}

type decodedMessage struct {
	role, kind, sourceID, replySourceID, replyToID, callID string
	parts                                                  []Part
	choiceCall                                             bool
}

func decodeMessage(record map[string]any) decodedMessage {
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
		value = strings.ToLower(value)
		if value == "user" || value == "assistant" || value == "system" || value == "tool" {
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
			case "system", "system_message":
				role = "system"
			case "tool", "tool_result", "function_call_output":
				role = "tool"
			}
		}
	}
	result := decodedMessage{role: role, kind: "message"}
	for index := len(candidates) - 1; index >= 0; index-- {
		candidate := candidates[index]
		if result.sourceID == "" {
			result.sourceID = firstString(candidate, "message_id", "uuid", "id")
		}
		if result.replySourceID == "" {
			result.replySourceID = firstString(candidate, "reply_to_id", "replyToId", "parent_id", "parent_uuid")
		}
		for _, field := range []string{"content", "text"} {
			if parts := decodeParts(candidate[field]); len(parts) > 0 {
				result.parts = parts
				break
			}
		}
		if len(result.parts) == 0 {
			result.parts = decodeParts(candidate)
		}
		if len(result.parts) > 0 {
			break
		}
	}
	for _, part := range result.parts {
		switch part.Kind {
		case "choice":
			result.kind = "choice"
		case "tool_call":
			if result.kind != "choice" {
				result.kind = "tool_call"
			}
			if result.callID == "" {
				result.callID = part.Ref
			}
			result.choiceCall = result.choiceCall || choiceTool(part.Name)
		case "tool_result":
			result.kind = "tool_result"
			if result.callID == "" {
				result.callID = part.Ref
			}
		case "attachment":
			if result.kind == "message" && joinPartText(result.parts) == "" {
				result.kind = "attachment"
			}
		}
	}
	if result.role == "" {
		switch result.kind {
		case "tool_call", "choice":
			result.role = "assistant"
		case "tool_result":
			result.role = "tool"
		}
	}
	return result
}

func decodeParts(value any) []Part {
	switch typed := value.(type) {
	case string:
		if text := strings.TrimSpace(typed); text != "" {
			return []Part{{Kind: "text", Text: text}}
		}
	case []any:
		var parts []Part
		for _, raw := range typed {
			parts = append(parts, decodeParts(raw)...)
		}
		return parts
	case map[string]any:
		kind := strings.ToLower(firstString(typed, "type"))
		switch kind {
		case "tool_use", "tool_call", "function_call":
			name := firstString(typed, "name")
			ref := firstString(typed, "id", "call_id")
			input := typed["input"]
			if input == nil {
				input = typed["arguments"]
			}
			parts := []Part{{Kind: "tool_call", Name: name, Ref: ref, Text: boundedText(textValue(input), 4096)}}
			if choiceTool(name) {
				parts = append(parts, choices(input)...)
			}
			return parts
		case "tool_result", "function_call_output":
			return []Part{{Kind: "tool_result", Ref: firstString(typed, "tool_use_id", "call_id", "id"), Text: boundedText(textValue(firstValue(typed, "content", "text", "output", "result")), 4096)}}
		case "image", "image_url", "document", "file", "attachment":
			return []Part{{Kind: "attachment", Name: firstString(typed, "name", "filename"), Ref: firstString(typed, "file_id", "url", "uri", "path", "id")}}
		case "text", "input_text", "output_text", "":
			if text := textValue(firstValue(typed, "text", "content", "message")); text != "" {
				return []Part{{Kind: "text", Text: text}}
			}
		default:
			if text := textValue(firstValue(typed, "text", "content")); text != "" {
				return []Part{{Kind: kind, Text: text, Name: firstString(typed, "name"), Ref: firstString(typed, "id", "url", "uri", "path")}}
			}
			return []Part{{Kind: kind, Name: firstString(typed, "name"), Ref: firstString(typed, "id", "url", "uri", "path")}}
		}
	}
	return nil
}

func choices(value any) []Part {
	var result []Part
	var walk func(any)
	walk = func(raw any) {
		switch typed := raw.(type) {
		case []any:
			for _, item := range typed {
				walk(item)
			}
		case map[string]any:
			if question := strings.TrimSpace(firstString(typed, "question", "header")); question != "" {
				result = append(result, Part{Kind: "text", Text: question})
			}
			if options, ok := typed["options"].([]any); ok {
				for _, rawOption := range options {
					option, ok := rawOption.(map[string]any)
					if !ok {
						if text, ok := rawOption.(string); ok && strings.TrimSpace(text) != "" {
							result = append(result, Part{Kind: "choice", Text: strings.TrimSpace(text)})
						}
						continue
					}
					label := firstString(option, "label", "name", "value")
					description := firstString(option, "description")
					text := strings.TrimSpace(strings.TrimSpace(label) + " — " + strings.TrimSpace(description))
					text = strings.TrimSuffix(text, " —")
					if text != "" {
						result = append(result, Part{Kind: "choice", Text: text})
					}
				}
			}
			if questions, found := typed["questions"]; found {
				walk(questions)
			}
		}
	}
	walk(value)
	return result
}

func textValue(value any) string {
	switch typed := value.(type) {
	case string:
		return strings.TrimSpace(typed)
	case []any:
		var values []string
		for _, item := range typed {
			if text := textValue(item); text != "" {
				values = append(values, text)
			}
		}
		return strings.Join(values, "\n")
	case map[string]any:
		var values []string
		for _, key := range []string{"text", "content", "message", "answer", "answers", "selected", "value", "result", "output", "query", "command", "prompt", "path", "url", "uri"} {
			if text := textValue(typed[key]); text != "" {
				values = append(values, text)
			}
		}
		return strings.Join(values, "\n")
	}
	return ""
}

func joinPartText(parts []Part) string {
	var values []string
	for _, part := range parts {
		if text := strings.TrimSpace(part.Text); text != "" {
			values = append(values, text)
		}
	}
	return strings.Join(values, "\n")
}

func firstString(value map[string]any, keys ...string) string {
	for _, key := range keys {
		if text, ok := value[key].(string); ok && strings.TrimSpace(text) != "" {
			return strings.TrimSpace(text)
		}
	}
	return ""
}

func firstValue(value map[string]any, keys ...string) any {
	for _, key := range keys {
		if value[key] != nil {
			return value[key]
		}
	}
	return nil
}

func choiceTool(name string) bool {
	name = strings.NewReplacer("_", "", "-", "").Replace(strings.ToLower(name))
	return name == "askuserquestion" || name == "requestuserinput"
}

func rolePrefix(role string) string {
	switch role {
	case "user":
		return "U"
	case "assistant":
		return "A"
	case "tool":
		return "T"
	case "system":
		return "S"
	default:
		return "E"
	}
}

func boundedText(value string, maximum int) string {
	value = strings.TrimSpace(value)
	if len(value) <= maximum {
		return value
	}
	end := maximum
	for end > 0 && !utf8.RuneStart(value[end]) {
		end--
	}
	return value[:end]
}
