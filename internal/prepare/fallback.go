// Package prepare defines deterministic context selection and hint rendering.
package prepare

import (
	"path/filepath"
	"strings"
	"unicode/utf8"
)

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

func NormalizePath(value string) string {
	value = strings.Trim(strings.ToLower(filepath.ToSlash(strings.TrimSpace(value))), "/")
	value = strings.TrimPrefix(value, "@repo/")
	value = strings.TrimPrefix(value, "@root/")
	value = strings.TrimPrefix(value, "file:")
	return strings.Trim(value, "/")
}
