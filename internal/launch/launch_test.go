package launch

import (
	"path/filepath"
	"testing"
)

func TestParseAllowsRuntimeFlagsAroundCommandArguments(t *testing.T) {
	root := t.TempDir()
	database := filepath.Join(t.TempDir(), "context.db")
	config, err := Parse([]string{"remember", "decision.database", "--root", root, "--db", database, "--project", "demo", "--value", "SQLite"})
	if err != nil {
		t.Fatal(err)
	}
	if config.Root != root || !config.RootSet || config.DBPath != database || config.ProjectID != "demo" || len(config.Args) != 4 {
		t.Fatalf("unexpected runtime arguments: %#v", config)
	}
}
