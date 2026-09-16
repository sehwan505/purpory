// Package codexoauth uses a separate ChatGPT Codex device-code session for reconciliation.
package codexoauth

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const (
	account     = "provider.openai-codex.oauth"
	clientID    = "app_EMoamEEZ73f0CkXaXp7hrann"
	issuer      = "https://auth.openai.com"
	backend     = "https://chatgpt.com/backend-api/codex"
	maxResponse = 4 << 20
)

type Store interface {
	Get(context.Context, string) (string, bool, error)
	Set(context.Context, string, string) error
	Delete(context.Context, string) error
}

type Tokens struct {
	AccessToken  string `json:"accessToken"`
	RefreshToken string `json:"refreshToken"`
}

type DeviceCode struct {
	URL          string `json:"url"`
	UserCode     string `json:"userCode"`
	DeviceAuthID string `json:"-"`
	Interval     int    `json:"-"`
}

type Client struct {
	store   Store
	http    *http.Client
	issuer  string
	backend string
	openURL func(context.Context, string) error
}

func New(store Store) *Client {
	return &Client{store: store, http: &http.Client{}, issuer: issuer, backend: backend, openURL: openBrowser}
}

func (c *Client) Configured(ctx context.Context) (bool, error) {
	_, found, err := c.load(ctx)
	return found, err
}

func (c *Client) Clear(ctx context.Context) error { return c.store.Delete(ctx, account) }

func (c *Client) Login(ctx context.Context, output io.Writer) error {
	device, err := c.Start(ctx)
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintf(output, "Open %s and enter code %s\n", device.URL, device.UserCode); err != nil {
		return err
	}
	if err := c.openURL(ctx, device.URL); err != nil {
		_, _ = fmt.Fprintln(output, "Could not open a browser automatically; use the URL above.")
	}
	if _, err := fmt.Fprintln(output, "Waiting for sign-in (Ctrl+C to cancel)..."); err != nil {
		return err
	}
	return c.Finish(ctx, device)
}

func (c *Client) Start(ctx context.Context) (DeviceCode, error) {
	var device struct {
		UserCode     string          `json:"user_code"`
		DeviceAuthID string          `json:"device_auth_id"`
		Interval     json.RawMessage `json:"interval"`
	}
	if err := c.postJSON(ctx, c.issuer+"/api/accounts/deviceauth/usercode", map[string]string{"client_id": clientID}, &device); err != nil {
		return DeviceCode{}, fmt.Errorf("Codex login: request device code: %w", err)
	}
	if device.UserCode == "" || device.DeviceAuthID == "" {
		return DeviceCode{}, errors.New("Codex login: incomplete device code response")
	}
	interval, err := strconv.Atoi(strings.Trim(string(device.Interval), `"`))
	if err != nil || interval < 3 {
		interval = 3
	}
	if interval > 30 {
		interval = 30
	}
	return DeviceCode{URL: c.issuer + "/codex/device", UserCode: device.UserCode, DeviceAuthID: device.DeviceAuthID, Interval: interval}, nil
}

func (c *Client) Finish(ctx context.Context, device DeviceCode) error {
	if device.DeviceAuthID == "" || device.UserCode == "" || device.Interval < 3 || device.Interval > 30 {
		return errors.New("Codex login: invalid pending device code")
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()
	for {
		select {
		case <-ctx.Done():
			return fmt.Errorf("Codex login: %w", ctx.Err())
		case <-time.After(time.Duration(device.Interval) * time.Second):
		}
		var code struct {
			AuthorizationCode string `json:"authorization_code"`
			CodeVerifier      string `json:"code_verifier"`
		}
		status, err := c.requestJSON(ctx, c.issuer+"/api/accounts/deviceauth/token", map[string]string{
			"device_auth_id": device.DeviceAuthID, "user_code": device.UserCode,
		}, &code)
		if err != nil {
			return fmt.Errorf("Codex login: poll authorization: %w", err)
		}
		if status == http.StatusForbidden || status == http.StatusNotFound || status == http.StatusTooManyRequests || status >= http.StatusInternalServerError && status <= 599 {
			continue
		}
		if status != http.StatusOK || code.AuthorizationCode == "" || code.CodeVerifier == "" {
			return fmt.Errorf("Codex login: invalid authorization response (status %d)", status)
		}
		var tokens struct {
			AccessToken  string `json:"access_token"`
			RefreshToken string `json:"refresh_token"`
		}
		err = c.postForm(ctx, url.Values{
			"grant_type": {"authorization_code"}, "code": {code.AuthorizationCode},
			"redirect_uri": {c.issuer + "/deviceauth/callback"}, "client_id": {clientID},
			"code_verifier": {code.CodeVerifier},
		}, &tokens)
		if err != nil {
			return fmt.Errorf("Codex login: exchange authorization code: %w", err)
		}
		if tokens.AccessToken == "" || tokens.RefreshToken == "" {
			return errors.New("Codex login: token response missing access or refresh token")
		}
		return c.save(ctx, Tokens{AccessToken: tokens.AccessToken, RefreshToken: tokens.RefreshToken})
	}
}

func openBrowser(ctx context.Context, address string) error {
	var command string
	var arguments []string
	switch runtime.GOOS {
	case "darwin":
		command, arguments = "open", []string{address}
	case "linux":
		command, arguments = "xdg-open", []string{address}
	case "windows":
		command, arguments = "rundll32", []string{"url.dll,FileProtocolHandler", address}
	default:
		return errors.New("no browser opener for this operating system")
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, command, arguments...).Run()
}

func (c *Client) GenerateJSON(ctx context.Context, model, system, prompt string, schema, target any, contextTokens int, timeout time.Duration) error {
	if strings.TrimSpace(model) == "" || strings.TrimSpace(prompt) == "" || schema == nil || target == nil || contextTokens < 1024 || timeout <= 0 {
		return errors.New("call openai-codex: model, prompt, schema, target, context and timeout are required")
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	tokens, found, err := c.load(ctx)
	if err != nil {
		return err
	}
	if !found {
		return errors.New("call openai-codex: sign in with `purpory model provider login openai-codex`")
	}
	if expiring(tokens.AccessToken) {
		tokens, err = c.refresh(ctx, tokens)
		if err != nil {
			return err
		}
	}
	encodedSchema, err := json.Marshal(schema)
	if err != nil {
		return fmt.Errorf("call openai-codex: encode schema: %w", err)
	}
	payload := map[string]any{
		"model": strings.TrimSpace(model), "store": false, "stream": true,
		"instructions": strings.TrimSpace(system) + "\nReturn only the requested JSON object.",
		"input":        []map[string]string{{"role": "user", "content": prompt}},
		"text": map[string]any{"format": map[string]any{
			"type": "json_schema", "name": "purpory", "strict": true, "schema": json.RawMessage(encodedSchema),
		}},
	}
	content, status, err := c.responses(ctx, tokens.AccessToken, payload)
	if err != nil {
		return err
	}
	if status == http.StatusUnauthorized {
		tokens, err = c.refresh(ctx, tokens)
		if err != nil {
			return err
		}
		content, status, err = c.responses(ctx, tokens.AccessToken, payload)
		if err != nil {
			return err
		}
	}
	if status != http.StatusOK {
		return fmt.Errorf("call openai-codex: status %d", status)
	}
	if err := json.Unmarshal(content, target); err != nil {
		return fmt.Errorf("call openai-codex: decode JSON response: %w", err)
	}
	return nil
}

func (c *Client) load(ctx context.Context) (Tokens, bool, error) {
	value, found, err := c.store.Get(ctx, account)
	if err != nil || !found {
		return Tokens{}, found, err
	}
	var tokens Tokens
	if err := json.Unmarshal([]byte(value), &tokens); err != nil || tokens.AccessToken == "" || tokens.RefreshToken == "" {
		return Tokens{}, true, errors.New("load Codex OAuth: invalid stored tokens; sign in again")
	}
	return tokens, true, nil
}

func (c *Client) save(ctx context.Context, tokens Tokens) error {
	value, err := json.Marshal(tokens)
	if err != nil {
		return err
	}
	return c.store.Set(ctx, account, string(value))
}

// ponytail: cron runs one worker; re-read after a failed refresh covers occasional concurrent token rotation.
func (c *Client) refresh(ctx context.Context, old Tokens) (Tokens, error) {
	var result struct {
		AccessToken  string `json:"access_token"`
		RefreshToken string `json:"refresh_token"`
	}
	err := c.postForm(ctx, url.Values{"grant_type": {"refresh_token"}, "refresh_token": {old.RefreshToken}, "client_id": {clientID}}, &result)
	if err != nil {
		current, found, readErr := c.load(ctx)
		if readErr == nil && found && current.RefreshToken != old.RefreshToken {
			return current, nil
		}
		return Tokens{}, fmt.Errorf("refresh Codex OAuth: %w; sign in again if the token was revoked", err)
	}
	if result.AccessToken == "" {
		return Tokens{}, errors.New("refresh Codex OAuth: response missing access token")
	}
	if result.RefreshToken == "" {
		result.RefreshToken = old.RefreshToken
	}
	tokens := Tokens{AccessToken: result.AccessToken, RefreshToken: result.RefreshToken}
	if err := c.save(ctx, tokens); err != nil {
		return Tokens{}, fmt.Errorf("refresh Codex OAuth: save rotated tokens: %w", err)
	}
	return tokens, nil
}

func expiring(token string) bool {
	claims := jwtClaims(token)
	expires, ok := claims["exp"].(float64)
	return !ok || time.Unix(int64(expires), 0).Before(time.Now().Add(2*time.Minute))
}

func jwtClaims(token string) map[string]any {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return nil
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil
	}
	var claims map[string]any
	_ = json.Unmarshal(payload, &claims)
	return claims
}

func accountID(token string) string {
	auth, _ := jwtClaims(token)["https://api.openai.com/auth"].(map[string]any)
	id, _ := auth["chatgpt_account_id"].(string)
	return id
}

func (c *Client) responses(ctx context.Context, token string, value any) ([]byte, int, error) {
	payload, err := json.Marshal(value)
	if err != nil {
		return nil, 0, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.backend+"/responses", bytes.NewReader(payload))
	if err != nil {
		return nil, 0, err
	}
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "text/event-stream")
	request.Header.Set("User-Agent", "Purpory/1.0")
	request.Header.Set("originator", "purpory")
	if id := accountID(token); id != "" {
		request.Header.Set("ChatGPT-Account-ID", id)
	}
	response, err := c.http.Do(request)
	if err != nil {
		return nil, 0, fmt.Errorf("call openai-codex: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return nil, response.StatusCode, nil
	}
	content, err := readStream(response.Body)
	return content, response.StatusCode, err
}

func readStream(reader io.Reader) ([]byte, error) {
	scanner := bufio.NewScanner(io.LimitReader(reader, maxResponse*2+1))
	scanner.Buffer(make([]byte, 4096), maxResponse)
	var output bytes.Buffer
	completed := false
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		data := strings.TrimPrefix(line, "data: ")
		if data == "[DONE]" {
			break
		}
		var event struct {
			Type     string `json:"type"`
			Delta    string `json:"delta"`
			Response struct {
				Status string `json:"status"`
				Output []struct {
					Content []struct {
						Type string `json:"type"`
						Text string `json:"text"`
					} `json:"content"`
				} `json:"output"`
			} `json:"response"`
		}
		if err := json.Unmarshal([]byte(data), &event); err != nil {
			return nil, fmt.Errorf("call openai-codex: invalid stream event: %w", err)
		}
		switch event.Type {
		case "response.output_text.delta":
			if output.Len()+len(event.Delta) > maxResponse {
				return nil, errors.New("call openai-codex: response exceeds 4 MiB")
			}
			output.WriteString(event.Delta)
		case "response.completed":
			completed = true
			if event.Response.Status != "" && event.Response.Status != "completed" {
				return nil, fmt.Errorf("call openai-codex: response %s", event.Response.Status)
			}
			if output.Len() == 0 {
				for _, item := range event.Response.Output {
					for _, part := range item.Content {
						if part.Type == "output_text" {
							output.WriteString(part.Text)
						}
					}
				}
			}
		case "response.failed", "response.incomplete", "error":
			return nil, fmt.Errorf("call openai-codex: %s", event.Type)
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("call openai-codex: read stream: %w", err)
	}
	if !completed || output.Len() == 0 || output.Len() > maxResponse {
		return nil, errors.New("call openai-codex: stream ended without a complete response")
	}
	return output.Bytes(), nil
}

func (c *Client) postJSON(ctx context.Context, endpoint string, value, target any) error {
	status, err := c.requestJSON(ctx, endpoint, value, target)
	if err != nil {
		return err
	}
	if status != http.StatusOK {
		return fmt.Errorf("status %d", status)
	}
	return nil
}

func (c *Client) requestJSON(ctx context.Context, endpoint string, value, target any) (int, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	payload, err := json.Marshal(value)
	if err != nil {
		return 0, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return 0, err
	}
	request.Header.Set("Content-Type", "application/json")
	return c.doJSON(request, target)
}

func (c *Client) postForm(ctx context.Context, values url.Values, target any) error {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.issuer+"/oauth/token", strings.NewReader(values.Encode()))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	status, err := c.doJSON(request, target)
	if err != nil {
		return err
	}
	if status != http.StatusOK {
		return fmt.Errorf("status %d", status)
	}
	return nil
}

func (c *Client) doJSON(request *http.Request, target any) (int, error) {
	response, err := c.http.Do(request)
	if err != nil {
		return 0, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return response.StatusCode, nil
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 64<<10)).Decode(target); err != nil {
		return response.StatusCode, err
	}
	return response.StatusCode, nil
}
