package cli

import (
	"context"
	"io"
	"path/filepath"
	"strings"
	"testing"
)

func TestRemovedModelDecisionCommandsHaveNoAliases(t *testing.T) {
	service := openCLIService(t, t.TempDir(), filepath.Join(t.TempDir(), "purpory.db"), "demo")
	for _, args := range [][]string{
		{"reconcile"}, {"request", "list"}, {"decision", "list"},
		{"model", "run", "old-model", "hello"},
		{"model", "select", "gate", "old-model"},
		{"model", "select", "reconcile", "old-model"},
		{"model", "install", "old-model", "reconcile"},
		{"model", "provider", "login", "openai-codex"},
		{"session-end", "hermes", "--defer"},
		{"prepare", "--retain-input", "database"},
	} {
		if err := runCLI(context.Background(), service, args, strings.NewReader(""), io.Discard); err == nil {
			t.Fatalf("removed command accepted: %q", args)
		}
	}
}
