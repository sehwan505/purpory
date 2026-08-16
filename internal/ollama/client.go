// Package ollama integrates with the local Ollama HTTP API.
package ollama

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Client struct {
	baseURL string
	http    *http.Client
}

type Status struct {
	Available bool   `json:"available"`
	Version   string `json:"version,omitempty"`
	Error     string `json:"error,omitempty"`
}

type Model struct {
	Name       string `json:"name"`
	Size       int64  `json:"size"`
	ModifiedAt string `json:"modifiedAt"`
}

func (c *Client) Pull(ctx context.Context, model string) error {
	model = strings.TrimSpace(model)
	if model == "" {
		return fmt.Errorf("pull ollama model: model is required")
	}
	return c.postTimeout(ctx, "/api/pull", map[string]any{"model": model, "stream": false}, &struct{}{}, 30*time.Minute)
}

func (c *Client) Embed(ctx context.Context, model string, texts []string, dimensions int) ([][]float64, error) {
	model = strings.TrimSpace(model)
	if model == "" || len(texts) == 0 || dimensions <= 0 {
		return nil, fmt.Errorf("embed with ollama: model, input, and dimensions are required")
	}
	for _, value := range texts {
		if strings.TrimSpace(value) == "" {
			return nil, fmt.Errorf("embed with ollama: input cannot be empty")
		}
	}
	var result struct {
		Embeddings [][]float64 `json:"embeddings"`
	}
	if err := c.postTimeout(ctx, "/api/embed", map[string]any{"model": model, "input": texts, "dimensions": dimensions}, &result, 2*time.Minute); err != nil {
		return nil, err
	}
	if len(result.Embeddings) != len(texts) {
		return nil, fmt.Errorf("embed with ollama: expected %d vectors, got %d", len(texts), len(result.Embeddings))
	}
	for _, vector := range result.Embeddings {
		if len(vector) != dimensions {
			return nil, fmt.Errorf("embed with ollama: expected %d dimensions, got %d", dimensions, len(vector))
		}
		for _, value := range vector {
			if math.IsNaN(value) || math.IsInf(value, 0) {
				return nil, fmt.Errorf("embed with ollama: vector contains a non-finite value")
			}
		}
	}
	return result.Embeddings, nil
}

func New(baseURL string, timeout time.Duration) (*Client, error) {
	parsed, err := url.Parse(strings.TrimRight(strings.TrimSpace(baseURL), "/"))
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return nil, fmt.Errorf("configure ollama: invalid URL %q", baseURL)
	}
	return &Client{baseURL: parsed.String(), http: &http.Client{Timeout: timeout}}, nil
}

func (c *Client) Status(ctx context.Context) Status {
	var result struct {
		Version string `json:"version"`
	}
	if err := c.get(ctx, "/api/version", &result); err != nil {
		return Status{Error: err.Error()}
	}
	return Status{Available: true, Version: result.Version}
}

func (c *Client) Models(ctx context.Context) ([]Model, error) {
	var result struct {
		Models []Model `json:"models"`
	}
	if err := c.get(ctx, "/api/tags", &result); err != nil {
		return nil, err
	}
	return result.Models, nil
}

func (c *Client) Chat(ctx context.Context, model, prompt string) (string, error) {
	model = strings.TrimSpace(model)
	prompt = strings.TrimSpace(prompt)
	if model == "" || prompt == "" {
		return "", fmt.Errorf("call ollama: model and prompt are required")
	}
	payload, err := json.Marshal(map[string]any{
		"model":    model,
		"messages": []map[string]string{{"role": "user", "content": prompt}},
		"stream":   false,
	})
	if err != nil {
		return "", fmt.Errorf("call ollama: encode request: %w", err)
	}
	var result struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/chat", bytes.NewReader(payload))
	if err != nil {
		return "", fmt.Errorf("call ollama: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	if err := c.do(request, &result); err != nil {
		return "", err
	}
	return result.Message.Content, nil
}

func (c *Client) ChatJSON(ctx context.Context, model, system, prompt string, schema, target any, contextTokens int, timeout time.Duration) error {
	model = strings.TrimSpace(model)
	prompt = strings.TrimSpace(prompt)
	if model == "" || prompt == "" || target == nil || contextTokens < 1024 || timeout <= 0 {
		return fmt.Errorf("call ollama: valid model, prompt, target, context, and timeout are required")
	}
	messages := []map[string]string{}
	if strings.TrimSpace(system) != "" {
		messages = append(messages, map[string]string{"role": "system", "content": system})
	}
	messages = append(messages, map[string]string{"role": "user", "content": prompt})
	payload, err := json.Marshal(map[string]any{
		"model": model, "messages": messages, "stream": false, "format": schema, "think": false,
		"keep_alive": "120s", "options": map[string]any{"temperature": 0, "num_ctx": contextTokens, "num_predict": min(8192, contextTokens/3)},
	})
	if err != nil {
		return fmt.Errorf("call ollama: encode request: %w", err)
	}
	var result struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/chat", bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("call ollama: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	client := *c.http
	client.Timeout = timeout
	if err := do(&client, request, &result); err != nil {
		return err
	}
	if err := json.Unmarshal([]byte(result.Message.Content), target); err != nil {
		return fmt.Errorf("call ollama: decode structured response: %w", err)
	}
	return nil
}

func (c *Client) get(ctx context.Context, path string, target any) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return fmt.Errorf("call ollama: %w", err)
	}
	return c.do(request, target)
}

func (c *Client) postTimeout(ctx context.Context, path string, value, target any, timeout time.Duration) error {
	payload, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("call ollama: encode request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("call ollama: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	client := *c.http
	client.Timeout = timeout
	return do(&client, request, target)
}

func (c *Client) do(request *http.Request, target any) error {
	return do(c.http, request, target)
}

func do(client *http.Client, request *http.Request, target any) error {
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("call ollama: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return fmt.Errorf("call ollama: status %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 4<<20)).Decode(target); err != nil {
		return fmt.Errorf("call ollama: decode response: %w", err)
	}
	return nil
}
