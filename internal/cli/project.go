package cli

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"strings"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/integration"
	"github.com/sehwan505/purpory/internal/launch"
	"github.com/sehwan505/purpory/internal/store"
)

func runSetupCommand(ctx context.Context, config launch.Config, arguments []string, output io.Writer) error {
	flags := flag.NewFlagSet("setup", flag.ContinueOnError)
	flags.SetOutput(output)
	agent := flags.String("agent", "codex", "codex or claude")
	id := flags.String("id", config.ProjectID, "project ID")
	name := flags.String("name", "", "project name")
	jsonOutput := flags.Bool("json", false, "write the setup result as JSON")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if flags.NArg() > 1 {
		return errors.New("setup accepts at most one project path")
	}
	*agent = strings.ToLower(strings.TrimSpace(*agent))
	if *agent != "codex" && *agent != "claude" {
		return errors.New("setup agent must be codex or claude")
	}
	root := config.Root
	if flags.NArg() == 1 {
		root = flags.Arg(0)
	}
	registered, err := product.RegisterProject(ctx, root, config.DBPath, *id, *name)
	if err != nil {
		return err
	}
	service, err := product.Open(ctx, root, config.DBPath, registered.ID)
	if err != nil {
		return err
	}
	updated, updateErr := service.Update(ctx)
	closeErr := service.Close()
	if updateErr != nil {
		return updateErr
	}
	if closeErr != nil {
		return closeErr
	}
	integrationResult, err := integration.Install(*agent)
	if err != nil {
		return err
	}
	if *jsonOutput {
		return writeJSON(output, map[string]any{"project": registered, "update": updated, "agent": *agent, "integration": integrationResult}, nil)
	}
	if _, err := fmt.Fprintf(output, "Purpory is ready.\nProject: %s (%s)\nIndexed: %d materials, %d knowledge items\nAgent: %s (%s)\nNext: ask the agent a project question; Purpory will offer up to three context paths.\n", registered.Name, registered.Root, updated.MaterialCount, updated.EntityCount, *agent, integrationResult); err != nil {
		return err
	}
	if *agent == "codex" {
		_, err = fmt.Fprintln(output, "Codex: review the installed hooks once with /hooks.")
	}
	return err
}

func runProjectCommand(ctx context.Context, config launch.Config, arguments []string, output io.Writer) error {
	if len(arguments) == 1 && arguments[0] == "list" {
		database, err := store.Open(ctx, config.DBPath)
		if err != nil {
			return err
		}
		defer database.Close()
		values, err := database.Projects(ctx)
		return writeJSON(output, values, err)
	}
	if len(arguments) == 2 && arguments[0] == "remove" {
		database, err := store.Open(ctx, config.DBPath)
		if err != nil {
			return err
		}
		defer database.Close()
		removed, err := database.RemoveProject(ctx, arguments[1])
		return writeJSON(output, removed, err)
	}
	if len(arguments) == 0 || arguments[0] != "add" {
		return errors.New("project requires add [PATH], list, or remove ID")
	}
	flags := flag.NewFlagSet("project add", flag.ContinueOnError)
	flags.SetOutput(output)
	id := flags.String("id", config.ProjectID, "project ID")
	name := flags.String("name", "", "project name")
	if err := flags.Parse(arguments[1:]); err != nil {
		return err
	}
	if flags.NArg() > 1 {
		return errors.New("project add accepts at most one path")
	}
	root := config.Root
	if flags.NArg() == 1 {
		root = flags.Arg(0)
	}
	value, err := product.RegisterProject(ctx, root, config.DBPath, *id, *name)
	return writeJSON(output, value, err)
}
