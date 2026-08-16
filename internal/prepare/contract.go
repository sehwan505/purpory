package prepare

import (
	"context"
	"errors"
	"strings"
	"unicode/utf8"
)

const (
	SchemaVersion     = 1
	ContextVersion    = 2
	PromptVersion     = "purpory-gate-v5"
	MinTokenBudget    = 128
	MaxTokenBudget    = 32_768
	MaxMessageChars   = 1_048_576
	MaxQueryChars     = 4_096
	MaxDirectEvidence = 2
	MaxAwarenessHints = 6
)

type Request struct {
	Message          string        `json:"message"`
	SessionID        string        `json:"sessionId"`
	ProjectID        string        `json:"project"`
	WorkingDirectory string        `json:"workingDirectory"`
	ActivePaths      []string      `json:"activePaths"`
	TokenBudget      int           `json:"tokenBudget"`
	RetainInput      bool          `json:"retainInput"`
	PriorKeys        []string      `json:"previousDeliveries"`
	Catalog          Catalog       `json:"contextCatalog"`
	Orientation      []Orientation `json:"orientation"`
}

type Orientation struct {
	Key     string `json:"key"`
	Label   string `json:"label"`
	Kind    string `json:"kind"`
	Source  string `json:"source,omitempty"`
	Preview string `json:"preview,omitempty"`
}

type Provider interface {
	Propose(context.Context, Request) (ProviderResult, error)
}

type ProviderResult struct {
	Proposal  Proposal
	ModelID   string
	Revision  string
	LatencyMS int
}

type Proposal struct {
	Action        string   `json:"action"`
	Query         *string  `json:"query"`
	Scopes        []string `json:"scopes"`
	Keywords      []string `json:"keywords"`
	ReasonCode    string   `json:"reasonCode"`
	Clarification *string  `json:"clarification"`
}

type Model struct {
	ID        *string `json:"id"`
	Revision  *string `json:"revision"`
	LatencyMS *int    `json:"latencyMs"`
}

type Candidate struct {
	NodeID        string   `json:"nodeId"`
	Key           string   `json:"key"`
	Namespace     string   `json:"namespace"`
	Label         string   `json:"label"`
	Kind          string   `json:"kind"`
	Origin        string   `json:"origin"`
	Source        string   `json:"source,omitempty"`
	Content       string   `json:"-"`
	Mode          string   `json:"-"`
	UpdatedAt     int64    `json:"-"`
	SelectedCount int      `json:"-"`
	ExpandedCount int      `json:"-"`
	Score         float64  `json:"score"`
	Signals       []string `json:"signals"`
}

type Delivery struct {
	NodeID          string   `json:"nodeId"`
	Key             string   `json:"key"`
	Kind            string   `json:"kind"`
	Origin          string   `json:"origin"`
	Mode            string   `json:"mode"`
	Truncated       bool     `json:"truncated"`
	Score           float64  `json:"score"`
	Signals         []string `json:"signals"`
	EstimatedTokens int      `json:"estimatedTokens"`
	Hash            string   `json:"valueHash"`
	Rendered        string   `json:"rendered"`
}

type Omitted struct {
	NodeID          string `json:"nodeId,omitempty"`
	Key             string `json:"key,omitempty"`
	Reason          string `json:"reason"`
	EstimatedTokens int    `json:"estimatedTokens,omitempty"`
}

type Awareness struct {
	NodeID    string  `json:"nodeId"`
	Key       string  `json:"key"`
	Namespace string  `json:"namespace"`
	Label     string  `json:"label"`
	Kind      string  `json:"kind"`
	Source    string  `json:"source,omitempty"`
	Reason    string  `json:"reason"`
	Relation  *string `json:"relation"`
}

type Counts struct {
	Human        int `json:"human"`
	Nodes        int `json:"material"`
	Resource     int `json:"resource"`
	PriorCount   int `json:"previousDeliveries"`
	OpenRequests int `json:"openRequests"`
}

type NamespaceCount struct {
	Name  string `json:"name"`
	Count int    `json:"count"`
}

type Catalog struct {
	SchemaVersion   int              `json:"schemaVersion"`
	ProjectID       string           `json:"project"`
	Counts          Counts           `json:"counts"`
	TopicNamespaces []NamespaceCount `json:"topicNamespaces"`
	NodeKinds       []NamespaceCount `json:"materialTypes"`
}

type Search struct {
	Query      string      `json:"query"`
	Scopes     []string    `json:"scopes"`
	Terms      []string    `json:"terms"`
	Candidates []Candidate `json:"candidates"`
}

type Context struct {
	Catalog         Catalog `json:"manifest"`
	Search          *Search `json:"search"`
	Rendered        string  `json:"rendered"`
	EstimatedTokens int     `json:"estimatedTokens"`
	Hash            *string `json:"valueHash"`
}

type Result struct {
	SchemaVersion int         `json:"schemaVersion"`
	DecisionID    int64       `json:"decisionId"`
	Action        string      `json:"action"`
	Proposal      Proposal    `json:"proposal"`
	Deliveries    []Delivery  `json:"delivery"`
	Omitted       []Omitted   `json:"omitted"`
	RequestID     *int64      `json:"requestId"`
	Clarification *string     `json:"clarification"`
	Model         Model       `json:"model"`
	Fallback      *string     `json:"fallback"`
	Awareness     []Awareness `json:"awareness"`
	Context       Context     `json:"context"`
}

type DecisionRecord struct {
	ProjectID  string
	SessionID  string
	InputHash  string
	InputText  *string
	Proposal   Proposal
	Action     string
	Deliveries []Delivery
	RequestID  *int64
	Model      Model
	Fallback   *string
}

type ContextRequest struct {
	ID          int64   `json:"id"`
	SessionID   string  `json:"sessionId"`
	ProjectID   string  `json:"project"`
	Need        string  `json:"need"`
	Status      string  `json:"status"`
	ResolvedKey *string `json:"resolvedKey"`
	CreatedAt   string  `json:"createdAt"`
	ResolvedAt  string  `json:"resolvedAt,omitempty"`
}

type Feedback struct {
	DecisionID     int64    `json:"decisionId"`
	Verdict        string   `json:"verdict"`
	ExpectedAction *string  `json:"expectedAction"`
	ExpectedKeys   []string `json:"expectedKeys"`
	Note           *string  `json:"note"`
	CreatedAt      string   `json:"createdAt,omitempty"`
}

type Decision struct {
	ID            int64      `json:"id"`
	SessionID     string     `json:"sessionId"`
	ProjectID     string     `json:"project"`
	InputHash     string     `json:"inputHash"`
	InputText     *string    `json:"inputText"`
	Proposal      Proposal   `json:"proposal"`
	FinalAction   string     `json:"finalAction"`
	Deliveries    []Delivery `json:"delivery"`
	RequestID     *int64     `json:"requestId"`
	ModelID       *string    `json:"modelId"`
	ModelRevision *string    `json:"modelRevision"`
	PromptVersion string     `json:"promptVersion"`
	LatencyMS     *int       `json:"latencyMs"`
	Fallback      *string    `json:"fallbackReason"`
	CreatedAt     string     `json:"createdAt"`
	Feedback      *Feedback  `json:"feedback,omitempty"`
}

func ValidateRequest(value Request) (Request, error) {
	value.Message = strings.TrimSpace(value.Message)
	value.SessionID = strings.TrimSpace(value.SessionID)
	value.ProjectID = strings.TrimSpace(value.ProjectID)
	value.WorkingDirectory = strings.TrimSpace(value.WorkingDirectory)
	if value.Message == "" || value.SessionID == "" || value.ProjectID == "" || value.WorkingDirectory == "" {
		return Request{}, errors.New("prepare context: message, session, project, and working directory are required")
	}
	if utf8.RuneCountInString(value.Message) > MaxMessageChars {
		return Request{}, errors.New("prepare context: message exceeds 1048576 characters")
	}
	if len(value.SessionID) > 255 || len(value.ProjectID) > 255 || len(value.WorkingDirectory) > 1024 {
		return Request{}, errors.New("prepare context: session, project, or working directory is too long")
	}
	if value.TokenBudget < MinTokenBudget || value.TokenBudget > MaxTokenBudget {
		return Request{}, errors.New("prepare context: token budget must be between 128 and 32768")
	}
	if len(value.ActivePaths) > 32 {
		return Request{}, errors.New("prepare context: active paths cannot contain more than 32 items")
	}
	seen := map[string]bool{}
	paths := value.ActivePaths[:0]
	for _, path := range value.ActivePaths {
		path = strings.TrimSpace(path)
		if path == "" {
			continue
		}
		if len(path) > 1024 {
			return Request{}, errors.New("prepare context: active path exceeds 1024 characters")
		}
		if !seen[path] {
			seen[path] = true
			paths = append(paths, path)
		}
	}
	value.ActivePaths = paths
	return value, nil
}

func ValidateProposal(value Proposal) (Proposal, error) {
	value.Action = strings.ToLower(strings.TrimSpace(value.Action))
	if value.Action != "skip" && value.Action != "search" && value.Action != "ask" {
		return Proposal{}, errors.New("prepare context: proposal action must be skip, search, or ask")
	}
	if value.Query != nil {
		query := strings.TrimSpace(*value.Query)
		if query == "" || utf8.RuneCountInString(query) > MaxQueryChars {
			return Proposal{}, errors.New("prepare context: proposal query is invalid")
		}
		value.Query = &query
	}
	if value.Action == "search" && value.Query == nil {
		return Proposal{}, errors.New("prepare context: search proposal requires a query")
	}
	if value.Action == "skip" && value.Query != nil {
		return Proposal{}, errors.New("prepare context: skip proposal cannot include a query")
	}
	if value.Action == "ask" && (value.Clarification == nil || strings.TrimSpace(*value.Clarification) == "") {
		return Proposal{}, errors.New("prepare context: ask proposal requires clarification")
	}
	if value.Clarification != nil {
		clarification := strings.TrimSpace(*value.Clarification)
		if utf8.RuneCountInString(clarification) > 2_048 {
			return Proposal{}, errors.New("prepare context: proposal clarification is too long")
		}
		value.Clarification = &clarification
	}
	allowedScopes := map[string]bool{"human": true, "resource": true, "material": true, "session": true}
	seen := map[string]bool{}
	var scopes []string
	for _, scope := range value.Scopes {
		scope = strings.ToLower(strings.TrimSpace(scope))
		if scope == "code" {
			scope = "material"
		}
		if !allowedScopes[scope] {
			return Proposal{}, errors.New("prepare context: unsupported proposal scope")
		}
		if !seen[scope] {
			seen[scope] = true
			scopes = append(scopes, scope)
		}
	}
	value.Scopes = scopes
	if value.Scopes == nil {
		value.Scopes = []string{}
	}
	if len(value.Keywords) > 8 {
		return Proposal{}, errors.New("prepare context: proposal cannot contain more than 8 keywords")
	}
	seen = map[string]bool{}
	keywords := make([]string, 0, len(value.Keywords))
	for _, keyword := range value.Keywords {
		keyword = strings.TrimSpace(keyword)
		if keyword == "" || utf8.RuneCountInString(keyword) > 128 {
			return Proposal{}, errors.New("prepare context: proposal keyword is invalid")
		}
		if !seen[keyword] {
			seen[keyword] = true
			keywords = append(keywords, keyword)
		}
	}
	value.Keywords = keywords
	allowedReasons := map[string]bool{
		"SELF_CONTAINED": true, "CONTEXT_SEARCH_REQUIRED": true, "PRIOR_DECISION_REFERENCED": true,
		"PROJECT_CONTEXT_REQUIRED": true, "SESSION_HISTORY_REQUIRED": true, "CODE_CONTEXT_REQUIRED": true,
		"USER_INPUT_REQUIRED": true, "AMBIGUOUS_REQUEST": true, "GATE_UNAVAILABLE": true,
	}
	value.ReasonCode = strings.ToUpper(strings.TrimSpace(value.ReasonCode))
	if !allowedReasons[value.ReasonCode] {
		return Proposal{}, errors.New("prepare context: proposal reason code is invalid")
	}
	return value, nil
}
