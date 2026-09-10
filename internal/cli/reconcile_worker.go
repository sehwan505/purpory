package cli

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/reconcile"
)

func runReconcileCommand(ctx context.Context, arguments []string) error {
	flags := flag.NewFlagSet("reconcile", flag.ContinueOnError)
	executor := flags.String("executor", "", "codex or claude")
	model := flags.String("model", "", "optional executor model")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("reconcile accepts no positional arguments")
	}
	*executor = strings.ToLower(strings.TrimSpace(*executor))
	if *executor != "codex" && *executor != "claude" {
		return errors.New("reconcile requires --executor codex or claude")
	}
	restoreProvider := setEnvironment("PURPORY_RECONCILE_PROVIDER", *executor)
	defer restoreProvider()
	restoreModel := setEnvironment("PURPORY_RECONCILE_MODEL", strings.TrimSpace(*model))
	defer restoreModel()
	return drainReconciliations(ctx)
}

func setEnvironment(key, value string) func() {
	previous, found := os.LookupEnv(key)
	_ = os.Setenv(key, value)
	return func() {
		if found {
			_ = os.Setenv(key, previous)
		} else {
			_ = os.Unsetenv(key)
		}
	}
}

func drainReconciliations(ctx context.Context) error {
	paths, err := reconcile.Pending()
	if err != nil {
		return err
	}
	var failures []error
	for _, path := range paths {
		job, err := reconcile.LoadJob(path)
		if err != nil {
			if rejectErr := reconcile.Reject(path, err); rejectErr != nil {
				failures = append(failures, errors.Join(err, rejectErr))
			}
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
