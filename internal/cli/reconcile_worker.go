package cli

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/reconcile"
)

func runReconcileCommand(ctx context.Context, arguments []string, output io.Writer) error {
	if len(arguments) > 0 {
		switch arguments[0] {
		case "list":
			if len(arguments) != 1 {
				return errors.New("reconcile list accepts no arguments")
			}
			jobs, err := reconcile.Queue(100)
			return writeJSON(output, jobs, err)
		case "retry":
			if len(arguments) != 2 {
				return errors.New("reconcile retry requires one job ID")
			}
			err := reconcile.Retry(arguments[1])
			return writeJSON(output, map[string]string{"id": arguments[1], "phase": reconcile.PhaseQueued}, err)
		case "discard":
			if len(arguments) != 2 {
				return errors.New("reconcile discard requires a job ID or --failed")
			}
			if arguments[1] == "--failed" {
				count, err := reconcile.DiscardFailed()
				return writeJSON(output, map[string]int{"discarded": count}, err)
			}
			err := reconcile.Discard(arguments[1])
			return writeJSON(output, map[string]string{"id": arguments[1], "phase": "discarded"}, err)
		}
	}
	flags := flag.NewFlagSet("reconcile", flag.ContinueOnError)
	flags.SetOutput(output)
	executor := flags.String("executor", "", "codex, openai-codex, or claude")
	model := flags.String("model", "", "optional executor model")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("reconcile accepts no positional arguments")
	}
	*executor = strings.ToLower(strings.TrimSpace(*executor))
	if *executor != "codex" && *executor != "openai-codex" && *executor != "claude" {
		return errors.New("reconcile requires --executor codex, openai-codex, or claude")
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
