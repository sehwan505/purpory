// Package agent runs supported coding-agent CLIs as structured generators.
package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const maximumOutput = 4 << 20

type commandRunner func(context.Context, string, []string, string, string) ([]byte, error)

type Client struct {
	name string
	path string
	run  commandRunner
}

// ponytail: each structured request starts one CLI process; reuse sessions only if cron throughput makes startup measurable.
func New(name string) (*Client, error) {
	name = strings.ToLower(strings.TrimSpace(name))
	if name != "codex" && name != "claude" {
		return nil, errors.New("configure agent: agent must be codex or claude")
	}
	path, err := exec.LookPath(name)
	if err != nil {
		return nil, fmt.Errorf("configure agent: find %s: %w", name, err)
	}
	return &Client{name: name, path: path, run: runCommand}, nil
}

func (c *Client) GenerateJSON(ctx context.Context, model, system, prompt string, schema, target any, contextTokens int, timeout time.Duration) error {
	prompt = strings.TrimSpace(prompt)
	if prompt == "" || schema == nil || target == nil || contextTokens < 1024 || timeout <= 0 {
		return fmt.Errorf("call %s: valid prompt, schema, target, context, and timeout are required", c.name)
	}
	schemaJSON, err := json.Marshal(schema)
	if err != nil {
		return fmt.Errorf("call %s: encode schema: %w", c.name, err)
	}
	directory, err := os.MkdirTemp("", "purpory-reconcile-*")
	if err != nil {
		return fmt.Errorf("call %s: create workspace: %w", c.name, err)
	}
	defer os.RemoveAll(directory)

	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	if c.name == "codex" {
		return c.generateCodex(ctx, model, system, prompt, schemaJSON, target, directory)
	}
	return c.generateClaude(ctx, model, system, prompt, schemaJSON, target, directory)
}

func (c *Client) generateCodex(ctx context.Context, model, system, prompt string, schema []byte, target any, directory string) error {
	schemaPath := filepath.Join(directory, "schema.json")
	outputPath := filepath.Join(directory, "result.json")
	if err := os.WriteFile(schemaPath, schema, 0o600); err != nil {
		return fmt.Errorf("call codex: write schema: %w", err)
	}
	arguments := []string{"exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--ignore-user-config", "--ignore-rules", "--color", "never"}
	if model = strings.TrimSpace(model); model != "" {
		arguments = append(arguments, "--model", model)
	}
	arguments = append(arguments, "--output-schema", schemaPath, "--output-last-message", outputPath, "-")
	if _, err := c.run(ctx, c.path, arguments, combinePrompt(system, prompt), directory); err != nil {
		return err
	}
	content, err := readLimited(outputPath)
	if err != nil {
		return fmt.Errorf("call codex: read structured response: %w", err)
	}
	if err := json.Unmarshal(content, target); err != nil {
		return fmt.Errorf("call codex: decode structured response: %w", err)
	}
	return nil
}

func (c *Client) generateClaude(ctx context.Context, model, system, prompt string, schema []byte, target any, directory string) error {
	arguments := []string{
		"--print", "--no-session-persistence", "--disable-slash-commands", "--no-chrome",
		"--tools", "", "--disallowedTools", "mcp__*", "--output-format", "json",
		"--json-schema", string(schema), "--system-prompt", strings.TrimSpace(system),
	}
	if model = strings.TrimSpace(model); model != "" {
		arguments = append(arguments, "--model", model)
	}
	content, err := c.run(ctx, c.path, arguments, prompt, directory)
	if err != nil {
		return err
	}
	var response struct {
		Subtype          string          `json:"subtype"`
		IsError          bool            `json:"is_error"`
		StructuredOutput json.RawMessage `json:"structured_output"`
	}
	if err := json.Unmarshal(content, &response); err != nil {
		return fmt.Errorf("call claude: decode response: %w", err)
	}
	if response.IsError || response.Subtype != "success" || len(response.StructuredOutput) == 0 || string(response.StructuredOutput) == "null" {
		return errors.New("call claude: response contains no structured output")
	}
	if err := json.Unmarshal(response.StructuredOutput, target); err != nil {
		return fmt.Errorf("call claude: decode structured response: %w", err)
	}
	return nil
}

func combinePrompt(system, prompt string) string {
	if system = strings.TrimSpace(system); system != "" {
		return system + "\n\n" + prompt
	}
	return prompt
}

func runCommand(ctx context.Context, path string, arguments []string, input, directory string) ([]byte, error) {
	command := exec.CommandContext(ctx, path, arguments...)
	command.Dir = directory
	command.Stdin = strings.NewReader(input)
	var stderr bytes.Buffer
	command.Stderr = &stderr
	output, err := command.Output()
	if ctx.Err() != nil {
		return nil, fmt.Errorf("call %s: %w", filepath.Base(path), ctx.Err())
	}
	if err != nil {
		detail := strings.TrimSpace(stderr.String())
		if len(detail) > 4096 {
			detail = detail[:4096]
		}
		if detail != "" {
			return nil, fmt.Errorf("call %s: %w: %s", filepath.Base(path), err, detail)
		}
		return nil, fmt.Errorf("call %s: %w", filepath.Base(path), err)
	}
	if len(output) > maximumOutput {
		return nil, fmt.Errorf("call %s: response exceeds 4 MiB", filepath.Base(path))
	}
	return output, nil
}

func readLimited(path string) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	content, err := io.ReadAll(io.LimitReader(file, maximumOutput+1))
	if err != nil {
		return nil, err
	}
	if len(content) > maximumOutput {
		return nil, errors.New("response exceeds 4 MiB")
	}
	return content, nil
}
