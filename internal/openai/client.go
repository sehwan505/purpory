// Package openai integrates with OpenAI-compatible HTTP APIs.
package openai

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Client struct {
	baseURL string
	apiKey  string
	http    *http.Client
}

func New(baseURL, apiKey string, timeout time.Duration) (*Client, error) {
	parsed, err := url.Parse(strings.TrimRight(strings.TrimSpace(baseURL), "/"))
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return nil, fmt.Errorf("configure openai: invalid URL %q", baseURL)
	}
	if parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, fmt.Errorf("configure openai: URL must not contain credentials, a query, or a fragment")
	}
	if parsed.Scheme != "https" && !loopback(parsed.Hostname()) {
		return nil, errorsInsecureEndpoint
	}
	if timeout <= 0 {
		return nil, fmt.Errorf("configure openai: timeout must be positive")
	}
	return &Client{baseURL: parsed.String(), apiKey: strings.TrimSpace(apiKey), http: &http.Client{Timeout: timeout}}, nil
}

var errorsInsecureEndpoint = errors.New("configure openai: remote endpoint must use HTTPS")

func loopback(host string) bool {
	ip := net.ParseIP(host)
	return strings.EqualFold(host, "localhost") || ip != nil && ip.IsLoopback()
}

func (c *Client) Configured() bool {
	parsed, _ := url.Parse(c.baseURL)
	return c.apiKey != "" || parsed != nil && loopback(parsed.Hostname())
}

func (c *Client) Embed(ctx context.Context, model string, texts []string, dimensions int) ([][]float64, error) {
	model = strings.TrimSpace(model)
	if model == "" || len(texts) == 0 || dimensions <= 0 {
		return nil, fmt.Errorf("embed with openai: model, input, and dimensions are required")
	}
	for _, value := range texts {
		if strings.TrimSpace(value) == "" {
			return nil, fmt.Errorf("embed with openai: input cannot be empty")
		}
	}
	var result struct {
		Data []struct {
			Index     int       `json:"index"`
			Embedding []float64 `json:"embedding"`
		} `json:"data"`
	}
	if err := c.post(ctx, "/embeddings", map[string]any{"model": model, "input": texts, "dimensions": dimensions}, &result, 2*time.Minute); err != nil {
		return nil, err
	}
	if len(result.Data) != len(texts) {
		return nil, fmt.Errorf("embed with openai: expected %d vectors, got %d", len(texts), len(result.Data))
	}
	vectors := make([][]float64, len(texts))
	for _, item := range result.Data {
		if item.Index < 0 || item.Index >= len(vectors) || vectors[item.Index] != nil {
			return nil, fmt.Errorf("embed with openai: invalid vector index %d", item.Index)
		}
		if len(item.Embedding) != dimensions {
			return nil, fmt.Errorf("embed with openai: expected %d dimensions, got %d", dimensions, len(item.Embedding))
		}
		for _, value := range item.Embedding {
			if math.IsNaN(value) || math.IsInf(value, 0) {
				return nil, fmt.Errorf("embed with openai: vector contains a non-finite value")
			}
		}
		vectors[item.Index] = item.Embedding
	}
	return vectors, nil
}

func (c *Client) post(ctx context.Context, path string, value, target any, timeout time.Duration) error {
	if !c.Configured() {
		return fmt.Errorf("configure openai: an API key is required for remote endpoints")
	}
	payload, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("call openai: encode request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("call openai: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	if c.apiKey != "" {
		request.Header.Set("Authorization", "Bearer "+c.apiKey)
	}
	client := *c.http
	client.Timeout = timeout
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("call openai: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return fmt.Errorf("call openai: status %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20+1))
	if err != nil {
		return fmt.Errorf("call openai: read response: %w", err)
	}
	if len(body) > 4<<20 {
		return fmt.Errorf("call openai: response exceeds 4 MiB")
	}
	if err := json.Unmarshal(body, target); err != nil {
		return fmt.Errorf("call openai: decode response: %w", err)
	}
	return nil
}
