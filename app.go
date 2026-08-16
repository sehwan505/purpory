package main

import (
	"context"
	"time"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	"github.com/sehwan505/purpory/internal/ollama"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
	"github.com/sehwan505/purpory/internal/project"
	"github.com/sehwan505/purpory/internal/store"
)

type App struct {
	ctx     context.Context
	service *product.Service
}

func NewApp(service *product.Service) *App {
	return &App{ctx: context.Background(), service: service}
}

func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
}

func (a *App) Status() product.Status {
	return a.service.Status()
}

func (a *App) Remember(key, kind string, value, source *string) (store.SaveResult, error) {
	return a.service.Remember(a.ctx, key, memory.Kind(kind), value, source)
}

func (a *App) Memories(prefix string) ([]memory.Memory, error) {
	return a.service.Memories(a.ctx, prefix)
}

func (a *App) DeleteMemory(key string) (bool, error) {
	return a.service.DeleteMemory(a.ctx, key)
}

func (a *App) ConfirmMemory(key string) (bool, error) {
	return a.service.ConfirmMemory(a.ctx, key)
}

func (a *App) NeedsReviews(status string) ([]memory.Review, error) {
	return a.service.NeedsReviews(a.ctx, status)
}

func (a *App) ResolveNeedsReview(id int64, outcome string, resultVersionID *int64) (*memory.Review, error) {
	return a.service.ResolveNeedsReview(a.ctx, id, outcome, resultVersionID)
}

func (a *App) ContextRequests(status string) ([]contextprepare.ContextRequest, error) {
	return a.service.ContextRequests(a.ctx, status)
}

func (a *App) ResolveContextRequest(id int64, key string) (bool, error) {
	return a.service.ResolveContextRequest(a.ctx, id, key)
}

func (a *App) ContextDecisions(limit int) ([]contextprepare.Decision, error) {
	return a.service.ContextDecisions(a.ctx, limit)
}

func (a *App) ContextFeedback(feedback contextprepare.Feedback) (contextprepare.Feedback, error) {
	return a.service.ContextFeedback(a.ctx, feedback)
}

func (a *App) Query(query string, limit int) (product.QueryResult, error) {
	return a.service.Query(a.ctx, query, limit)
}

func (a *App) Graph(scope string, limit int) (product.GraphResult, error) {
	return a.service.Graph(a.ctx, scope, limit)
}

func (a *App) Prepare(message string, tokenBudget int) (product.PrepareResult, error) {
	return a.service.Prepare(a.ctx, message, tokenBudget)
}

func (a *App) Explain(query string) (product.ExplainResult, error) {
	return a.service.Explain(a.ctx, query)
}

func (a *App) Path(source, target string) (graph.Path, error) {
	return a.service.Path(a.ctx, source, target)
}

func (a *App) Update() (product.UpdateResult, error) {
	return a.service.Update(a.ctx)
}

func (a *App) ModelState() (product.ModelState, error) {
	return a.service.ModelState(a.ctx)
}

func (a *App) StartModels(waitSeconds int) ollama.Status {
	return a.service.StartModels(a.ctx, time.Duration(waitSeconds)*time.Second)
}

func (a *App) SelectModel(role, model string) (product.ModelSelection, error) {
	return a.service.SelectModel(a.ctx, role, model)
}

func (a *App) InstallModel(model, role string) (product.ModelSelection, error) {
	return a.service.InstallModel(a.ctx, model, role)
}

func (a *App) SyncEmbeddings(limit int) (product.EmbeddingSyncResult, error) {
	return a.service.SyncEmbeddings(a.ctx, limit)
}

func (a *App) EmbeddingStatus() (product.EmbeddingStatus, error) {
	return a.service.EmbeddingStatus(a.ctx)
}

func (a *App) Workspace() (project.Workspace, error) {
	return a.service.Workspace(a.ctx)
}
