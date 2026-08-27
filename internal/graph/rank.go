package graph

import (
	"math"
	"sort"
)

const (
	pprRestart       = 0.2
	pprIterations    = 40
	pprConvergence   = 1e-10
	containsRelation = "contains"
)

// Rank is a node's relevance to a personalized graph walk.
type Rank struct {
	NodeID string
	Score  float64
}

// TypedPPR ranks active nodes from weighted seeds. Edge meaning and direction
// affect each transition; disconnected nodes never receive score.
func TypedPPR(nodes []Node, edges []Edge, seeds map[string]float64) []Rank {
	index := make(map[string]int, len(nodes))
	ids := make([]string, 0, len(nodes))
	for _, node := range nodes {
		if node.State == StateActive {
			index[node.ID] = len(ids)
			ids = append(ids, node.ID)
		}
	}
	personalization := make([]float64, len(ids))
	var seedTotal float64
	for id, weight := range seeds {
		if position, found := index[id]; found && weight > 0 && !math.IsNaN(weight) && !math.IsInf(weight, 0) {
			personalization[position] += weight
			seedTotal += weight
		}
	}
	if seedTotal == 0 {
		return nil
	}
	for position := range personalization {
		personalization[position] /= seedTotal
	}

	type transition struct {
		to     int
		weight float64
	}
	adjacent := make([][]transition, len(ids))
	totals := make([]float64, len(ids))
	for _, edge := range edges {
		source, sourceFound := index[edge.SourceID]
		target, targetFound := index[edge.TargetID]
		if !sourceFound || !targetFound {
			continue
		}
		forward := transitionWeight(edge.Relation, true)
		reverse := transitionWeight(edge.Relation, false)
		adjacent[source] = append(adjacent[source], transition{to: target, weight: forward})
		adjacent[target] = append(adjacent[target], transition{to: source, weight: reverse})
		totals[source] += forward
		totals[target] += reverse
	}

	scores := append([]float64(nil), personalization...)
	for iteration := 0; iteration < pprIterations; iteration++ {
		next := make([]float64, len(ids))
		for position, weight := range personalization {
			next[position] = pprRestart * weight
		}
		var dangling float64
		for from, score := range scores {
			if totals[from] == 0 {
				dangling += score
				continue
			}
			for _, transition := range adjacent[from] {
				next[transition.to] += (1 - pprRestart) * score * transition.weight / totals[from]
			}
		}
		if dangling > 0 {
			for position, weight := range personalization {
				next[position] += (1 - pprRestart) * dangling * weight
			}
		}
		var delta float64
		for position := range scores {
			delta += math.Abs(next[position] - scores[position])
		}
		scores = next
		if delta < pprConvergence {
			break
		}
	}

	result := make([]Rank, 0, len(ids))
	for position, score := range scores {
		if score > 0 {
			result = append(result, Rank{NodeID: ids[position], Score: score})
		}
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Score != result[j].Score {
			return result[i].Score > result[j].Score
		}
		return result[i].NodeID < result[j].NodeID
	})
	return result
}

func transitionWeight(relation string, forward bool) float64 {
	switch relation {
	case RelationAppliesTo, RelationRealizedBy, RelationVerifiedBy, RelationContradictedBy:
		if forward {
			return 1
		}
		return 0.9
	case containsRelation:
		if forward {
			return 0.45
		}
		return 1
	default:
		return 0.7
	}
}
