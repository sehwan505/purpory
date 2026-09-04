package graph

import "testing"

func TestIntentIntentRelationsAreDefined(t *testing.T) {
	for _, relation := range []string{RelationRefines, RelationDependsOn, RelationConflictsWith} {
		if !IsIntentIntentRelation(relation) {
			t.Fatalf("intent relation %q is not defined", relation)
		}
	}
	if IsIntentIntentRelation("related_to") {
		t.Fatal("undefined intent relation was accepted")
	}
}

func TestConflictLinkHasOneDeterministicDirection(t *testing.T) {
	link := NormalizeLink(Link{SourceKind: KindIntent, SourceRef: "z.intent", Relation: RelationConflictsWith, TargetKind: KindIntent, TargetRef: "a.intent"})
	if link.SourceRef != "a.intent" || link.TargetRef != "z.intent" {
		t.Fatalf("conflict link was not normalized: %#v", link)
	}
	if transitionWeight(RelationConflictsWith, true) != transitionWeight(RelationConflictsWith, false) {
		t.Fatal("symmetric conflict relation has directional retrieval weight")
	}
}

func TestTypedPPRUsesRelationTypeAndDirection(t *testing.T) {
	nodes := []Node{
		{ID: "seed", State: StateActive},
		{ID: "evidence", State: StateActive},
		{ID: "intent", State: StateActive},
		{ID: "related", State: StateActive},
		{ID: "contained", State: StateActive},
	}
	edges := []Edge{
		{SourceID: "seed", TargetID: "evidence", Relation: RelationRealizedBy},
		{SourceID: "seed", TargetID: "intent", Relation: RelationRefines},
		{SourceID: "seed", TargetID: "related", Relation: "calls"},
		{SourceID: "seed", TargetID: "contained", Relation: containsRelation},
	}
	ranked := TypedPPR(nodes, edges, map[string]float64{"seed": 1})
	scores := map[string]float64{}
	for _, rank := range ranked {
		scores[rank.NodeID] = rank.Score
	}
	if !(scores["evidence"] == scores["intent"] && scores["intent"] > scores["related"] && scores["related"] > scores["contained"]) {
		t.Fatalf("edge types did not affect forward rank: %#v", scores)
	}

	reversed := TypedPPR(nodes, edges, map[string]float64{"contained": 1})
	if len(reversed) < 2 || reversed[0].NodeID != "seed" {
		t.Fatalf("reverse containment did not return to its parent: %#v", reversed)
	}
}

func TestTypedPPRSkipsMissingAndDisconnectedNodes(t *testing.T) {
	nodes := []Node{{ID: "seed", State: StateActive}, {ID: "connected", State: StateActive}, {ID: "missing", State: StateMissing}, {ID: "island", State: StateActive}}
	edges := []Edge{{SourceID: "seed", TargetID: "connected", Relation: "related_to"}, {SourceID: "seed", TargetID: "missing", Relation: RelationAppliesTo}}
	ranked := TypedPPR(nodes, edges, map[string]float64{"seed": 1})
	if len(ranked) != 2 || ranked[0].NodeID != "seed" || ranked[1].NodeID != "connected" {
		t.Fatalf("rank escaped the active connected component: %#v", ranked)
	}
}
