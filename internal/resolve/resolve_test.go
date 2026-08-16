package resolve

import (
	"testing"

	"github.com/sehwan505/purpory/internal/graph"
)

func TestClaimsResolveOnlyUniqueTargets(t *testing.T) {
	nodes := []graph.Node{{ID: "caller", Label: "run()"}, {ID: "target", Label: "Service.help()"}}
	claims := []graph.Claim{{SourceID: "caller", TargetLabel: "help()", Relation: "calls"}}
	edges := Claims(nodes, claims)
	if len(edges) != 1 || edges[0].TargetID != "target" {
		t.Fatalf("unexpected edges: %#v", edges)
	}
	nodes = append(nodes, graph.Node{ID: "ambiguous", Label: "Other.help()"})
	if edges := Claims(nodes, claims); len(edges) != 0 {
		t.Fatalf("ambiguous claim resolved: %#v", edges)
	}
}
