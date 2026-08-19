// Package graph defines the structural graph shared by indexing and retrieval.
package graph

import "strings"

const (
	KindIntent    = "intent"
	KindMaterial  = "material"
	KindKnowledge = "knowledge"
	KindReference = "reference"

	OwnerObserved = "observed"
	OwnerDurable  = "durable"
	StateActive   = "active"
	StateMissing  = "missing"

	RelationAppliesTo      = "applies_to"
	RelationRealizedBy     = "realized_by"
	RelationVerifiedBy     = "verified_by"
	RelationContradictedBy = "contradicted_by"
)

// ReferenceID is stable within a project and keeps graph identities inspectable.
func ReferenceID(kind, ref string) string { return kind + ":" + ref }

func IsIntentMaterialRelation(value string) bool {
	return value == RelationAppliesTo || value == RelationRealizedBy ||
		value == RelationVerifiedBy || value == RelationContradictedBy
}

type Node struct {
	ID          string `json:"id"`
	Path        string `json:"path,omitempty"`
	Label       string `json:"label"`
	Kind        string `json:"kind"`
	Subkind     string `json:"subkind,omitempty"`
	Ref         string `json:"ref"`
	Owner       string `json:"owner"`
	State       string `json:"state"`
	Provenance  string `json:"provenance,omitempty"`
	MaterialID  string `json:"materialId,omitempty"`
	MaterialURI string `json:"materialUri,omitempty"`
	Locator     string `json:"locator,omitempty"`
	Content     string `json:"content,omitempty"`
}

// TopicPath projects a durable memory key as a kind-independent path. Observed
// nodes keep their stable Material address instead of inventing a taxonomy.
func TopicPath(node Node) string {
	if node.Owner == OwnerDurable {
		return strings.TrimPrefix(node.Ref, node.Kind+".")
	}
	if node.MaterialURI != "" && node.Locator != "" {
		return node.MaterialURI + "#" + node.Locator
	}
	return node.MaterialURI
}

type Edge struct {
	SourceID   string `json:"sourceId"`
	TargetID   string `json:"targetId"`
	Relation   string `json:"relation"`
	Owner      string `json:"owner"`
	Provenance string `json:"provenance,omitempty"`
}

// Claim is an extracted relationship before project-wide resolution.
type Claim struct {
	MaterialID  string `json:"materialId"`
	SourceID    string `json:"sourceId"`
	TargetID    string `json:"targetId,omitempty"`
	TargetLabel string `json:"target,omitempty"`
	Relation    string `json:"relation"`
}

// Link is a requested durable connection. Stores resolve it into physical nodes
// and an edge before commit.
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
	Paths       []string     `json:"paths,omitempty"`
}

type Path struct {
	Nodes      []Node   `json:"nodes"`
	Edges      []Edge   `json:"edges"`
	TopicPaths []string `json:"topicPaths,omitempty"`
}
