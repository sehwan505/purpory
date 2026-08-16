package cli

import (
	"context"
	"errors"
	"flag"
	"io"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/launch"
	"github.com/sehwan505/purpory/internal/store"
)

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
	if err := flags.Parse(normalizeProjectAddArguments(arguments[1:])); err != nil {
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

func normalizeProjectAddArguments(arguments []string) []string {
	var options, positional []string
	for index := 0; index < len(arguments); index++ {
		argument := arguments[index]
		if argument == "--id" || argument == "--name" {
			options = append(options, argument)
			if index+1 < len(arguments) {
				index++
				options = append(options, arguments[index])
			}
			continue
		}
		positional = append(positional, argument)
	}
	return append(options, positional...)
}
