package reconcile

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestQueueSnapshotsAndCompletesIdempotently(t *testing.T) {
	root := t.TempDir()
	t.Setenv("PURPORY_RECONCILE_DIR", filepath.Join(root, "queue"))
	transcript := filepath.Join(root, "session.jsonl")
	if err := os.WriteFile(transcript, []byte("original"), 0o600); err != nil {
		t.Fatal(err)
	}
	first, err := Enqueue("codex", "codex:one", "demo", root, filepath.Join(root, "purpory.db"), transcript, "exit")
	if err != nil {
		t.Fatal(err)
	}
	second, err := Enqueue("codex", "codex:one", "demo", root, filepath.Join(root, "purpory.db"), transcript, "exit")
	if err != nil || first != second {
		t.Fatalf("queue was not idempotent: %q %q %v", first, second, err)
	}
	job, err := LoadJob(first)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(transcript, []byte("changed"), 0o600); err != nil {
		t.Fatal(err)
	}
	snapshot, err := os.ReadFile(job.TranscriptPath)
	if err != nil || string(snapshot) != "original" {
		t.Fatalf("transcript was not snapshotted: %q %v", snapshot, err)
	}
	calls := 0
	if err := Process(first, func(Job) error { calls++; return errors.New("model unavailable") }); err == nil {
		t.Fatal("failed job was reported as complete")
	}
	if _, err := os.Stat(first); err != nil {
		t.Fatalf("failed job was not retained: %v", err)
	}
	if err := Process(first, func(Job) error { calls++; return nil }); err != nil {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatalf("job ran %d times", calls)
	}
}
