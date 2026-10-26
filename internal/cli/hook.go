package cli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	product "github.com/sehwan505/purpory/internal/app"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
)

const maximumHookInput = 1 << 20

type hookPayload struct {
	Event     string `json:"hook_event_name"`
	Prompt    string `json:"prompt"`
	SessionID string `json:"session_id"`
	CWD       string `json:"cwd"`
}

type hookResponse struct {
	Output struct {
		Event   string `json:"hookEventName"`
		Context string `json:"additionalContext"`
	} `json:"hookSpecificOutput"`
}

type hookFailure struct {
	Decision string `json:"decision"`
	Reason   string `json:"reason"`
}

func readHook(input io.Reader) (hookPayload, error) {
	reader := io.LimitReader(input, maximumHookInput+1)
	content, err := io.ReadAll(reader)
	if err != nil {
		return hookPayload{}, fmt.Errorf("read agent hook: %w", err)
	}
	if len(content) > maximumHookInput {
		return hookPayload{}, errors.New("read agent hook: input exceeds 1 MiB")
	}
	var payload hookPayload
	if err := json.Unmarshal(content, &payload); err != nil {
		return hookPayload{}, fmt.Errorf("read agent hook: %w", err)
	}
	if strings.TrimSpace(payload.SessionID) == "" || strings.TrimSpace(payload.CWD) == "" {
		return hookPayload{}, errors.New("read agent hook: session_id and cwd are required")
	}
	return payload, nil
}

func runPreflight(ctx context.Context, service *product.Service, agent string, input io.Reader, output io.Writer) error {
	payload, err := readHook(input)
	if err != nil {
		return writeHookFailure(output)
	}
	if payload.Event != "UserPromptSubmit" || strings.TrimSpace(payload.Prompt) == "" {
		return writeHookFailure(output)
	}
	sessionID, err := agentSession(agent, payload.SessionID)
	if err != nil {
		return writeHookFailure(output)
	}
	budget, err := hookTokenBudget()
	if err != nil {
		return writeHookFailure(output)
	}
	prepared, err := service.PrepareContext(ctx, contextprepare.Request{
		Message: payload.Prompt, SessionID: sessionID, WorkingDirectory: payload.CWD,
		TokenBudget: budget,
	})
	if err != nil {
		return writeHookFailure(output)
	}
	context := hookContext(prepared)
	if context == "" {
		return nil
	}
	response := hookResponse{}
	response.Output.Event = payload.Event
	response.Output.Context = context
	return writeJSON(output, response, nil)
}

func writeHookFailure(output io.Writer) error {
	return writeJSON(output, hookFailure{
		Decision: "block",
		Reason:   "Purpory could not complete the mandatory context preflight. Run `purpory model status` and retry the prompt.",
	}, nil)
}

func hookTokenBudget() (int, error) {
	raw := strings.TrimSpace(os.Getenv("PURPORY_CONTEXT_TOKEN_BUDGET"))
	if raw == "" {
		return 512, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value < contextprepare.MinTokenBudget || value > contextprepare.MaxTokenBudget {
		return 0, errors.New("preflight: PURPORY_CONTEXT_TOKEN_BUDGET is invalid")
	}
	return value, nil
}

func hookContext(result product.PrepareResult) string {
	return contextprepare.RenderHintMap(result.Hints)
}

func runSessionEnd(ctx context.Context, service *product.Service, agent string, input io.Reader) error {
	payload, err := readHook(input)
	if err != nil {
		return err
	}
	if payload.Event != "SessionEnd" {
		return errors.New("session-end: expected SessionEnd")
	}
	sessionID, err := agentSession(agent, payload.SessionID)
	if err != nil {
		return err
	}
	return service.SaveSessionAt(ctx, payload.CWD, sessionID, agent, "ended")
}

func agentSession(agent, sessionID string) (string, error) {
	agent = strings.ToLower(strings.TrimSpace(agent))
	if agent != "codex" && agent != "claude" && agent != "hermes" {
		return "", errors.New("agent hook: agent must be codex, claude, or hermes")
	}
	return agent + ":" + strings.TrimSpace(sessionID), nil
}
