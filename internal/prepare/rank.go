// Package prepare defines deterministic context selection and hint rendering.
package prepare

import (
	"fmt"
	"math"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
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

var koreanSuffixes = []string{"에서", "에게", "부터", "까지", "으로", "은", "는", "이", "가", "을", "를", "과", "와", "의", "에", "로", "도", "만"}

func Fallback(message string) Proposal {
	if IsGreeting(message) {
		return Proposal{Action: "skip", Keywords: []string{}, ReasonCode: "SELF_CONTAINED"}
	}
	if utf8.RuneCountInString(message) > MaxQueryChars {
		return Proposal{Action: "skip", Keywords: []string{}, ReasonCode: "GATE_UNAVAILABLE"}
	}
	query := strings.TrimSpace(message)
	return Proposal{Action: "search", Query: &query, Keywords: []string{}, ReasonCode: "GATE_UNAVAILABLE"}
}

func IsGreeting(message string) bool {
	value := strings.Trim(strings.ToLower(strings.TrimSpace(message)), "!?. ")
	switch value {
	case "hi", "hello", "hey", "안녕", "안녕하세요", "반가워":
		return true
	}
	return false
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

func NormalizePath(value string) string {
	value = strings.Trim(strings.ToLower(filepath.ToSlash(strings.TrimSpace(value))), "/")
	value = strings.TrimPrefix(value, "@repo/")
	value = strings.TrimPrefix(value, "@root/")
	value = strings.TrimPrefix(value, "file:")
	return strings.Trim(value, "/")
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
