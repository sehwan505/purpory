package app

import (
	"context"
	"errors"
	"os"
	"strings"
	"time"

	"github.com/sehwan505/purpory/internal/ollama"
)

var modelRoles = map[string]struct {
	environment string
	defaultName string
}{
	"gate":      {environment: "PURPORY_GATE_MODEL"},
	"reconcile": {environment: "PURPORY_RECONCILE_MODEL", defaultName: "qwen3.5:9b"},
	"embedding": {environment: "PURPORY_EMBEDDING_MODEL", defaultName: "qwen3-embedding:0.6b"},
}

type ModelSelection struct {
	Role   string `json:"role"`
	Model  string `json:"model,omitempty"`
	Source string `json:"source"`
}

type ModelState struct {
	Ollama ollama.Status    `json:"ollama"`
	Models []ModelSelection `json:"selected"`
}

func (s *Service) modelName(ctx context.Context, role string) (ModelSelection, error) {
	config, found := modelRoles[role]
	if !found {
		return ModelSelection{}, errors.New("select model: role must be gate, reconcile, or embedding")
	}
	if role == "embedding" {
		if value, found, err := s.store.ProjectEmbeddingModel(ctx, s.project.ID); err != nil {
			return ModelSelection{}, err
		} else if found {
			return ModelSelection{Role: role, Model: value, Source: "project"}, nil
		}
	}
	if value := strings.TrimSpace(os.Getenv(config.environment)); value != "" {
		return ModelSelection{Role: role, Model: value, Source: "environment"}, nil
	}
	if role != "embedding" {
		if value, found, err := s.store.Setting(ctx, "model."+role); err != nil {
			return ModelSelection{}, err
		} else if found {
			return ModelSelection{Role: role, Model: value, Source: "setting"}, nil
		}
	}
	return ModelSelection{Role: role, Model: config.defaultName, Source: "default"}, nil
}

func (s *Service) ModelState(ctx context.Context) (ModelState, error) {
	result := ModelState{Ollama: s.ollama.Status(ctx)}
	for _, role := range []string{"gate", "reconcile", "embedding"} {
		selected, err := s.modelName(ctx, role)
		if err != nil {
			return ModelState{}, err
		}
		result.Models = append(result.Models, selected)
	}
	return result, nil
}

func (s *Service) SelectModel(ctx context.Context, role, model string) (ModelSelection, error) {
	role, model = strings.ToLower(strings.TrimSpace(role)), strings.TrimSpace(model)
	if _, found := modelRoles[role]; !found {
		return ModelSelection{}, errors.New("select model: role must be gate, reconcile, or embedding")
	}
	if model == "" || len(model) > 255 {
		return ModelSelection{}, errors.New("select model: model is required")
	}
	if role == "embedding" {
		if err := s.store.SetProjectEmbeddingModel(ctx, s.project.ID, model); err != nil {
			return ModelSelection{}, err
		}
		return ModelSelection{Role: role, Model: model, Source: "project"}, nil
	}
	if err := s.store.SaveSetting(ctx, "model."+role, model); err != nil {
		return ModelSelection{}, err
	}
	selected := ModelSelection{Role: role, Model: model, Source: "setting"}
	if role == "gate" {
		s.gate = newGateProvider(s.ollama, model)
	}
	return selected, nil
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
		return ModelSelection{Model: model, Source: "installed"}, nil
	}
	return s.SelectModel(ctx, role, model)
}
