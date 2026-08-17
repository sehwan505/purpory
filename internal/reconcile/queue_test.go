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

func TestRejectQuarantinesUnreadableJob(t *testing.T) {
	root := filepath.Join(t.TempDir(), "queue")
	t.Setenv("PURPORY_RECONCILE_DIR", root)
	pending, err := queueDirectory("pending")
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(pending, "broken.json")
	if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := Reject(path, errors.New("invalid job")); err != nil {
		t.Fatal(err)
	}
	jobs, err := Pending()
	if err != nil || len(jobs) != 0 {
		t.Fatalf("rejected job remained pending: %#v %v", jobs, err)
	}
	if _, err := os.Stat(filepath.Join(pending, "broken.invalid.json")); err != nil {
		t.Fatalf("rejected job was not preserved: %v", err)
	}
}
