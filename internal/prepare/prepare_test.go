package prepare

import (
	"strings"
	"testing"
)

func TestContractAndBudget(t *testing.T) {
	request, err := ValidateRequest(Request{Message: "database", SessionID: "agent", ProjectID: "demo", WorkingDirectory: "/demo", TokenBudget: 128})
	if err != nil || request.Message != "database" {
		t.Fatalf("valid request rejected: %#v %v", request, err)
	}
	if _, err := ValidateRequest(Request{Message: "database", SessionID: "agent", ProjectID: "demo", WorkingDirectory: "/demo", TokenBudget: 127}); err == nil {
		t.Fatal("small token budget accepted")
	}
}

func TestGreeting(t *testing.T) {
	if !IsGreeting("안녕하세요") {
		t.Fatal("greeting was not recognized")
	}
}

func TestRenderHintMapUsesAliasesAndNoContent(t *testing.T) {
	hints := &HintMap{
		Nodes: []HintNode{
			{ID: "intent:auth", Path: "product.auth.sessions", Label: "Authentication intent", Kind: "intent", Subkind: "decision", Match: "semantic-seed"},
			{ID: "material:file:auth.md", Label: "auth.md", Kind: "material", Match: "path", Source: "file:auth.md"},
		},
		Edges: []HintEdge{{SourceID: "intent:auth", TargetID: "material:file:auth.md", Relation: "realized_by"}},
	}
	rendered := RenderHintMap(hints)
	if !strings.Contains(rendered, "N1 --realized_by--> N2") || !strings.Contains(rendered, "product.auth.sessions") || strings.Contains(rendered, "`intent:auth`") || !strings.Contains(rendered, `purpory explain`) || strings.Contains(rendered, "source body") {
		t.Fatalf("hint map was not navigable: %q", rendered)
	}
}
