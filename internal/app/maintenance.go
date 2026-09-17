package app

import (
	"context"

	"github.com/sehwan505/purpory/internal/reconcile"
	"github.com/sehwan505/purpory/internal/store"
)

const maintenanceHistoryLimit = 100

type MaintenanceResult struct {
	store.MaintenanceResult
	PrunedCompletedReconciliations int `json:"prunedCompletedReconciliations"`
}

// Maintain prunes global history and compacts the database.
func Maintain(ctx context.Context, databasePath string) (MaintenanceResult, error) {
	database, err := store.Open(ctx, databasePath)
	if err != nil {
		return MaintenanceResult{}, err
	}
	stored, err := database.Maintain(ctx, maintenanceHistoryLimit)
	closeErr := database.Close()
	if err != nil {
		return MaintenanceResult{}, err
	}
	if closeErr != nil {
		return MaintenanceResult{}, closeErr
	}
	pruned, err := reconcile.PruneCompleted(maintenanceHistoryLimit)
	if err != nil {
		return MaintenanceResult{}, err
	}
	return MaintenanceResult{MaintenanceResult: stored, PrunedCompletedReconciliations: pruned}, nil
}
