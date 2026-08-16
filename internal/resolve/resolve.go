// Package resolve turns extracted relationship claims into project-wide edges.
package resolve

import (
	"sort"
	"strings"

	"github.com/sehwan505/purpory/internal/graph"
)

func Claims(nodes []graph.Node, claims []graph.Claim) []graph.Edge {
	byID := make(map[string]bool, len(nodes))
	byLabel := map[string][]string{}
	bySuffix := map[string][]string{}
	for _, node := range nodes {
		byID[node.ID] = true
		byLabel[node.Label] = append(byLabel[node.Label], node.ID)
		if separator := strings.LastIndex(node.Label, "."); separator >= 0 {
			bySuffix[node.Label[separator+1:]] = append(bySuffix[node.Label[separator+1:]], node.ID)
		}
	}
	seen := map[string]bool{}
	var edges []graph.Edge
	for _, claim := range claims {
		if !byID[claim.SourceID] {
			continue
		}
		target := claim.TargetID
		if target == "" {
			candidates := byLabel[claim.TargetLabel]
			if len(candidates) == 0 {
				candidates = bySuffix[claim.TargetLabel]
			}
			if len(candidates) != 1 {
				continue
			}
			target = candidates[0]
		}
		if !byID[target] || target == claim.SourceID {
			continue
		}
		key := claim.SourceID + "\x00" + target + "\x00" + claim.Relation
		if seen[key] {
			continue
		}
		seen[key] = true
		edges = append(edges, graph.Edge{SourceID: claim.SourceID, TargetID: target, Relation: claim.Relation})
	}
	sort.Slice(edges, func(i, j int) bool {
		left, right := edges[i], edges[j]
		return left.SourceID+left.TargetID+left.Relation < right.SourceID+right.TargetID+right.Relation
	})
	return edges
}
