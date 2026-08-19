package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/sehwan505/purpory/internal/ollama"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
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

type ollamaGate struct {
	client *ollama.Client
	model  string
	tokens int
}

func newGateProvider(client *ollama.Client, selected string) contextprepare.Provider {
	model := strings.TrimSpace(os.Getenv("PURPORY_GATE_MODEL"))
	if model == "" {
		model = strings.TrimSpace(selected)
	}
	if model == "" {
		return nil
	}
	endpoint := strings.TrimSpace(os.Getenv("PURPORY_OLLAMA_URL"))
	if endpoint != "" && !localEndpoint(endpoint) && !environmentTrue("PURPORY_ALLOW_REMOTE_GATE") {
		return failingGate("remote gate URLs require PURPORY_ALLOW_REMOTE_GATE=true")
	}
	tokens := 8_192
	if configured, err := strconv.Atoi(strings.TrimSpace(os.Getenv("PURPORY_GATE_CONTEXT_TOKENS"))); err == nil && configured >= 1_024 {
		tokens = configured
	}
	return ollamaGate{client: client, model: model, tokens: tokens}
}

type failingGate string

func (f failingGate) Propose(context.Context, contextprepare.Request) (contextprepare.ProviderResult, error) {
	return contextprepare.ProviderResult{}, errors.New(string(f))
}

func localEndpoint(value string) bool {
	parsed, err := url.Parse(value)
	if err != nil {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	return host == "localhost" || host == "" || net.ParseIP(host) != nil && net.ParseIP(host).IsLoopback()
}

func environmentTrue(name string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(name))) {
	case "1", "true", "yes":
		return true
	}
	return false
}

func (g ollamaGate) Propose(ctx context.Context, request contextprepare.Request) (contextprepare.ProviderResult, error) {
	payload, err := json.Marshal(map[string]any{
		"request":          request.Message,
		"sessionId":        request.SessionID,
		"project":          request.ProjectID,
		"workingDirectory": request.WorkingDirectory,
		"activePaths":      request.ActivePaths,
		"tokenBudget":      request.TokenBudget,
		"openedNodes":      request.OpenedNodes,
		"contextCatalog":   request.Catalog,
	})
	if err != nil {
		return contextprepare.ProviderResult{}, fmt.Errorf("prepare gate: encode request: %w", err)
	}
	if len(payload) > g.tokens*3 {
		return contextprepare.ProviderResult{}, errors.New("gate request exceeds model context limit; model invocation skipped")
	}
	proposal := contextprepare.Proposal{}
	schema := map[string]any{
		"type": "object", "additionalProperties": false,
		"properties": map[string]any{
			"action":        map[string]any{"type": "string", "enum": []string{"skip", "search", "ask"}},
			"query":         map[string]any{"type": []string{"string", "null"}},
			"keywords":      map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
			"reasonCode":    map[string]any{"type": "string", "enum": []string{"SELF_CONTAINED", "CONTEXT_SEARCH_REQUIRED", "PRIOR_DECISION_REFERENCED", "PROJECT_CONTEXT_REQUIRED", "SESSION_HISTORY_REQUIRED", "CODE_CONTEXT_REQUIRED", "USER_INPUT_REQUIRED", "AMBIGUOUS_REQUEST"}},
			"clarification": map[string]any{"type": []string{"string", "null"}},
		},
		"required": []string{"action", "query", "keywords", "reasonCode", "clarification"},
	}
	started := time.Now()
	system := "Classify whether the request needs project context. Return only the strict JSON schema. Never answer the request. Use skip for self-contained work, search for project evidence, and ask only when user input is required."
	if err := g.client.ChatJSON(ctx, g.model, system, string(payload), schema, &proposal, g.tokens, 2*time.Second); err != nil {
		return contextprepare.ProviderResult{}, fmt.Errorf("prepare gate: %w", err)
	}
	return contextprepare.ProviderResult{Proposal: proposal, ModelID: g.model, LatencyMS: int(time.Since(started).Milliseconds())}, nil
}
