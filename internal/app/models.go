package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/sehwan505/purpory/internal/ollama"
	"github.com/sehwan505/purpory/internal/openai"
)

const (
	providerOllama              = "ollama"
	providerOpenAI              = "openai"
	defaultOpenAIURL            = "https://api.openai.com/v1"
	openAIEndpointSetting       = "provider.openai.url"
	openAICredentialAccount     = "provider.openai.api-key"
	openAIEndpointEnvironment   = "PURPORY_OPENAI_BASE_URL"
	openAICredentialEnvironment = "PURPORY_OPENAI_API_KEY"
)

type embedder interface {
	Embed(context.Context, string, []string, int) ([][]float64, error)
}

type credentialStore interface {
	Get(context.Context, string) (string, bool, error)
	Set(context.Context, string, string) error
	Delete(context.Context, string) error
}

type ModelSelection struct {
	Role       string `json:"role"`
	Provider   string `json:"provider"`
	Model      string `json:"model,omitempty"`
	Dimensions int    `json:"dimensions,omitempty"`
	Source     string `json:"source"`
}

type ProviderState struct {
	ID               string `json:"id"`
	Label            string `json:"label"`
	Configured       bool   `json:"configured"`
	Available        bool   `json:"available"`
	Endpoint         string `json:"endpoint"`
	EndpointSource   string `json:"endpointSource"`
	CredentialSource string `json:"credentialSource"`
	Error            string `json:"error,omitempty"`
}

type ModelState struct {
	Ollama    ollama.Status    `json:"ollama"`
	Providers []ProviderState  `json:"providers"`
	Models    []ModelSelection `json:"selected"`
}

type storedModelBinding struct {
	Provider   string `json:"provider"`
	Model      string `json:"model"`
	Dimensions int    `json:"dimensions,omitempty"`
}

func (s *Service) embeddingModel(ctx context.Context) (ModelSelection, error) {
	selected := ModelSelection{Role: "embedding", Provider: providerOllama, Model: "qwen3-embedding:0.6b", Dimensions: 512, Source: "default"}
	if value, found, err := s.store.Setting(ctx, "model.embedding"); err != nil {
		return ModelSelection{}, err
	} else if found {
		var binding storedModelBinding
		if err := json.Unmarshal([]byte(value), &binding); err != nil {
			return ModelSelection{}, fmt.Errorf("load embedding binding: %w", err)
		}
		selected.Provider, selected.Model, selected.Dimensions, selected.Source = binding.Provider, binding.Model, binding.Dimensions, "setting"
	}
	if value := strings.TrimSpace(os.Getenv("PURPORY_EMBEDDING_PROVIDER")); value != "" {
		selected.Provider, selected.Source = strings.ToLower(value), "environment"
	}
	if value := strings.TrimSpace(os.Getenv("PURPORY_EMBEDDING_MODEL")); value != "" {
		selected.Model, selected.Source = value, "environment"
	}
	if raw := strings.TrimSpace(os.Getenv("PURPORY_EMBEDDING_DIMENSIONS")); raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil {
			return ModelSelection{}, errors.New("embedding dimensions must be an integer")
		}
		selected.Dimensions, selected.Source = value, "environment"
	}
	if err := validateModelSelection(selected); err != nil {
		return ModelSelection{}, err
	}
	return selected, nil
}

func validateModelSelection(selected ModelSelection) error {
	if selected.Role != "embedding" {
		return errors.New("model role must be embedding")
	}
	if selected.Provider != providerOllama && selected.Provider != providerOpenAI {
		return errors.New("embedding provider must be ollama or openai")
	}
	if selected.Model == "" || len(selected.Model) > 255 {
		return errors.New("embedding model is required and must be at most 255 characters")
	}
	if selected.Dimensions < 1 || selected.Dimensions > 16_384 {
		return errors.New("embedding dimensions must be between 1 and 16384")
	}
	return nil
}

func (s *Service) ModelState(ctx context.Context) (ModelState, error) {
	ollamaStatus := s.ollama.Status(ctx)
	openAIState := s.openAIState(ctx)
	ollamaEndpointSource := "default"
	if strings.TrimSpace(os.Getenv("PURPORY_OLLAMA_URL")) != "" {
		ollamaEndpointSource = "environment"
	}
	result := ModelState{
		Ollama: ollamaStatus,
		Providers: []ProviderState{
			{ID: providerOllama, Label: "Ollama", Configured: true, Available: ollamaStatus.Available, Endpoint: s.ollamaURL, EndpointSource: ollamaEndpointSource, CredentialSource: "none", Error: ollamaStatus.Error},
			openAIState,
		},
	}
	selected, err := s.embeddingModel(ctx)
	if err != nil {
		return ModelState{}, err
	}
	result.Models = []ModelSelection{selected}
	return result, nil
}

func (s *Service) SelectModel(ctx context.Context, role, model string) (ModelSelection, error) {
	return s.SelectModelProvider(ctx, role, providerOllama, model, 512)
}

func (s *Service) SelectModelProvider(ctx context.Context, role, provider, model string, dimensions int) (ModelSelection, error) {
	if dimensions == 0 {
		dimensions = 512
	}
	selected := ModelSelection{Role: strings.ToLower(strings.TrimSpace(role)), Provider: strings.ToLower(strings.TrimSpace(provider)), Model: strings.TrimSpace(model), Dimensions: dimensions, Source: "setting"}
	if err := validateModelSelection(selected); err != nil {
		return ModelSelection{}, err
	}
	encoded, err := json.Marshal(storedModelBinding{Provider: selected.Provider, Model: selected.Model, Dimensions: selected.Dimensions})
	if err != nil {
		return ModelSelection{}, err
	}
	if err := s.store.SaveSetting(ctx, "model.embedding", string(encoded)); err != nil {
		return ModelSelection{}, err
	}
	return selected, nil
}

func (s *Service) embeddingProvider(ctx context.Context, provider string) (embedder, error) {
	switch provider {
	case providerOllama:
		return s.ollama, nil
	case providerOpenAI:
		return s.openAIClient(ctx)
	default:
		return nil, fmt.Errorf("configure embedding: unknown provider %q", provider)
	}
}

func (s *Service) ConfigureProvider(ctx context.Context, provider, endpoint, apiKey string) (ProviderState, error) {
	provider, endpoint, apiKey = strings.ToLower(strings.TrimSpace(provider)), strings.TrimSpace(endpoint), strings.TrimSpace(apiKey)
	if provider != providerOpenAI {
		return ProviderState{}, errors.New("configure provider: provider must be openai")
	}
	if endpoint == "" {
		current, _, err := s.openAIEndpoint(ctx)
		if err != nil {
			return ProviderState{}, err
		}
		endpoint = current
	}
	if len(endpoint) > 1_024 {
		return ProviderState{}, errors.New("configure provider: endpoint must be at most 1024 characters")
	}
	if _, err := openai.New(endpoint, "validation", 30*time.Second); err != nil {
		return ProviderState{}, err
	}
	if apiKey != "" && len(apiKey) > 2_048 {
		return ProviderState{}, errors.New("configure provider: API key must be at most 2048 characters")
	}
	if value := strings.TrimSpace(os.Getenv(openAIEndpointEnvironment)); value != "" && endpoint != value {
		return ProviderState{}, fmt.Errorf("configure provider: endpoint is controlled by %s", openAIEndpointEnvironment)
	}
	if apiKey != "" && strings.TrimSpace(os.Getenv(openAICredentialEnvironment)) != "" {
		return ProviderState{}, fmt.Errorf("configure provider: credential is controlled by %s", openAICredentialEnvironment)
	}
	var previous string
	var hadPrevious bool
	if apiKey != "" {
		var err error
		previous, hadPrevious, err = s.credentials.Get(ctx, openAICredentialAccount)
		if err != nil {
			return ProviderState{}, err
		}
		if err := s.credentials.Set(ctx, openAICredentialAccount, apiKey); err != nil {
			return ProviderState{}, err
		}
	}
	if strings.TrimSpace(os.Getenv(openAIEndpointEnvironment)) == "" {
		if err := s.store.SaveSetting(ctx, openAIEndpointSetting, endpoint); err != nil {
			var rollbackErr error
			if apiKey != "" {
				if hadPrevious {
					rollbackErr = s.credentials.Set(ctx, openAICredentialAccount, previous)
				} else {
					rollbackErr = s.credentials.Delete(ctx, openAICredentialAccount)
				}
			}
			if rollbackErr != nil {
				return ProviderState{}, errors.Join(err, fmt.Errorf("restore provider credential: %w", rollbackErr))
			}
			return ProviderState{}, err
		}
	}
	return s.openAIState(ctx), nil
}

func (s *Service) ClearProviderCredential(ctx context.Context, provider string) (ProviderState, error) {
	if strings.ToLower(strings.TrimSpace(provider)) != providerOpenAI {
		return ProviderState{}, errors.New("clear provider credential: provider must be openai")
	}
	if strings.TrimSpace(os.Getenv(openAICredentialEnvironment)) != "" {
		return ProviderState{}, fmt.Errorf("clear provider credential: credential is controlled by %s", openAICredentialEnvironment)
	}
	if err := s.credentials.Delete(ctx, openAICredentialAccount); err != nil {
		return ProviderState{}, err
	}
	return s.openAIState(ctx), nil
}

func (s *Service) openAIClient(ctx context.Context) (*openai.Client, error) {
	endpoint, _, err := s.openAIEndpoint(ctx)
	if err != nil {
		return nil, err
	}
	apiKey, _, err := s.openAICredential(ctx)
	if err != nil {
		return nil, err
	}
	return openai.New(endpoint, apiKey, 30*time.Second)
}

func (s *Service) openAIState(ctx context.Context) ProviderState {
	state := ProviderState{ID: providerOpenAI, Label: "OpenAI-compatible", CredentialSource: "none"}
	endpoint, source, err := s.openAIEndpoint(ctx)
	state.Endpoint, state.EndpointSource = endpoint, source
	if err != nil {
		state.Error = err.Error()
		return state
	}
	apiKey, credentialSource, err := s.openAICredential(ctx)
	state.CredentialSource = credentialSource
	if err != nil {
		state.Error = err.Error()
		return state
	}
	client, err := openai.New(endpoint, apiKey, 30*time.Second)
	if err != nil {
		state.Error = err.Error()
		return state
	}
	state.Configured = client.Configured()
	if !state.Configured {
		state.Error = "OpenAI-compatible API credential is not configured"
	}
	return state
}

func (s *Service) openAIEndpoint(ctx context.Context) (string, string, error) {
	if value := strings.TrimSpace(os.Getenv(openAIEndpointEnvironment)); value != "" {
		return value, "environment", nil
	}
	if value, found, err := s.store.Setting(ctx, openAIEndpointSetting); err != nil {
		return "", "", err
	} else if found {
		return value, "setting", nil
	}
	return defaultOpenAIURL, "default", nil
}

func (s *Service) openAICredential(ctx context.Context) (string, string, error) {
	if value := strings.TrimSpace(os.Getenv(openAICredentialEnvironment)); value != "" {
		return value, "environment", nil
	}
	value, found, err := s.credentials.Get(ctx, openAICredentialAccount)
	if err != nil {
		if found {
			return "", "database", err
		}
		return "", "none", err
	}
	if found {
		return value, "database", nil
	}
	return "", "none", nil
}

func (s *Service) StartModels(ctx context.Context, wait time.Duration) ollama.Status {
	if wait <= 0 || wait > time.Minute {
		wait = 10 * time.Second
	}
	return s.ollama.Start(ctx, wait)
}

func (s *Service) InstallModel(ctx context.Context, model, role string) (ModelSelection, error) {
	if role != "" && role != "embedding" {
		return ModelSelection{}, errors.New("model role must be embedding")
	}
	model = strings.TrimSpace(model)
	if model == "" || len(model) > 255 {
		return ModelSelection{}, errors.New("install model: model is required")
	}
	if status := s.StartModels(ctx, 10*time.Second); !status.Available {
		message := status.Error
		if message == "" {
			message = "install model: ollama did not become available"
		}
		return ModelSelection{}, errors.New(message)
	}
	if err := s.ollama.Pull(ctx, model); err != nil {
		return ModelSelection{}, err
	}
	if strings.TrimSpace(role) == "" {
		return ModelSelection{Provider: providerOllama, Model: model, Source: "installed"}, nil
	}
	return s.SelectModel(ctx, role, model)
}
