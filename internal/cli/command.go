package cli

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"time"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/memory"
	contextprepare "github.com/sehwan505/purpory/internal/prepare"
)

const usage = "usage: purpory [--root PATH] [--db PATH] [--project ID] <project|remember|request|decision|review|prepare|query|embed|explain|path|update|model|integration|preflight|session-end|session|version>"

func runCLI(ctx context.Context, service *product.Service, arguments []string, input io.Reader, output io.Writer) error {
	if len(arguments) == 0 {
		return errors.New("command is required")
	}
	command := arguments[0]
	switch command {
	case "remember":
		flags := flag.NewFlagSet(command, flag.ContinueOnError)
		flags.SetOutput(output)
		value := flags.String("value", "", "memory value")
		source := flags.String("source", "", "source reference")
		kind := flags.String("kind", string(memory.Note), "note, decision, or reference")
		list := flags.Bool("list", false, "list memories")
		history := flags.Bool("history", false, "list memory versions")
		deleteMemory := flags.Bool("delete", false, "delete a memory")
		confirm := flags.Bool("confirm", false, "confirm a memory is current")
		batch := flags.String("batch", "", "preview or apply a JSON batch file (- for stdin)")
		apply := flags.Bool("apply", false, "apply a batch after optimistic-hash validation")
		session := flags.String("session", "", "reconciliation session ID")
		prefix := flags.String("prefix", "", "key prefix")
		if err := flags.Parse(arguments[1:]); err != nil {
			return err
		}
		if *list {
			if flags.NArg() != 0 || *value != "" || *source != "" || *history {
				return errors.New("remember --list cannot be combined with a key, value, source, or history")
			}
			result, err := service.Memories(ctx, *prefix)
			return writeJSON(output, result, err)
		}
		if *history {
			if flags.NArg() != 1 || *value != "" || *source != "" || *prefix != "" {
				return errors.New("remember --history requires one key and no value, source, or prefix")
			}
			result, err := service.MemoryVersions(ctx, flags.Arg(0))
			return writeJSON(output, result, err)
		}
		if *batch != "" {
			if flags.NArg() != 0 || *value != "" || *source != "" || *prefix != "" || *deleteMemory || *confirm {
				return errors.New("remember --batch cannot be combined with a key or another operation")
			}
			changes, err := readMemoryBatch(input, *batch)
			if err != nil {
				return err
			}
			result, err := service.ReconcileMemoryBatch(ctx, changes, *apply, *session)
			return writeJSON(output, result, err)
		}
		if *apply || *session != "" {
			return errors.New("remember --apply and --session require --batch")
		}
		if *prefix != "" {
			return errors.New("remember --prefix requires --list")
		}
		if flags.NArg() != 1 {
			return errors.New("remember requires one key")
		}
		if *deleteMemory || *confirm {
			if *deleteMemory && *confirm || *value != "" || *source != "" {
				return errors.New("remember --delete or --confirm requires one key and no value or source")
			}
			var result bool
			var err error
			if *deleteMemory {
				result, err = service.DeleteMemory(ctx, flags.Arg(0))
			} else {
				result, err = service.ConfirmMemory(ctx, flags.Arg(0))
			}
			return writeJSON(output, result, err)
		}
		var valuePointer, sourcePointer *string
		if *value != "" {
			valuePointer = value
		}
		if *source != "" {
			sourcePointer = source
		}
		result, err := service.Remember(ctx, flags.Arg(0), memory.Kind(*kind), valuePointer, sourcePointer)
		return writeJSON(output, result, err)
	case "request":
		if len(arguments) == 2 && arguments[1] == "list" {
			result, err := service.ContextRequests(ctx, "")
			return writeJSON(output, result, err)
		}
		if len(arguments) == 3 && arguments[1] == "list" {
			result, err := service.ContextRequests(ctx, arguments[2])
			return writeJSON(output, result, err)
		}
		if len(arguments) == 4 && arguments[1] == "resolve" {
			id, err := strconv.ParseInt(arguments[2], 10, 64)
			if err != nil || id <= 0 {
				return errors.New("request resolve requires a positive request ID")
			}
			result, err := service.ResolveContextRequest(ctx, id, arguments[3])
			return writeJSON(output, result, err)
		}
		return errors.New("request requires list [open|resolved] or resolve ID KEY")
	case "decision":
		if len(arguments) == 2 && arguments[1] == "list" {
			result, err := service.ContextDecisions(ctx, 100)
			return writeJSON(output, result, err)
		}
		if len(arguments) >= 4 && arguments[1] == "feedback" {
			id, err := strconv.ParseInt(arguments[2], 10, 64)
			if err != nil || id <= 0 {
				return errors.New("decision feedback requires a positive decision ID")
			}
			feedback := contextprepare.Feedback{DecisionID: id, Verdict: arguments[3]}
			flags := flag.NewFlagSet("decision feedback", flag.ContinueOnError)
			flags.SetOutput(output)
			expectedAction := flags.String("expected-action", "", "skip, retrieve, or ask")
			note := flags.String("note", "", "feedback note")
			var keys stringList
			flags.Var(&keys, "key", "expected memory key (repeatable)")
			if err := flags.Parse(arguments[4:]); err != nil {
				return err
			}
			if flags.NArg() != 0 {
				return errors.New("decision feedback accepts no extra arguments")
			}
			if *expectedAction != "" {
				feedback.ExpectedAction = expectedAction
			}
			if *note != "" {
				feedback.Note = note
			}
			feedback.ExpectedKeys = keys
			result, err := service.ContextFeedback(ctx, feedback)
			return writeJSON(output, result, err)
		}
		return errors.New("decision requires list or feedback ID VERDICT")
	case "review":
		if len(arguments) >= 2 && arguments[1] == "list" {
			status := ""
			if len(arguments) == 3 {
				status = arguments[2]
			} else if len(arguments) != 2 {
				return errors.New("review list accepts at most one status")
			}
			result, err := service.NeedsReviews(ctx, status)
			return writeJSON(output, result, err)
		}
		if len(arguments) >= 3 && arguments[1] == "create" {
			flags := flag.NewFlagSet("review create", flag.ContinueOnError)
			flags.SetOutput(output)
			sourceType := flags.String("source-type", "", "evidence source type")
			sourceID := flags.String("source-id", "", "evidence source ID")
			contentHash := flags.String("content-hash", "", "evidence content hash")
			reason := flags.String("reason", "", "review reason")
			if err := flags.Parse(arguments[3:]); err != nil {
				return err
			}
			if flags.NArg() != 0 {
				return errors.New("review create accepts no extra arguments")
			}
			result, err := service.CreateNeedsReview(ctx, arguments[2], *sourceType, *sourceID, *contentHash, *reason)
			return writeJSON(output, result, err)
		}
		if len(arguments) >= 4 && arguments[1] == "resolve" {
			id, err := strconv.ParseInt(arguments[2], 10, 64)
			if err != nil || id <= 0 {
				return errors.New("review resolve requires a positive review ID")
			}
			flags := flag.NewFlagSet("review resolve", flag.ContinueOnError)
			flags.SetOutput(output)
			version := flags.Int64("version", 0, "result memory version ID")
			if err := flags.Parse(arguments[4:]); err != nil {
				return err
			}
			var versionID *int64
			if *version > 0 {
				versionID = version
			}
			result, err := service.ResolveNeedsReview(ctx, id, arguments[3], versionID)
			return writeJSON(output, result, err)
		}
		return errors.New("review requires list, create KEY, or resolve ID OUTCOME")
	case "query":
		if len(arguments) != 2 {
			return errors.New("query requires one question")
		}
		result, err := service.Query(ctx, arguments[1], 20)
		return writeJSON(output, result, err)
	case "embed":
		if len(arguments) == 2 && arguments[1] == "status" {
			result, err := service.EmbeddingStatus(ctx)
			return writeJSON(output, result, err)
		}
		limit := 0
		if len(arguments) == 2 {
			parsed, err := strconv.Atoi(arguments[1])
			if err != nil || parsed <= 0 {
				return errors.New("embed limit must be a positive integer")
			}
			limit = parsed
		} else if len(arguments) != 1 {
			return errors.New("embed accepts an optional limit")
		}
		result, err := service.SyncEmbeddings(ctx, limit)
		return writeJSON(output, result, err)
	case "prepare":
		flags := flag.NewFlagSet(command, flag.ContinueOnError)
		flags.SetOutput(output)
		var paths stringList
		flags.Var(&paths, "path", "active project path (repeatable)")
		cwd := flags.String("cwd", "", "active working directory")
		session := flags.String("session", "", "agent session ID")
		budget := flags.Int("budget", 2_000, "context token budget")
		jsonOutput := flags.Bool("json", false, "write the full result as JSON")
		retainInput := flags.Bool("retain-input", false, "retain request text in the decision audit")
		noRetainInput := flags.Bool("no-retain-input", false, "store only the request hash")
		if err := flags.Parse(arguments[1:]); err != nil {
			return err
		}
		if flags.NArg() != 1 {
			return errors.New("prepare requires one message")
		}
		if *retainInput && *noRetainInput {
			return errors.New("prepare --retain-input and --no-retain-input cannot be combined")
		}
		retain := !*noRetainInput
		result, err := service.PrepareContext(ctx, contextprepare.Request{
			Message: flags.Arg(0), SessionID: *session, WorkingDirectory: *cwd,
			ActivePaths: paths, TokenBudget: *budget, RetainInput: retain,
		})
		if err != nil {
			return err
		}
		if *jsonOutput {
			return writeJSON(output, result, nil)
		}
		switch result.Action {
		case "retrieve":
			_, err = fmt.Fprintln(output, contextprepare.RenderHintMap(result.Hints))
			return err
		case "ask":
			if result.Clarification != nil {
				_, err = fmt.Fprintln(output, *result.Clarification)
			}
			return err
		default:
			return nil
		}
	case "explain":
		if len(arguments) < 2 {
			return errors.New("explain requires at least one key or node")
		}
		result, err := service.ExplainMany(ctx, arguments[1:])
		return writeJSON(output, result, err)
	case "path":
		if len(arguments) != 3 {
			return errors.New("path requires source and target")
		}
		result, err := service.Path(ctx, arguments[1], arguments[2])
		return writeJSON(output, result, err)
	case "update":
		flags := flag.NewFlagSet(command, flag.ContinueOnError)
		flags.SetOutput(output)
		jsonOutput := flags.Bool("json", false, "write the update result as JSON")
		if err := flags.Parse(arguments[1:]); err != nil {
			return err
		}
		if flags.NArg() != 0 {
			return errors.New("update accepts no positional arguments")
		}
		result, err := service.Update(ctx)
		if err != nil {
			return err
		}
		if *jsonOutput {
			return writeJSON(output, result, nil)
		}
		_, err = fmt.Fprintf(output, "updated %d materials (%d added, %d modified, %d removed, %d unchanged); extracted %d; %d entities and %d relations",
			result.MaterialCount, result.Changes.Added, result.Changes.Modified, result.Changes.Removed, result.Changes.Unchanged,
			result.Processed, result.EntityCount, result.RelationCount)
		if err == nil && len(result.Warnings) > 0 {
			_, err = fmt.Fprintf(output, "; %d warnings", len(result.Warnings))
		}
		if err == nil {
			_, err = fmt.Fprintln(output)
		}
		for _, warning := range result.Warnings {
			if err == nil {
				_, err = fmt.Fprintf(output, "warning: %s\n", warning)
			}
		}
		return err
	case "model":
		if len(arguments) < 2 {
			return errors.New("model requires status, list, run, install, select, or start")
		}
		switch arguments[1] {
		case "status":
			result, err := service.ModelState(ctx)
			return writeJSON(output, result, err)
		case "list":
			result, err := service.Models(ctx)
			return writeJSON(output, result, err)
		case "run":
			if len(arguments) != 4 {
				return errors.New("model run requires a model and prompt")
			}
			result, err := service.RunModel(ctx, arguments[2], arguments[3])
			if err != nil {
				return err
			}
			_, err = fmt.Fprintln(output, result)
			return err
		case "start":
			wait := 10 * time.Second
			if len(arguments) == 3 {
				seconds, err := strconv.Atoi(arguments[2])
				if err != nil || seconds <= 0 || seconds > 60 {
					return errors.New("model start wait must be 1-60 seconds")
				}
				wait = time.Duration(seconds) * time.Second
			} else if len(arguments) != 2 {
				return errors.New("model start accepts an optional wait in seconds")
			}
			status := service.StartModels(ctx, wait)
			if !status.Available {
				if status.Error == "" {
					return errors.New("model start: ollama did not become available")
				}
				return errors.New(status.Error)
			}
			return writeJSON(output, status, nil)
		case "select":
			if len(arguments) != 4 {
				return errors.New("model select requires a role and model")
			}
			result, err := service.SelectModel(ctx, arguments[2], arguments[3])
			return writeJSON(output, result, err)
		case "install":
			if len(arguments) < 3 || len(arguments) > 4 {
				return errors.New("model install requires a model and optional role")
			}
			role := ""
			if len(arguments) == 4 {
				role = arguments[3]
			}
			result, err := service.InstallModel(ctx, arguments[2], role)
			return writeJSON(output, result, err)
		default:
			return errors.New("model requires status, list, run, install, select, or start")
		}
	case "preflight":
		if len(arguments) != 2 {
			return errors.New("preflight requires codex or claude")
		}
		return runPreflight(ctx, service, arguments[1], input, output)
	case "session-end":
		if len(arguments) != 2 {
			return errors.New("session-end requires codex or claude")
		}
		_, err := runSessionEnd(ctx, service, arguments[1], input)
		if err != nil {
			return err
		}
		return startReconciliation()
	case "session":
		if len(arguments) < 3 || len(arguments) > 4 {
			return errors.New("session requires start or end, an ID, and optionally an agent")
		}
		status := "active"
		if arguments[1] == "end" {
			status = "ended"
		} else if arguments[1] != "start" {
			return errors.New("session requires start or end")
		}
		agent := "unknown"
		if len(arguments) == 4 {
			agent = arguments[3]
		}
		if err := service.SaveSession(ctx, arguments[2], agent, status); err != nil {
			return err
		}
		_, err := fmt.Fprintln(output, status)
		return err
	case "version":
		_, err := fmt.Fprintln(output, service.Status().Version)
		return err
	case "help", "--help", "-h":
		_, err := fmt.Fprintln(output, usage)
		return err
	default:
		return fmt.Errorf("unknown command %q", command)
	}
}

func readMemoryBatch(input io.Reader, path string) ([]memory.BatchChange, error) {
	var reader io.Reader = input
	if path != "-" {
		file, err := os.Open(path)
		if err != nil {
			return nil, fmt.Errorf("read memory batch: %w", err)
		}
		defer file.Close()
		reader = file
	}
	data, err := io.ReadAll(io.LimitReader(reader, 1<<20+1))
	if err != nil {
		return nil, fmt.Errorf("read memory batch: %w", err)
	}
	if len(data) > 1<<20 {
		return nil, errors.New("read memory batch: file exceeds 1 MiB")
	}
	var changes []memory.BatchChange
	if err := json.Unmarshal(data, &changes); err != nil {
		return nil, fmt.Errorf("read memory batch: %w", err)
	}
	return changes, nil
}

type stringList []string

func (s *stringList) String() string { return strings.Join(*s, ",") }

func (s *stringList) Set(value string) error {
	*s = append(*s, value)
	return nil
}

func writeJSON(output io.Writer, value any, err error) error {
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(output)
	encoder.SetIndent("", "  ")
	return encoder.Encode(value)
}
