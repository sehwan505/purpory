package prepare

import (
	"crypto/sha256"
	"encoding/hex"
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

func RenderAwareness(items []Awareness) string {
	if len(items) == 0 {
		return ""
	}
	lines := []string{
		"[PURPORY RELATED CONTEXT AVAILABLE — NOT LOADED]",
		"These are discovery hints, not evidence. Do not assume they are relevant. Run `purpory prepare \"<specific need>\"` only if the task reveals a matching gap.",
	}
	for _, item := range items[:min(len(items), MaxAwarenessHints)] {
		var details []string
		if item.Relation != nil && strings.TrimSpace(*item.Relation) != "" {
			details = append(details, "via "+strings.TrimSpace(*item.Relation))
		}
		if item.Source != "" {
			details = append(details, item.Source)
		}
		suffix := ""
		if len(details) > 0 {
			suffix = " (" + strings.Join(details, "; ") + ")"
		}
		lines = append(lines, "- "+item.Key+": "+item.Label+suffix)
	}
	return strings.Join(lines, "\n")
}
