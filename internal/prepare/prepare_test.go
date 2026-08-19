package prepare

import (
	"strings"
	"testing"
)

func TestBM25RanksLexicalEvidenceWithoutForcingAMatch(t *testing.T) {
	candidates := []Candidate{
		{NodeID: "auth", Key: "knowledge.auth", Label: "Authentication", Content: "Signed browser sessions and cookies."},
		{NodeID: "git", Key: "resource.git", Label: ".git", Content: ".git"},
	}
	found, terms := BM25(candidates, "signed sessions", nil)
	if len(found) != 1 || found[0].NodeID != "auth" || !strings.HasPrefix(found[0].Signals[0], "bm25:") || !contains(terms, "signed") {
		t.Fatalf("unexpected BM25 ranking: %#v %#v", found, terms)
	}
	if found, _ := BM25(candidates, "deployment", nil); len(found) != 0 {
		t.Fatalf("BM25 forced an invalid result: %#v", found)
	}
}

func TestContractAndBudget(t *testing.T) {
	request, err := ValidateRequest(Request{Message: "database", SessionID: "agent", ProjectID: "demo", WorkingDirectory: "/demo", TokenBudget: 128})
	if err != nil || request.Message != "database" {
		t.Fatalf("valid request rejected: %#v %v", request, err)
	}
	if _, err := ValidateRequest(Request{Message: "database", SessionID: "agent", ProjectID: "demo", WorkingDirectory: "/demo", TokenBudget: 127}); err == nil {
		t.Fatal("small token budget accepted")
	}
}

func TestFallback(t *testing.T) {
	if proposal := Fallback("안녕하세요"); proposal.Action != "skip" || proposal.ReasonCode != "SELF_CONTAINED" {
		t.Fatalf("greeting was not skipped: %#v", proposal)
	}
}

func TestRenderHintMapUsesAliasesAndNoContent(t *testing.T) {
	hints := &HintMap{
		Nodes: []HintNode{
			{ID: "intent:auth", Path: "product.auth.sessions", Label: "Authentication intent", Kind: "intent", Subkind: "decision", Match: "semantic"},
			{ID: "material:file:auth.md", Label: "auth.md", Kind: "material", Match: "path", Source: "file:auth.md"},
		},
		Edges: []HintEdge{{SourceID: "intent:auth", TargetID: "material:file:auth.md", Relation: "realized_by"}},
	}
	rendered := RenderHintMap(hints)
	if !strings.Contains(rendered, "N1 --realized_by--> N2") || !strings.Contains(rendered, "product.auth.sessions") || strings.Contains(rendered, "`intent:auth`") || !strings.Contains(rendered, `purpory explain`) || strings.Contains(rendered, "source body") {
		t.Fatalf("hint map was not navigable: %q", rendered)
	}
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
