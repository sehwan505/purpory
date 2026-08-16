// Package graph defines the structural graph shared by indexing and retrieval.
package graph

type Node struct {
	ID          string `json:"id"`
	Label       string `json:"label"`
	Kind        string `json:"kind"`
	MaterialID  string `json:"materialId,omitempty"`
	MaterialURI string `json:"materialUri,omitempty"`
	Locator     string `json:"locator,omitempty"`
	Content     string `json:"content,omitempty"`
}

type Edge struct {
	SourceID string `json:"sourceId"`
	TargetID string `json:"targetId"`
	Relation string `json:"relation"`
}

// Claim is an extracted relationship before project-wide resolution.
type Claim struct {
	MaterialID  string `json:"materialId"`
	SourceID    string `json:"sourceId"`
	TargetID    string `json:"targetId,omitempty"`
	TargetLabel string `json:"target,omitempty"`
	Relation    string `json:"relation"`
}

// Link is a durable connection between intent and real project evidence.
// References remain valid records even while a target is temporarily absent.
type Link struct {
	SourceKind string `json:"sourceKind"`
	SourceRef  string `json:"sourceRef"`
	Relation   string `json:"relation"`
	TargetKind string `json:"targetKind"`
	TargetRef  string `json:"targetRef"`
}

type Connection struct {
	Direction string `json:"direction"`
	Relation  string `json:"relation"`
	Node      Node   `json:"node"`
}

type Explanation struct {
	Node        Node         `json:"node"`
	Connections []Connection `json:"connections"`
}

type Path struct {
	Nodes []Node `json:"nodes"`
	Edges []Edge `json:"edges"`
}
