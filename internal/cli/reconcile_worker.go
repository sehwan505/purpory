package cli

import (
	"context"
	"errors"
	"fmt"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/reconcile"
)

func drainReconciliations(ctx context.Context) error {
	paths, err := reconcile.Pending()
	if err != nil {
		return err
	}
	var failures []error
	for _, path := range paths {
		job, err := reconcile.LoadJob(path)
		if err != nil {
			failures = append(failures, err)
			continue
		}
		service, err := product.Open(ctx, job.CWD, job.DBPath, job.ProjectID)
		if err == nil {
			err = service.ProcessReconciliation(ctx, path)
			closeErr := service.Close()
			if err == nil {
				err = closeErr
			}
		}
		if errors.Is(err, reconcile.ErrJobLocked) {
			continue
		}
		if err != nil {
			failures = append(failures, fmt.Errorf("reconcile %s: %w", job.ID, err))
		}
	}
	return errors.Join(failures...)
}
