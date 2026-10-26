package app

import (
	"os"
	"strings"
)

func currentSessionID(explicit string) string {
	if value := strings.TrimSpace(explicit); value != "" {
		return value
	}
	for _, item := range []struct{ name, prefix string }{{"PURPORY_SESSION", ""}, {"CODEX_THREAD_ID", "codex:"}, {"CLAUDE_SESSION_ID", "claude:"}} {
		if value := strings.TrimSpace(os.Getenv(item.name)); value != "" {
			if item.prefix != "" && !strings.HasPrefix(value, item.prefix) {
				return item.prefix + value
			}
			return value
		}
	}
	return "anon"
}

func sessionAgent(sessionID string) string {
	agent, _, found := strings.Cut(sessionID, ":")
	if found && agent != "" {
		return agent
	}
	return "unknown"
}
