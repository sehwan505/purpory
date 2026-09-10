package app

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
)

type memoryCredentials struct {
	mu     sync.Mutex
	values map[string]string
}

func newMemoryCredentials() *memoryCredentials {
	return &memoryCredentials{values: map[string]string{}}
}

func (m *memoryCredentials) Get(_ context.Context, account string) (string, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	value, found := m.values[account]
	return value, found, nil
}

func (m *memoryCredentials) Set(_ context.Context, account, value string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.values[account] = value
	return nil
}

func (m *memoryCredentials) Delete(_ context.Context, account string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.values, account)
	return nil
}

func TestOpenAIProviderDrivesEveryModelRole(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer test-key" {
			http.Error(response, "unauthorized", http.StatusUnauthorized)
			return
		}
		switch request.URL.Path {
		case "/v1/chat/completions":
			var body struct {
				Messages []struct {
					Content string `json:"content"`
				} `json:"messages"`
			}
			if err := json.NewDecoder(request.Body).Decode(&body); err != nil || len(body.Messages) == 0 {
				http.Error(response, "invalid request", http.StatusBadRequest)
				return
			}
			content := `{"action":"skip","query":null,"keywords":[],"reasonCode":"SELF_CONTAINED","clarification":null}`
			if strings.Contains(body.Messages[len(body.Messages)-1].Content, "TRANSCRIPT") {
				content = `{"candidates":[]}`
			}
			_ = json.NewEncoder(response).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]any{"content": content}}}})
		case "/v1/embeddings":
			var body struct {
				Input []string `json:"input"`
			}
			if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
				http.Error(response, "invalid request", http.StatusBadRequest)
				return
			}
			data := make([]map[string]any, len(body.Input))
			for index := range body.Input {
				data[index] = map[string]any{"index": index, "embedding": []float64{1, 0}}
			}
			_ = json.NewEncoder(response).Encode(map[string]any{"data": data})
		default:
			http.NotFound(response, request)
		}
	}))
	t.Cleanup(server.Close)
	ctx := context.Background()
	root := t.TempDir()
	service := openTestService(t, root, filepath.Join(t.TempDir(), "purpory.db"), "demo")
	provider, err := service.ConfigureProvider(ctx, providerOpenAI, server.URL+"/v1", "test-key")
	if err != nil || !provider.Configured || provider.EndpointSource != "setting" || provider.CredentialSource != "database" {
		t.Fatalf("provider configuration = %#v, %v", provider, err)
	}

	if _, err := service.SelectModelProvider(ctx, "gate", providerOpenAI, "small-chat", 8192, 0); err != nil {
		t.Fatal(err)
	}
	prepared, err := service.PrepareContext(ctx, contextprepare.Request{Message: "Say hello", SessionID: "test", WorkingDirectory: root, TokenBudget: 512})
	if err != nil || prepared.Action != "skip" || prepared.Model.ID == nil || *prepared.Model.ID != "openai/small-chat" {
		t.Fatalf("external gate = %#v, %v", prepared, err)
	}

	if _, err := service.SelectModelProvider(ctx, "reconcile", providerOpenAI, "large-chat", 16_384, 0); err != nil {
		t.Fatal(err)
	}
	model, err := service.reconcileModel(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if candidates, err := model.Extract(ctx, "[U000001] hello"); err != nil || len(candidates) != 0 {
		t.Fatalf("external reconcile = %#v, %v", candidates, err)
	}

	value := "External embeddings are isolated by provider and dimensions."
	if _, err := service.Remember(ctx, "knowledge.providers", memory.Note, &value, nil); err != nil {
		t.Fatal(err)
	}
	selected, err := service.SelectModelProvider(ctx, "embedding", providerOpenAI, "text-embedding", 0, 2)
	if err != nil {
		t.Fatal(err)
	}
	if result, err := service.SyncEmbeddings(ctx, 0); err != nil || result.Embedded != 1 || result.Provider != providerOpenAI {
		t.Fatalf("external embedding sync = %#v, %v", result, err)
	}
	stored, err := service.store.Embeddings(ctx, "demo", embeddingIdentity(selected))
	if err != nil || len(stored) != 1 || stored[0].Model != "openai/text-embedding#2" {
		t.Fatalf("external embeddings = %#v, %v", stored, err)
	}

	setting, found, err := service.store.Setting(ctx, "model.embedding")
	if err != nil || !found || strings.Contains(setting, "test-key") {
		t.Fatalf("provider binding was not safely persisted: %q, %v, %v", setting, found, err)
	}
	loaded, err := service.modelName(ctx, "embedding")
	if err != nil || loaded.Provider != providerOpenAI || loaded.Model != "text-embedding" || loaded.Dimensions != 2 {
		t.Fatalf("provider binding did not reload: %#v, %v", loaded, err)
	}
	cleared, err := service.ClearProviderCredential(ctx, providerOpenAI)
	if err != nil || cleared.CredentialSource != "none" {
		t.Fatalf("provider credential was not cleared: %#v, %v", cleared, err)
	}
}

func TestLegacyModelSettingRemainsAnOllamaBinding(t *testing.T) {
	ctx := context.Background()
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	if err := service.store.SaveSetting(ctx, "model.reconcile", "legacy-local"); err != nil {
		t.Fatal(err)
	}
	selected, err := service.modelName(ctx, "reconcile")
	if err != nil || selected.Provider != providerOllama || selected.Model != "legacy-local" || selected.ContextTokens != 32_768 {
		t.Fatalf("legacy selection = %#v, %v", selected, err)
	}
}

func TestAgentReconcileProviderUsesItsConfiguredDefaultModel(t *testing.T) {
	t.Setenv("PURPORY_RECONCILE_PROVIDER", providerCodex)
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	selected, err := service.modelName(context.Background(), "reconcile")
	if err != nil || selected.Provider != providerCodex || selected.Model != "" {
		t.Fatalf("agent selection = %#v, %v", selected, err)
	}
}

func TestProviderEnvironmentOverridesStoredConfiguration(t *testing.T) {
	t.Setenv(openAIEndpointEnvironment, "https://environment.example/v1")
	t.Setenv(openAICredentialEnvironment, "environment-secret")
	service := openTestService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")

	state, err := service.ConfigureProvider(context.Background(), providerOpenAI, "", "")
	if err != nil || state.EndpointSource != "environment" || state.CredentialSource != "environment" || !state.Configured {
		t.Fatalf("environment provider state = %#v, %v", state, err)
	}
	if _, err := service.ConfigureProvider(context.Background(), providerOpenAI, "https://stored.example/v1", ""); err == nil {
		t.Fatal("environment-controlled endpoint was overwritten")
	}
	if _, err := service.ClearProviderCredential(context.Background(), providerOpenAI); err == nil {
		t.Fatal("environment-controlled credential was cleared")
	}
}
