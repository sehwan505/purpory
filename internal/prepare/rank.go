// Package prepare defines deterministic context selection and delivery rules.
package prepare

import (
	"fmt"
	"math"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

var tokenPattern = regexp.MustCompile(`[A-Za-z0-9_-]+|[가-힣]{2,}`)

var stopWords = map[string]bool{
	"a": true, "an": true, "and": true, "are": true, "as": true, "at": true,
	"be": true, "by": true, "did": true, "do": true, "does": true, "for": true,
	"from": true, "how": true, "i": true, "in": true, "is": true, "it": true,
	"of": true, "on": true, "or": true, "our": true, "should": true, "that": true,
	"the": true, "this": true, "to": true, "we": true, "what": true, "when": true,
	"where": true, "which": true, "who": true, "why": true, "with": true,
	"you": true, "your": true,
}

var genericTerms = map[string]bool{
	"app": true, "application": true, "code": true, "component": true, "core": true,
	"file": true, "helper": true, "manager": true, "module": true, "service": true,
	"services": true, "system": true, "util": true, "utils": true,
}

var koreanSuffixes = []string{"에서", "에게", "부터", "까지", "으로", "은", "는", "이", "가", "을", "를", "과", "와", "의", "에", "로", "도", "만"}

var aliases = map[string][]string{
	"개발자": {"developer", "developers"}, "검색": {"search", "retrieval"},
	"결정": {"decision"}, "근거": {"evidence"}, "기억": {"memory"},
	"날짜": {"date"}, "대체": {"replace", "autonomy"}, "데이터베이스": {"database"},
	"반려": {"reject", "rejected"}, "버전": {"version", "superseded"},
	"보고서": {"report"}, "사용자": {"user", "human"}, "선택": {"select", "selected"},
	"세션": {"session"}, "승인": {"approve", "approved", "approval"},
	"요청": {"request"}, "유용": {"utility", "usage", "useful"},
	"의도": {"intent"}, "입력": {"input"}, "전역": {"global"},
	"제공": {"deliver", "delivery"}, "지식": {"knowledge"},
	"감독": {"supervise", "supervision"}, "검토": {"review"}, "리뷰": {"review"},
	"변경": {"change", "changed"}, "위험": {"risk"},
	"인증": {"auth", "authentication"}, "충돌": {"conflict"},
	"코드": {"code"}, "프로젝트": {"project"}, "리소스": {"resource"},
	"자료": {"resource", "document", "data"}, "확장": {"expand", "expanded"},
}

func Fallback(message string) Proposal {
	if IsGreeting(message) {
		return Proposal{Action: "skip", Scopes: []string{}, Keywords: []string{}, ReasonCode: "SELF_CONTAINED"}
	}
	if utf8.RuneCountInString(message) > MaxQueryChars {
		return Proposal{Action: "skip", Scopes: []string{}, Keywords: []string{}, ReasonCode: "GATE_UNAVAILABLE"}
	}
	query := strings.TrimSpace(message)
	return Proposal{Action: "search", Query: &query, Scopes: []string{"human", "resource", "session"}, Keywords: []string{}, ReasonCode: "GATE_UNAVAILABLE"}
}

func IsGreeting(message string) bool {
	value := strings.Trim(strings.ToLower(strings.TrimSpace(message)), "!?. ")
	switch value {
	case "hi", "hello", "hey", "안녕", "안녕하세요", "반가워":
		return true
	}
	return false
}

func Rank(candidates []Candidate, query string, keywords, activePaths, priorKeys []string) ([]Candidate, []string) {
	terms := Tokens(append([]string{query}, keywords...)...)
	priorSet := stringSet(priorKeys)
	active := make([]string, 0, len(activePaths))
	for _, path := range activePaths {
		active = append(active, NormalizePath(path))
	}
	result := make([]Candidate, 0, len(candidates))
	for _, candidate := range candidates {
		label := strings.ToLower(candidate.Label)
		key := strings.ToLower(candidate.Key)
		source := NormalizePath(candidate.Source)
		// Content matching remains the deterministic fallback when dense embeddings are unavailable.
		searchable := strings.ToLower(strings.Join([]string{candidate.Key, candidate.Label, candidate.Kind, candidate.Source, candidate.Content}, " "))
		score := candidate.Score
		matched := map[string]bool{}
		signals := append([]string(nil), candidate.Signals...)
		for _, term := range terms {
			switch {
			case term == label || term == key:
				score += 60
				signals = append(signals, "exact:"+term)
				matched[term] = true
			case strings.HasPrefix(label, term) || strings.HasPrefix(key, term):
				score += 36
				signals = append(signals, "prefix:"+term)
				matched[term] = true
			case strings.Contains(label, term) || strings.Contains(key, term):
				score += 18
				signals = append(signals, "label:"+term)
				matched[term] = true
			case strings.Contains(searchable, term):
				score += 10
				signals = append(signals, "term:"+term)
				matched[term] = true
			}
			if source != "" && strings.Contains(source, term) {
				score += 6
				signals = append(signals, "source:"+term)
			}
		}
		activeMatch := false
		for _, path := range active {
			if PathsRelated(source, path) {
				activeMatch = true
				break
			}
		}
		if activeMatch {
			score += 32
			signals = append(signals, "active-path")
		}
		distinctive := false
		for term := range matched {
			if !genericTerms[term] {
				distinctive = true
				break
			}
		}
		semanticMatch := hasSignalPrefix(signals, "semantic:")
		if !distinctive && !activeMatch && !semanticMatch {
			continue
		}
		if candidate.Origin == "human" {
			score += 12
			signals = append(signals, "human")
		}
		if candidate.Kind == "decision" {
			score += 12
			signals = append(signals, "decision")
		}
		if candidate.SelectedCount > 0 {
			score += math.Min(12, float64(candidate.SelectedCount*2))
			signals = append(signals, fmt.Sprintf("usage:selected=%d", candidate.SelectedCount))
		}
		if candidate.ExpandedCount > 0 {
			score += math.Min(8, float64(candidate.ExpandedCount*3))
			signals = append(signals, fmt.Sprintf("usage:expanded=%d", candidate.ExpandedCount))
		}
		if candidate.UpdatedAt > 0 && time.Now().Unix()-candidate.UpdatedAt > 90*24*60*60 {
			score -= 10
			signals = append(signals, "stale")
		}
		if priorSet[candidate.Key] {
			score -= 8
			signals = append(signals, "previously-delivered")
		}
		candidate.Score = math.Round(score*1_000_000) / 1_000_000
		candidate.Signals = uniqueSorted(signals)
		result = append(result, candidate)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Score != result[j].Score {
			return result[i].Score > result[j].Score
		}
		if result[i].Key != result[j].Key {
			return result[i].Key < result[j].Key
		}
		return result[i].NodeID < result[j].NodeID
	})
	return result, terms
}

// BM25 ranks content-bearing graph candidates without changing semantic scores.
func BM25(candidates []Candidate, query string, keywords []string) ([]Candidate, []string) {
	terms := uniqueSorted(lexicalTokens(append([]string{query}, keywords...)...))
	if len(candidates) == 0 || len(terms) == 0 {
		return nil, terms
	}
	documents := make([][]string, len(candidates))
	documentFrequency := map[string]int{}
	totalLength := 0
	for index, candidate := range candidates {
		documents[index] = lexicalTokens(candidate.Key, candidate.Label, candidate.Kind, candidate.Source, candidate.Content)
		totalLength += len(documents[index])
		seen := map[string]bool{}
		for _, token := range documents[index] {
			if !seen[token] {
				documentFrequency[token]++
				seen[token] = true
			}
		}
	}
	averageLength := float64(totalLength) / float64(len(documents))
	if averageLength == 0 {
		return nil, terms
	}
	result := make([]Candidate, 0, len(candidates))
	for index, candidate := range candidates {
		frequency := map[string]int{}
		for _, token := range documents[index] {
			frequency[token]++
		}
		var score float64
		for _, term := range terms {
			tf := float64(frequency[term])
			if tf == 0 {
				continue
			}
			df := float64(documentFrequency[term])
			idf := math.Log(1 + (float64(len(documents))-df+0.5)/(df+0.5))
			score += idf * (tf * 2.2) / (tf + 1.2*(0.25+0.75*float64(len(documents[index]))/averageLength))
		}
		if score == 0 {
			continue
		}
		candidate.Score = math.Round(score*1_000_000) / 1_000_000
		candidate.Signals = []string{fmt.Sprintf("bm25:%.3f", candidate.Score)}
		result = append(result, candidate)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Score != result[j].Score {
			return result[i].Score > result[j].Score
		}
		return result[i].Key < result[j].Key
	})
	return result, terms
}

func lexicalTokens(values ...string) []string {
	var result []string
	for _, value := range values {
		for _, raw := range tokenPattern.FindAllString(value, -1) {
			token := strings.ToLower(raw)
			if len([]rune(token)) < 2 || stopWords[token] {
				continue
			}
			result = append(result, token)
			for _, suffix := range koreanSuffixes {
				if strings.HasSuffix(token, suffix) && len([]rune(token)) > len([]rune(suffix))+1 {
					result = append(result, strings.TrimSuffix(token, suffix))
					break
				}
			}
		}
	}
	return result
}

func hasSignalPrefix(signals []string, prefix string) bool {
	for _, signal := range signals {
		if strings.HasPrefix(signal, prefix) {
			return true
		}
	}
	return false
}

func Tokens(values ...string) []string {
	seen := map[string]bool{}
	var result []string
	for _, value := range values {
		for _, raw := range tokenPattern.FindAllString(value, -1) {
			token := strings.ToLower(raw)
			if len([]rune(token)) < 2 || stopWords[token] {
				continue
			}
			variants := []string{token}
			for _, suffix := range koreanSuffixes {
				if strings.HasSuffix(token, suffix) && len([]rune(token)) > len([]rune(suffix))+1 {
					variants = append(variants, strings.TrimSuffix(token, suffix))
					break
				}
			}
			for _, variant := range variants {
				for _, item := range append([]string{variant}, aliases[variant]...) {
					if !seen[item] {
						seen[item] = true
						result = append(result, item)
					}
				}
			}
		}
	}
	return result
}

func NormalizePath(value string) string {
	value = strings.Trim(strings.ToLower(filepath.ToSlash(strings.TrimSpace(value))), "/")
	value = strings.TrimPrefix(value, "@repo/")
	value = strings.TrimPrefix(value, "@root/")
	value = strings.TrimPrefix(value, "file:")
	return strings.Trim(value, "/")
}

func PathsRelated(left, right string) bool {
	left, right = NormalizePath(left), NormalizePath(right)
	return left != "" && right != "" && (left == right || strings.HasPrefix(left, right+"/") || strings.HasPrefix(right, left+"/"))
}

func stringSet(values []string) map[string]bool {
	result := make(map[string]bool, len(values))
	for _, value := range values {
		result[value] = true
	}
	return result
}

func uniqueSorted(values []string) []string {
	seen := map[string]bool{}
	result := values[:0]
	for _, value := range values {
		if !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	sort.Strings(result)
	return result
}
