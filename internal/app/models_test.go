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

func TestOpenAIProviderDrivesEmbedding(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer test-key" {
			http.Error(response, "unauthorized", http.StatusUnauthorized)
			return
		}
		switch request.URL.Path {
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

	value := "External embeddings are isolated by provider and dimensions."
	if _, err := service.Remember(ctx, "knowledge.providers", memory.Note, &value, nil); err != nil {
		t.Fatal(err)
	}
	selected, err := service.SelectModelProvider(ctx, "embedding", providerOpenAI, "text-embedding", 2)
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
	loaded, err := service.embeddingModel(ctx)
	if err != nil || loaded.Provider != providerOpenAI || loaded.Model != "text-embedding" || loaded.Dimensions != 2 {
		t.Fatalf("provider binding did not reload: %#v, %v", loaded, err)
	}
	cleared, err := service.ClearProviderCredential(ctx, providerOpenAI)
	if err != nil || cleared.CredentialSource != "none" {
		t.Fatalf("provider credential was not cleared: %#v, %v", cleared, err)
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
