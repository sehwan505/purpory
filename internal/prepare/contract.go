package prepare

import (
	"errors"
	"strings"
	"unicode/utf8"
)

const (
	SchemaVersion   = 1
	MinTokenBudget  = 128
	MaxTokenBudget  = 32_768
	MaxMessageChars = 1_048_576
	MaxQueryChars   = 4_096
)

type Request struct {
	Message          string `json:"message"`
	SessionID        string `json:"sessionId"`
	ProjectID        string `json:"project"`
	WorkingDirectory string `json:"workingDirectory"`
	TokenBudget      int    `json:"tokenBudget"`
}

type Result struct {
	SchemaVersion int      `json:"schemaVersion"`
	Action        string   `json:"action"`
	Hints         *HintMap `json:"hints,omitempty"`
}

type HintMap struct {
	Nodes []HintNode `json:"nodes"`
	Edges []HintEdge `json:"edges"`
}

type HintNode struct {
	ID         string `json:"id"`
	Path       string `json:"path,omitempty"`
	Label      string `json:"label"`
	Kind       string `json:"kind"`
	Subkind    string `json:"subkind,omitempty"`
	State      string `json:"state,omitempty"`
	Source     string `json:"source,omitempty"`
	Match      string `json:"match"`
	Provenance string `json:"provenance,omitempty"`
}

type HintEdge struct {
	SourceID string `json:"sourceId"`
	TargetID string `json:"targetId"`
	Relation string `json:"relation"`
}

type NavigationEvent struct {
	ID           int64  `json:"id"`
	Action       string `json:"action"`
	SourceNodeID string `json:"sourceNodeId,omitempty"`
	TargetNodeID string `json:"targetNodeId,omitempty"`
	CreatedAt    string `json:"createdAt,omitempty"`
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
	return value, nil
}
