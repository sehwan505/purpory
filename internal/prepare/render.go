package prepare

import (
	"crypto/sha256"
	"encoding/hex"
	"strconv"
	"strings"
	"unicode/utf8"
)

func EstimateTokens(value string) int {
	return max(1, (len([]byte(value))+3)/4)
}

func Truncate(value string, budget int) (string, bool) {
	if EstimateTokens(value) <= budget {
		return value, false
	}
	marker := "\n\n[truncated by Purpory context budget]\n"
	maximum := max(1, budget*4-len(marker))
	raw := []byte(value)
	if maximum > len(raw) {
		maximum = len(raw)
	}
	for maximum > 0 && !utf8.Valid(raw[:maximum]) {
		maximum--
	}
	return strings.TrimSpace(string(raw[:maximum])) + marker, true
}

func Hash(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}

func RenderHintMap(hints *HintMap) string {
	if hints == nil || len(hints.Nodes) == 0 {
		return ""
	}
	aliases := make(map[string]string, len(hints.Nodes))
	lines := []string{
		"[PURPORY MEMORY MAP — CONTENT NOT LOADED]",
		"This is a navigation map, not evidence. Load only the nodes needed for the task.",
		"Nodes:",
	}
	for index, node := range hints.Nodes {
		alias := "N" + strconv.Itoa(index+1)
		aliases[node.ID] = alias
		details := []string{node.Kind}
		if node.Subkind != "" {
			details[0] += "/" + node.Subkind
		}
		if node.Match != "" {
			details = append(details, node.Match)
		}
		if node.State != "" && node.State != "active" {
			details = append(details, node.State)
		}
		if node.Source != "" {
			details = append(details, node.Source)
		}
		address := node.ID
		if node.Path != "" {
			address = node.Path
		}
		lines = append(lines, "- "+alias+" `"+address+"` ["+strings.Join(details, "; ")+"] "+node.Label)
	}
	if len(hints.Edges) > 0 {
		lines = append(lines, "Paths:")
		for _, edge := range hints.Edges {
			left, leftFound := aliases[edge.SourceID]
			right, rightFound := aliases[edge.TargetID]
			if leftFound && rightFound {
				lines = append(lines, "- "+left+" --"+edge.Relation+"--> "+right)
			}
		}
	}
	lines = append(lines,
		"Inspect nodes: `purpory explain \"<path or node ID>\" [\"<path or node ID>\" ...]`",
		"Browse a branch or search: `purpory query \"<path prefix or question>\"`",
		"Connect two nodes: `purpory path \"<path or node ID>\" \"<path or node ID>\"`",
	)
	return strings.Join(lines, "\n")
}
