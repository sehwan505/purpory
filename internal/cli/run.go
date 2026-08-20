package cli

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/integration"
	"github.com/sehwan505/purpory/internal/launch"
	"github.com/sehwan505/purpory/internal/project"
)

func Run(arguments []string, input io.Reader, output, errorOutput io.Writer) int {
	config, err := launch.Parse(arguments)
	if err != nil {
		fmt.Fprintf(errorOutput, "purpory: %v\n", err)
		return 2
	}
	if len(config.Args) == 1 && config.Args[0] == "reconcile-drain" {
		if err := drainReconciliations(context.Background()); err != nil {
			fmt.Fprintf(errorOutput, "purpory: %v\n", err)
			return 1
		}
		return 0
	}
	if len(config.Args) == 1 && (config.Args[0] == "help" || config.Args[0] == "--help" || config.Args[0] == "-h") {
		fmt.Fprintln(output, usage)
		return 0
	}
	if len(config.Args) == 1 && config.Args[0] == "version" {
		fmt.Fprintln(output, product.Version)
		return 0
	}
	if len(config.Args) > 0 && config.Args[0] == "project" {
		if err := runProjectCommand(context.Background(), config, config.Args[1:], output); err != nil {
			fmt.Fprintf(errorOutput, "purpory: %v\n", err)
			return 1
		}
		return 0
	}
	if len(config.Args) > 0 && config.Args[0] == "integration" {
		if err := runIntegrationCommand(config.Args[1:], output); err != nil {
			fmt.Fprintf(errorOutput, "purpory: %v\n", err)
			return 1
		}
		return 0
	}
	if isAgentHook(config.Args) {
		content, readErr := io.ReadAll(io.LimitReader(input, maximumHookInput+1))
		input = bytes.NewReader(content)
		if readErr == nil {
			if payload, parseErr := readHook(bytes.NewReader(content)); parseErr == nil {
				config.Root = payload.CWD
			}
		}
	}
	service, err := product.Open(context.Background(), config.Root, config.DBPath, config.ProjectID)
	if err != nil {
		if errors.Is(err, project.ErrNotRegistered) && isAgentHook(config.Args) {
			return 0
		}
		fmt.Fprintf(errorOutput, "purpory: %v\n", err)
		return 1
	}
	defer service.Close()
	if err := runCLI(context.Background(), service, config.Args, input, output); err != nil {
		fmt.Fprintf(errorOutput, "purpory: %v\n", err)
		return 1
	}
	return 0
}

func runIntegrationCommand(arguments []string, output io.Writer) error {
	if len(arguments) != 2 {
		return errors.New("integration requires agent and install or uninstall")
	}
	var result string
	var err error
	switch arguments[1] {
	case "install":
		result, err = integration.Install(arguments[0])
	case "uninstall":
		result, err = integration.Uninstall(arguments[0])
	default:
		return errors.New("integration requires install or uninstall")
	}
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(output, result)
	return err
}

func isAgentHook(arguments []string) bool {
	return len(arguments) == 2 && (arguments[0] == "preflight" || arguments[0] == "session-end")
}
