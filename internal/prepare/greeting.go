// Package prepare defines deterministic context selection and hint rendering.
package prepare

import (
	"strings"
)

func IsGreeting(message string) bool {
	value := strings.Trim(strings.ToLower(strings.TrimSpace(message)), "!?. ")
	switch value {
	case "hi", "hello", "hey", "안녕", "안녕하세요", "반가워":
		return true
	}
	return false
}
