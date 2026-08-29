package openai

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestClientGeneratesJSONAndEmbeds(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer secret" {
			http.Error(response, "missing authorization", http.StatusUnauthorized)
			return
		}
		switch request.URL.Path {
		case "/v1/chat/completions":
			var body struct {
				ResponseFormat struct {
					JSONSchema struct {
						Strict bool `json:"strict"`
					} `json:"json_schema"`
				} `json:"response_format"`
			}
			if err := json.NewDecoder(request.Body).Decode(&body); err != nil || !body.ResponseFormat.JSONSchema.Strict {
				http.Error(response, "missing strict schema", http.StatusBadRequest)
				return
			}
			_, _ = response.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"skip\"}"}}]}`))
		case "/v1/embeddings":
			_, _ = response.Write([]byte(`{"data":[{"index":1,"embedding":[0,1]},{"index":0,"embedding":[1,0]}]}`))
		default:
			http.NotFound(response, request)
		}
	}))
	defer server.Close()
	client, err := New(server.URL+"/v1", "secret", time.Second)
	if err != nil {
		t.Fatal(err)
	}
	var generated struct {
		Action string `json:"action"`
	}
	if err := client.GenerateJSON(context.Background(), "small", "system", "prompt", map[string]any{"type": "object"}, &generated, 8192, time.Second); err != nil || generated.Action != "skip" {
		t.Fatalf("generate JSON = %#v, %v", generated, err)
	}
	vectors, err := client.Embed(context.Background(), "embed", []string{"first", "second"}, 2)
	if err != nil || len(vectors) != 2 || vectors[0][0] != 1 || vectors[1][1] != 1 {
		t.Fatalf("embeddings = %#v, %v", vectors, err)
	}
}

func TestClientRejectsInsecureRemoteEndpointAndMissingKey(t *testing.T) {
	if _, err := New("http://example.com/v1", "secret", time.Second); err == nil || !strings.Contains(err.Error(), "HTTPS") {
		t.Fatalf("insecure endpoint error = %v", err)
	}
	client, err := New("https://api.openai.com/v1", "", time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if err := client.GenerateJSON(context.Background(), "small", "", "prompt", map[string]any{"type": "object"}, &struct{}{}, 8192, time.Second); err == nil || !strings.Contains(err.Error(), "API_KEY") {
		t.Fatalf("missing key error = %v", err)
	}
}

func TestClientBoundsResponses(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		_, _ = response.Write([]byte(`{"choices":[]}` + strings.Repeat(" ", 4<<20)))
	}))
	defer server.Close()
	client, err := New(server.URL, "", time.Second)
	if err != nil {
		t.Fatal(err)
	}
	err = client.GenerateJSON(context.Background(), "small", "", "prompt", map[string]any{"type": "object"}, &struct{}{}, 8192, time.Second)
	if err == nil || !strings.Contains(err.Error(), "exceeds 4 MiB") {
		t.Fatalf("oversized response error = %v", err)
	}
}
