package codexoauth

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type memoryStore map[string]string

func (s memoryStore) Get(_ context.Context, key string) (string, bool, error) {
	value, found := s[key]
	return value, found, nil
}
func (s memoryStore) Set(_ context.Context, key, value string) error { s[key] = value; return nil }
func (s memoryStore) Delete(_ context.Context, key string) error     { delete(s, key); return nil }

func testToken(expires time.Time) string {
	claims, _ := json.Marshal(map[string]any{
		"exp":                         expires.Unix(),
		"https://api.openai.com/auth": map[string]string{"chatgpt_account_id": "acct-test"},
	})
	return "a." + base64.RawURLEncoding.EncodeToString(claims) + ".signature"
}

func TestDeviceLoginRefreshAndStructuredResponse(t *testing.T) {
	store := memoryStore{}
	expired := testToken(time.Now().Add(-time.Hour))
	active := testToken(time.Now().Add(time.Hour))
	var loginCalls, refreshCalls, responseCalls int
	var serverURL string
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/api/accounts/deviceauth/usercode":
			var body map[string]string
			_ = json.NewDecoder(request.Body).Decode(&body)
			if body["client_id"] != clientID {
				t.Errorf("unexpected OAuth client ID: %q", body["client_id"])
			}
			_, _ = response.Write([]byte(`{"user_code":"TEST-CODE","device_auth_id":"device-id","interval":"3"}`))
		case "/api/accounts/deviceauth/token":
			loginCalls++
			if loginCalls == 1 {
				response.WriteHeader(http.StatusForbidden)
				return
			}
			if loginCalls == 2 {
				response.WriteHeader(http.StatusGatewayTimeout)
				return
			}
			_, _ = response.Write([]byte(`{"authorization_code":"auth-code","code_verifier":"verifier"}`))
		case "/oauth/token":
			_ = request.ParseForm()
			if request.Form.Get("client_id") != clientID {
				t.Error("token exchange used wrong client")
			}
			switch request.Form.Get("grant_type") {
			case "authorization_code":
				if request.Form.Get("code_verifier") != "verifier" || request.Form.Get("redirect_uri") != serverURL+"/deviceauth/callback" {
					t.Error("authorization-code exchange did not include verifier and redirect URI")
				}
				_, _ = fmt.Fprintf(response, `{"access_token":%q,"refresh_token":"refresh-one","id_token":%q}`, expired, strings.Repeat("x", 5000))
			case "refresh_token":
				refreshCalls++
				if request.Form.Get("refresh_token") != "refresh-one" {
					t.Error("refresh did not use the stored token")
				}
				_, _ = fmt.Fprintf(response, `{"access_token":%q,"refresh_token":"refresh-two"}`, active)
			default:
				t.Error("unexpected token grant")
			}
		case "/backend-api/codex/responses":
			responseCalls++
			if request.Header.Get("Authorization") != "Bearer "+active || request.Header.Get("ChatGPT-Account-ID") != "acct-test" || request.Header.Get("originator") != "purpory" {
				t.Error("Codex request lacks OAuth identity headers")
			}
			var body struct {
				Model  string `json:"model"`
				Stream bool   `json:"stream"`
				Store  bool   `json:"store"`
				Text   struct {
					Format struct {
						Type   string         `json:"type"`
						Schema map[string]any `json:"schema"`
					} `json:"format"`
				} `json:"text"`
			}
			if err := json.NewDecoder(request.Body).Decode(&body); err != nil || body.Model != "gpt-test" || !body.Stream || body.Store || body.Text.Format.Type != "json_schema" || body.Text.Format.Schema["type"] != "object" {
				t.Errorf("invalid structured request: %#v %v", body, err)
			}
			response.Header().Set("Content-Type", "text/event-stream")
			_, _ = response.Write([]byte("data: {\"type\":\"response.output_text.delta\",\"delta\":\"{\\\"ok\\\":true}\"}\n\n"))
			_, _ = response.Write([]byte("data: {\"type\":\"response.completed\",\"response\":{\"status\":\"completed\"}}\n\n"))
		default:
			t.Errorf("unexpected path %s", request.URL.Path)
		}
	}))
	defer server.Close()
	serverURL = server.URL
	client := New(store)
	client.issuer = server.URL
	client.backend = server.URL + "/backend-api/codex"
	client.http = server.Client()
	var openedURL string
	client.openURL = func(_ context.Context, address string) error { openedURL = address; return nil }
	var output bytes.Buffer
	if err := client.Login(context.Background(), &output); err != nil {
		t.Fatal(err)
	}
	if loginCalls != 3 || openedURL != server.URL+"/codex/device" || !strings.Contains(output.String(), "TEST-CODE") {
		t.Fatalf("device login did not complete: calls=%d output=%q", loginCalls, output.String())
	}
	var result struct {
		OK bool `json:"ok"`
	}
	if err := client.GenerateJSON(context.Background(), "gpt-test", "system", "prompt", map[string]any{"type": "object"}, &result, 8192, 15*time.Second); err != nil {
		t.Fatal(err)
	}
	if !result.OK || refreshCalls != 1 || responseCalls != 1 {
		t.Fatalf("OAuth Responses path failed: result=%#v refresh=%d responses=%d", result, refreshCalls, responseCalls)
	}
	tokens, found, err := client.load(context.Background())
	if err != nil || !found || tokens.RefreshToken != "refresh-two" {
		t.Fatalf("rotated refresh token was not persisted: %#v %v", tokens, err)
	}
}

func TestIncompleteStreamFails(t *testing.T) {
	_, err := readStream(strings.NewReader("data: {\"type\":\"response.output_text.delta\",\"delta\":\"{}\"}\n"))
	if err == nil {
		t.Fatal("incomplete Codex stream was accepted")
	}
}

func TestStartDeviceCodeDoesNotExposePollingCredential(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		_, _ = response.Write([]byte(`{"user_code":"TEST-CODE","device_auth_id":"private-device-id","interval":"3"}`))
	}))
	defer server.Close()
	client := New(memoryStore{})
	client.issuer = server.URL
	client.http = server.Client()
	device, err := client.Start(context.Background())
	if err != nil || device.UserCode != "TEST-CODE" || device.DeviceAuthID == "" {
		t.Fatalf("device start = %#v %v", device, err)
	}
	public, err := json.Marshal(device)
	if err != nil || bytes.Contains(public, []byte("private-device-id")) || bytes.Contains(public, []byte("interval")) {
		t.Fatalf("device polling credential leaked to UI: %s %v", public, err)
	}
}
