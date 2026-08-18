package prepare

import (
	"strings"
	"testing"
	"time"
)

func TestRankUsesDistinctTermsAndActivePaths(t *testing.T) {
	candidates := []Candidate{
		{NodeID: "auth", Key: "material.auth", Label: "AuthService", Kind: "type", Origin: "structural", Source: "file:src/auth/service.go"},
		{NodeID: "db", Key: "decision.database", Label: "decision.database", Kind: "decision", Origin: "human"},
	}
	found, terms := Rank(candidates, "인증은 어디에 있어?", nil, nil, nil)
	if len(found) != 1 || found[0].NodeID != "auth" || !contains(terms, "auth") {
		t.Fatalf("unexpected Korean retrieval: %#v %#v", found, terms)
	}
	found, _ = Rank(candidates, "service", nil, nil, nil)
	if len(found) != 0 {
		t.Fatalf("generic term selected context: %#v", found)
	}
	found, _ = Rank(candidates, "unrelated", nil, []string{"src/auth"}, nil)
	if len(found) != 1 || found[0].NodeID != "auth" || !contains(found[0].Signals, "active-path") {
		t.Fatalf("active path did not select context: %#v", found)
	}
}

func TestRankAppliesSemanticUsageAndFreshnessAfterRelevance(t *testing.T) {
	now := time.Now().Unix()
	candidates := []Candidate{
		{NodeID: "fresh", Key: "knowledge.fresh", Label: "knowledge.fresh", Origin: "human", UpdatedAt: now, SelectedCount: 2, Score: 35, Signals: []string{"semantic:0.700"}},
		{NodeID: "stale", Key: "knowledge.stale", Label: "knowledge.stale", Origin: "human", UpdatedAt: now - 100*24*60*60, SelectedCount: 2, Score: 35, Signals: []string{"semantic:0.700"}},
		{NodeID: "unused", Key: "knowledge.unused", Label: "knowledge.unused", Origin: "human", UpdatedAt: now, SelectedCount: 100},
	}
	found, _ := Rank(candidates, "unrelated", nil, nil, nil)
	if len(found) != 2 || found[0].NodeID != "fresh" || found[0].Score-found[1].Score != 10 || !contains(found[0].Signals, "usage:selected=2") || !contains(found[1].Signals, "stale") {
		t.Fatalf("semantic usage/freshness ranking failed: %#v", found)
	}
}

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
	value, truncated := Truncate(strings.Repeat("context ", 500), 128)
	if !truncated || EstimateTokens(value) > 128 || !strings.Contains(value, "truncated by Purpory") {
		t.Fatalf("budget was not enforced: %d %q", EstimateTokens(value), value)
	}
}

func TestFallbackAndAwareness(t *testing.T) {
	if proposal := Fallback("안녕하세요"); proposal.Action != "skip" || proposal.ReasonCode != "SELF_CONTAINED" {
		t.Fatalf("greeting was not skipped: %#v", proposal)
	}
	relation := "calls"
	rendered := RenderAwareness([]Awareness{{Key: "material.token", Label: "TokenRepository", Source: "src/token.go", Relation: &relation}})
	if !strings.Contains(rendered, "NOT LOADED") || !strings.Contains(rendered, "via calls") {
		t.Fatalf("awareness was not rendered: %q", rendered)
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
