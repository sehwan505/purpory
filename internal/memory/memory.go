// Package memory defines durable project memory and its validation rules.
package memory

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"regexp"
	"strings"
)

type Kind string

const (
	Note      Kind = "note"
	Decision  Kind = "decision"
	Reference Kind = "reference"
)

var keyPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)*$`)

type Memory struct {
	ProjectID string  `json:"projectId"`
	Key       string  `json:"key"`
	Kind      Kind    `json:"kind"`
	Value     *string `json:"value,omitempty"`
	Source    *string `json:"source,omitempty"`
	Hash      string  `json:"hash"`
	UpdatedAt string  `json:"updatedAt"`
}

type Version struct {
	ID        int64   `json:"id"`
	Key       string  `json:"key"`
	Kind      Kind    `json:"kind"`
	Value     *string `json:"value,omitempty"`
	Source    *string `json:"source,omitempty"`
	Hash      string  `json:"hash"`
	CreatedAt string  `json:"createdAt"`
}

type ReconcileEvent struct {
	SessionID   string `json:"sessionId"`
	ChangesJSON string `json:"changes"`
	OccurredAt  string `json:"occurredAt"`
}

type Review struct {
	ID              int64  `json:"id"`
	ProjectID       string `json:"projectId"`
	Key             string `json:"key"`
	Status          string `json:"status"`
	SourceType      string `json:"sourceType"`
	SourceID        string `json:"sourceId"`
	ContentHash     string `json:"contentHash"`
	Reason          string `json:"reason"`
	Outcome         string `json:"outcome,omitempty"`
	ResultVersionID *int64 `json:"resultVersionId,omitempty"`
	CreatedAt       string `json:"createdAt"`
	ResolvedAt      string `json:"resolvedAt,omitempty"`
}

type Usage struct {
	SelectedCount int    `json:"selectedCount"`
	ExpandedCount int    `json:"expandedCount"`
	LastSelected  string `json:"lastSelectedAt,omitempty"`
	LastExpanded  string `json:"lastExpandedAt,omitempty"`
}

type BatchChange struct {
	Key             string  `json:"key"`
	Kind            Kind    `json:"kind"`
	Value           *string `json:"value,omitempty"`
	Source          *string `json:"source,omitempty"`
	ExpectedHash    *string `json:"expectedHash"`
	ExpectedHashSet bool    `json:"-"`
}

func (c *BatchChange) UnmarshalJSON(content []byte) error {
	type raw BatchChange
	var value raw
	if err := json.Unmarshal(content, &value); err != nil {
		return err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(content, &fields); err != nil {
		return err
	}
	*c = BatchChange(value)
	_, c.ExpectedHashSet = fields["expectedHash"]
	return nil
}

type BatchItem struct {
	Key          string  `json:"key"`
	Action       string  `json:"action"`
	CurrentHash  *string `json:"currentHash"`
	ProposedHash string  `json:"proposedHash"`
	ExpectedHash *string `json:"expectedHash"`
	VersionID    int64   `json:"versionId,omitempty"`
}

type BatchResult struct {
	Applied   bool        `json:"applied"`
	ProjectID string      `json:"projectId"`
	Changes   []BatchItem `json:"changes"`
}

func New(projectID, key string, kind Kind, value, source *string) (Memory, error) {
	projectID = strings.TrimSpace(projectID)
	var err error
	key, err = ValidateKey(key)
	if projectID == "" {
		return Memory{}, errors.New("validate memory: project ID is empty")
	}
	if err != nil {
		return Memory{}, err
	}
	if kind != Note && kind != Decision && kind != Reference {
		return Memory{}, errors.New("validate memory: unsupported kind")
	}
	if (value == nil) == (source == nil) {
		return Memory{}, errors.New("validate memory: exactly one of value or source is required")
	}
	if value != nil {
		normalized := strings.TrimSpace(*value)
		if normalized == "" {
			return Memory{}, errors.New("validate memory: value is empty")
		}
		value = &normalized
	}
	if source != nil {
		normalized := strings.TrimSpace(*source)
		if normalized == "" {
			return Memory{}, errors.New("validate memory: source is empty")
		}
		source = &normalized
	}
	payload := string(kind) + "\x00" + text(value) + "\x00" + text(source)
	sum := sha256.Sum256([]byte(payload))
	return Memory{
		ProjectID: projectID,
		Key:       key,
		Kind:      kind,
		Value:     value,
		Source:    source,
		Hash:      hex.EncodeToString(sum[:]),
	}, nil
}

func ValidateKey(key string) (string, error) {
	key = strings.TrimSpace(key)
	if len(key) > 255 || !keyPattern.MatchString(key) {
		return "", errors.New("validate memory: key must be a dot-separated address using letters, numbers, dashes, or underscores")
	}
	return key, nil
}

func text(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}
