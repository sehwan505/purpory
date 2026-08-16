package ollama

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestClient(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/api/version":
			fmt.Fprint(response, `{"version":"0.11.0"}`)
		case "/api/tags":
			fmt.Fprint(response, `{"models":[{"name":"qwen3:4b","size":42,"modifiedAt":"today"}]}`)
		case "/api/chat":
			var body map[string]any
			_ = json.NewDecoder(request.Body).Decode(&body)
			if body["format"] != nil {
				fmt.Fprint(response, `{"message":{"role":"assistant","content":"{\"answer\":\"hello\"}"}}`)
			} else {
				fmt.Fprint(response, `{"message":{"role":"assistant","content":"hello"}}`)
			}
		case "/api/pull":
			fmt.Fprint(response, `{"status":"success"}`)
		case "/api/embed":
			fmt.Fprint(response, `{"embeddings":[[1,0]]}`)
		default:
			http.NotFound(response, request)
		}
	}))
	defer server.Close()
	client, err := New(server.URL, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if status := client.Status(context.Background()); !status.Available || status.Version != "0.11.0" {
		t.Fatalf("unexpected status: %#v", status)
	}
	if status := client.Start(context.Background(), time.Second); !status.Available {
		t.Fatalf("running server was not detected: %#v", status)
	}
	models, err := client.Models(context.Background())
	if err != nil || len(models) != 1 || models[0].Name != "qwen3:4b" {
		t.Fatalf("unexpected models: %#v, %v", models, err)
	}
	answer, err := client.Chat(context.Background(), "qwen3:4b", "hi")
	if err != nil || answer != "hello" {
		t.Fatalf("unexpected answer: %q, %v", answer, err)
	}
	var structured struct {
		Answer string `json:"answer"`
	}
	if err := client.ChatJSON(context.Background(), "qwen3:4b", "system", "hi", map[string]any{"type": "object"}, &structured, 8192, time.Second); err != nil || structured.Answer != "hello" {
		t.Fatalf("unexpected structured answer: %#v, %v", structured, err)
	}
	if err := client.Pull(context.Background(), "qwen3:4b"); err != nil {
		t.Fatal(err)
	}
	vectors, err := client.Embed(context.Background(), "embed", []string{"hello"}, 2)
	if err != nil || len(vectors) != 1 || vectors[0][0] != 1 {
		t.Fatalf("unexpected embeddings: %#v, %v", vectors, err)
	}
}
