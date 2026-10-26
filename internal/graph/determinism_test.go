package graph

import (
	"fmt"
	"reflect"
	"testing"
)

func TestTypedPPRSeedInsertionOrderDoesNotChangeScores(t *testing.T) {
	var nodes []Node
	seeds := map[string]float64{}
	for i := 0; i < 20; i++ {
		id := fmt.Sprintf("node:%02d", i)
		nodes = append(nodes, Node{ID: id, State: StateActive})
		seeds[id] = 1 / float64(i+1)
	}
	want := TypedPPR(nodes, nil, seeds)
	for repeat := 0; repeat < 100; repeat++ {
		reordered := map[string]float64{}
		for i := len(nodes) - 1; i >= 0; i-- {
			reordered[nodes[i].ID] = seeds[nodes[i].ID]
		}
		if got := TypedPPR(nodes, nil, reordered); !reflect.DeepEqual(got, want) {
			t.Fatalf("unstable scores: %#v != %#v", got, want)
		}
	}
}
