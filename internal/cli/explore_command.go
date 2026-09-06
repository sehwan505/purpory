package cli

import (
	"context"
	"errors"
	"flag"
	"io"
	"strconv"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/memory"
)

func runExplorationCommand(ctx context.Context, service *product.Service, arguments []string, output io.Writer) error {
	flags := flag.NewFlagSet("explore", flag.ContinueOnError)
	flags.SetOutput(output)
	sessionID := flags.String("session", "", "agent session ID")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	arguments = flags.Args()
	if len(arguments) == 0 {
		return errors.New("explore requires on, off, status, set, delete, link, unlink, history, rollback, or checkpoint")
	}
	switch arguments[0] {
	case "on", "off":
		if len(arguments) != 1 {
			return errors.New("explore on and off accept only --session ID")
		}
		result, err := service.SetExplorationMode(ctx, *sessionID, arguments[0] == "on")
		return writeJSON(output, result, err)
	case "status":
		if len(arguments) != 1 {
			return errors.New("explore status accepts only --session ID")
		}
		result, err := service.Exploration(ctx, *sessionID)
		return writeJSON(output, result, err)
	case "set":
		setFlags := flag.NewFlagSet("explore set", flag.ContinueOnError)
		setFlags.SetOutput(output)
		kind := setFlags.String("kind", string(memory.Note), "memory kind")
		if err := setFlags.Parse(arguments[1:]); err != nil {
			return err
		}
		values := setFlags.Args()
		if len(values) < 2 || len(values) > 3 {
			return errors.New("explore set requires KEY VALUE and optional REASON")
		}
		reason := ""
		if len(values) == 3 {
			reason = values[2]
		}
		result, err := service.SetAgentMemory(ctx, *sessionID, memory.Kind(*kind), values[0], values[1], reason)
		return writeJSON(output, result, err)
	case "delete":
		if len(arguments) != 2 {
			return errors.New("explore delete requires KEY")
		}
		result, err := service.DeleteAgentMemory(ctx, *sessionID, arguments[1])
		return writeJSON(output, result, err)
	case "link":
		if len(arguments) < 4 || len(arguments) > 5 {
			return errors.New("explore link requires SOURCE RELATION TARGET and optional REASON")
		}
		reason := ""
		if len(arguments) == 5 {
			reason = arguments[4]
		}
		result, err := service.SetAgentLink(ctx, *sessionID, arguments[1], arguments[2], arguments[3], reason)
		return writeJSON(output, result, err)
	case "unlink":
		if len(arguments) != 4 {
			return errors.New("explore unlink requires SOURCE RELATION TARGET")
		}
		result, err := service.DeleteAgentLink(ctx, *sessionID, arguments[1], arguments[2], arguments[3])
		return writeJSON(output, result, err)
	case "history":
		limit := 100
		if len(arguments) == 2 {
			parsed, err := strconv.Atoi(arguments[1])
			if err != nil || parsed <= 0 || parsed > 500 {
				return errors.New("explore history limit must be between 1 and 500")
			}
			limit = parsed
		} else if len(arguments) != 1 {
			return errors.New("explore history accepts an optional limit")
		}
		result, err := service.AgentChanges(ctx, limit)
		return writeJSON(output, result, err)
	case "rollback":
		if len(arguments) != 1 {
			return errors.New("explore rollback accepts only --session ID")
		}
		result, err := service.RollbackAgentChanges(ctx, *sessionID)
		return writeJSON(output, result, err)
	case "checkpoint":
		if len(arguments) != 1 {
			return errors.New("explore checkpoint accepts only --session ID")
		}
		result, err := service.CheckpointAgentChanges(ctx, *sessionID)
		return writeJSON(output, result, err)
	default:
		return errors.New("explore requires on, off, status, set, delete, link, unlink, history, rollback, or checkpoint")
	}
}
