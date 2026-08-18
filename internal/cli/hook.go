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
	"github.com/sehwan505/purpory/internal/reconcile"
)

const maximumHookInput = 1 << 20

type hookPayload struct {
	Event          string `json:"hook_event_name"`
	Prompt         string `json:"prompt"`
	SessionID      string `json:"session_id"`
	CWD            string `json:"cwd"`
	TranscriptPath string `json:"transcript_path"`
	Reason         string `json:"reason"`
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
		TokenBudget: budget, RetainInput: retainHookInput(), HintsOnly: true,
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
	var parts []string
	if result.Action == "retrieve" {
		if hints := contextprepare.RenderHintMap(result.Hints); hints != "" {
			parts = append(parts, hints)
		}
	}
	if result.Action == "ask" && result.Clarification != nil {
		requestID := ""
		if result.RequestID != nil {
			requestID = fmt.Sprintf("\nRequest ID: %d", *result.RequestID)
		}
		parts = append(parts, "[PURPORY INTENT ALIGNMENT SUGGESTION]\nKeep the original prompt active and ask the user this clarification:"+requestID+"\n"+*result.Clarification)
	}
	return strings.Join(parts, "\n\n")
}

func retainHookInput() bool {
	value := strings.ToLower(strings.TrimSpace(os.Getenv("PURPORY_CONTEXT_RETAIN_INPUT")))
	return value != "0" && value != "false" && value != "no"
}

func runSessionEnd(ctx context.Context, service *product.Service, agent string, input io.Reader) (string, error) {
	payload, err := readHook(input)
	if err != nil {
		return "", err
	}
	if payload.Event != "SessionEnd" {
		return "", errors.New("session-end: expected SessionEnd")
	}
	sessionID, err := agentSession(agent, payload.SessionID)
	if err != nil {
		return "", err
	}
	return service.QueueSessionEnd(ctx, payload.CWD, sessionID, agent, payload.TranscriptPath, payload.Reason)
}

func startReconciliation() error {
	executable, err := os.Executable()
	if err != nil {
		return fmt.Errorf("start reconciliation: executable: %w", err)
	}
	return reconcile.StartWorker(executable)
}

func agentSession(agent, sessionID string) (string, error) {
	agent = strings.ToLower(strings.TrimSpace(agent))
	if agent != "codex" && agent != "claude" {
		return "", errors.New("agent hook: agent must be codex or claude")
	}
	return agent + ":" + strings.TrimSpace(sessionID), nil
}
