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
)

const (
	providerOllama = "ollama"
	providerOpenAI = "openai"
)

var modelRoles = map[string]struct {
	providerEnvironment  string
	modelEnvironment     string
	parameterEnvironment string
	defaultModel         string
	defaultContext       int
	defaultDimensions    int
}{
	"gate": {
		providerEnvironment: "PURPORY_GATE_PROVIDER", modelEnvironment: "PURPORY_GATE_MODEL",
		parameterEnvironment: "PURPORY_GATE_CONTEXT_TOKENS", defaultContext: 8_192,
	},
	"reconcile": {
		providerEnvironment: "PURPORY_RECONCILE_PROVIDER", modelEnvironment: "PURPORY_RECONCILE_MODEL",
		parameterEnvironment: "PURPORY_RECONCILE_CONTEXT_TOKENS", defaultModel: "qwen3.5:9b", defaultContext: 32_768,
	},
	"embedding": {
		providerEnvironment: "PURPORY_EMBEDDING_PROVIDER", modelEnvironment: "PURPORY_EMBEDDING_MODEL",
		parameterEnvironment: "PURPORY_EMBEDDING_DIMENSIONS", defaultModel: "qwen3-embedding:0.6b", defaultDimensions: 512,
	},
}

type structuredGenerator interface {
	GenerateJSON(context.Context, string, string, string, any, any, int, time.Duration) error
}

type embedder interface {
	Embed(context.Context, string, []string, int) ([][]float64, error)
}

type ModelSelection struct {
	Role          string `json:"role"`
	Provider      string `json:"provider"`
	Model         string `json:"model,omitempty"`
	ContextTokens int    `json:"contextTokens,omitempty"`
	Dimensions    int    `json:"dimensions,omitempty"`
	Source        string `json:"source"`
}

type ProviderState struct {
	ID         string `json:"id"`
	Label      string `json:"label"`
	Configured bool   `json:"configured"`
	Available  bool   `json:"available"`
	Endpoint   string `json:"endpoint"`
	Error      string `json:"error,omitempty"`
}

type ModelState struct {
	Ollama    ollama.Status    `json:"ollama"`
	Providers []ProviderState  `json:"providers"`
	Models    []ModelSelection `json:"selected"`
}

type storedModelBinding struct {
	Provider      string `json:"provider"`
	Model         string `json:"model"`
	ContextTokens int    `json:"contextTokens,omitempty"`
	Dimensions    int    `json:"dimensions,omitempty"`
}

func (s *Service) modelName(ctx context.Context, role string) (ModelSelection, error) {
	role = strings.ToLower(strings.TrimSpace(role))
	config, found := modelRoles[role]
	if !found {
		return ModelSelection{}, errors.New("select model: role must be gate, reconcile, or embedding")
	}
	selected := ModelSelection{
		Role: role, Provider: providerOllama, Model: config.defaultModel,
		ContextTokens: config.defaultContext, Dimensions: config.defaultDimensions, Source: "default",
	}
	if value, found, err := s.store.Setting(ctx, "model."+role); err != nil {
		return ModelSelection{}, err
	} else if found {
		selected.Source = "setting"
		if strings.HasPrefix(strings.TrimSpace(value), "{") {
			var binding storedModelBinding
			if err := json.Unmarshal([]byte(value), &binding); err != nil {
				return ModelSelection{}, fmt.Errorf("load model binding: %w", err)
			}
			selected.Provider, selected.Model = binding.Provider, binding.Model
			if binding.ContextTokens > 0 {
				selected.ContextTokens = binding.ContextTokens
			}
			if binding.Dimensions > 0 {
				selected.Dimensions = binding.Dimensions
			}
		} else {
			selected.Model = value // Legacy model-only settings belong to Ollama.
		}
	}
	if value := strings.TrimSpace(os.Getenv(config.providerEnvironment)); value != "" {
		selected.Provider = strings.ToLower(value)
		selected.Source = "environment"
	}
	if value := strings.TrimSpace(os.Getenv(config.modelEnvironment)); value != "" {
		selected.Model = value
		selected.Source = "environment"
	}
	if raw := strings.TrimSpace(os.Getenv(config.parameterEnvironment)); raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil {
			return ModelSelection{}, fmt.Errorf("configure %s model: %s must be an integer", role, config.parameterEnvironment)
		}
		if role == "embedding" {
			selected.Dimensions = value
		} else {
			selected.ContextTokens = value
		}
		selected.Source = "environment"
	}
	if err := validateModelSelection(selected, false); err != nil {
		return ModelSelection{}, err
	}
	return selected, nil
}

func validateModelSelection(selected ModelSelection, requireModel bool) error {
	if _, found := modelRoles[selected.Role]; !found {
		return errors.New("select model: role must be gate, reconcile, or embedding")
	}
	if selected.Provider != providerOllama && selected.Provider != providerOpenAI {
		return errors.New("select model: provider must be ollama or openai")
	}
	if len(selected.Model) > 255 || requireModel && strings.TrimSpace(selected.Model) == "" {
		return errors.New("select model: model is required")
	}
	if selected.Role == "embedding" {
		if selected.Dimensions < 1 || selected.Dimensions > 16_384 {
			return errors.New("select model: embedding dimensions must be between 1 and 16384")
		}
	} else if selected.ContextTokens < 1_024 || selected.ContextTokens > 1_048_576 {
		return errors.New("select model: context tokens must be between 1024 and 1048576")
	} else if selected.Role == "reconcile" && selected.ContextTokens < 8_192 {
		return errors.New("select model: reconcile context tokens must be between 8192 and 1048576")
	}
	return nil
}

func (s *Service) ModelState(ctx context.Context) (ModelState, error) {
	ollamaStatus := s.ollama.Status(ctx)
	result := ModelState{
		Ollama: ollamaStatus,
		Providers: []ProviderState{
			{ID: providerOllama, Label: "Ollama", Configured: true, Available: ollamaStatus.Available, Endpoint: s.ollamaURL, Error: ollamaStatus.Error},
			{ID: providerOpenAI, Label: "OpenAI-compatible", Configured: s.openAI.Configured(), Endpoint: s.openAIURL},
		},
	}
	if !s.openAI.Configured() {
		result.Providers[1].Error = "PURPORY_OPENAI_API_KEY is not configured"
	}
	for _, role := range []string{"gate", "reconcile", "embedding"} {
		selected, err := s.modelName(ctx, role)
		if err != nil {
			return ModelState{}, err
		}
		result.Models = append(result.Models, selected)
	}
	return result, nil
}

// SelectModel keeps the original CLI/API behavior: a model-only selection uses Ollama.
func (s *Service) SelectModel(ctx context.Context, role, model string) (ModelSelection, error) {
	config, found := modelRoles[strings.ToLower(strings.TrimSpace(role))]
	if !found {
		return ModelSelection{}, errors.New("select model: role must be gate, reconcile, or embedding")
	}
	return s.SelectModelProvider(ctx, role, providerOllama, model, config.defaultContext, config.defaultDimensions)
}

func (s *Service) SelectModelProvider(ctx context.Context, role, provider, model string, contextTokens, dimensions int) (ModelSelection, error) {
	role, provider, model = strings.ToLower(strings.TrimSpace(role)), strings.ToLower(strings.TrimSpace(provider)), strings.TrimSpace(model)
	config, found := modelRoles[role]
	if !found {
		return ModelSelection{}, errors.New("select model: role must be gate, reconcile, or embedding")
	}
	if contextTokens == 0 {
		contextTokens = config.defaultContext
	}
	if dimensions == 0 {
		dimensions = config.defaultDimensions
	}
	selected := ModelSelection{Role: role, Provider: provider, Model: model, ContextTokens: contextTokens, Dimensions: dimensions, Source: "setting"}
	if err := validateModelSelection(selected, true); err != nil {
		return ModelSelection{}, err
	}
	encoded, err := json.Marshal(storedModelBinding{Provider: provider, Model: model, ContextTokens: contextTokens, Dimensions: dimensions})
	if err != nil {
		return ModelSelection{}, fmt.Errorf("save model binding: %w", err)
	}
	if err := s.store.SaveSetting(ctx, "model."+role, string(encoded)); err != nil {
		return ModelSelection{}, err
	}
	if role == "gate" {
		s.gate = s.newGateProvider(selected)
	}
	return selected, nil
}

func (s *Service) generator(provider string) (structuredGenerator, error) {
	switch provider {
	case providerOllama:
		return s.ollama, nil
	case providerOpenAI:
		return s.openAI, nil
	default:
		return nil, fmt.Errorf("configure model: unknown provider %q", provider)
	}
}

func (s *Service) embeddingProvider(provider string) (embedder, error) {
	switch provider {
	case providerOllama:
		return s.ollama, nil
	case providerOpenAI:
		return s.openAI, nil
	default:
		return nil, fmt.Errorf("configure embedding: unknown provider %q", provider)
	}
}

func (s *Service) StartModels(ctx context.Context, wait time.Duration) ollama.Status {
	if wait <= 0 || wait > time.Minute {
		wait = 10 * time.Second
	}
	return s.ollama.Start(ctx, wait)
}

func (s *Service) InstallModel(ctx context.Context, model, role string) (ModelSelection, error) {
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
