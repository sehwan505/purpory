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
	first, err := Enqueue("hermes", "hermes:one", "demo", root, filepath.Join(root, "purpory.db"), transcript, "exit")
	if err != nil {
		t.Fatal(err)
	}
	second, err := Enqueue("hermes", "hermes:one", "demo", root, filepath.Join(root, "purpory.db"), transcript, "exit")
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
	runs, err := Runs("demo", 20)
	if err != nil || len(runs) != 1 || runs[0].Phase != PhaseFailed || runs[0].Detail != "model unavailable" || runs[0].CWD != root || runs[0].Reason != "exit" {
		t.Fatalf("failed run was not visible: %#v %v", runs, err)
	}
	if pending, err := Pending(); err != nil || len(pending) != 0 {
		t.Fatalf("failed job remained in automatic queue: %#v %v", pending, err)
	}
	if err := Process(first, func(Job) error { calls++; return nil }); err != nil || calls != 1 {
		t.Fatalf("failed job was processed without retry: calls=%d err=%v", calls, err)
	}
	if err := Retry(job.ID); err != nil {
		t.Fatal(err)
	}
	if pending, err := Pending(); err != nil || len(pending) != 1 || pending[0] != first {
		t.Fatalf("retried job was not queued: %#v %v", pending, err)
	}
	if err := Process(first, func(Job) error { calls++; return SetPhase(first, PhaseApplying, "후보 1개 저장") }); err != nil {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatalf("job ran %d times", calls)
	}
	runs, err = Runs("demo", 20)
	if err != nil || len(runs) != 1 || runs[0].Phase != PhaseCompleted || runs[0].Detail != "후보 1개 저장" {
		t.Fatalf("completed run was not visible: %#v %v", runs, err)
	}
	if other, err := Runs("other", 20); err != nil || len(other) != 0 {
		t.Fatalf("run escaped its project: %#v %v", other, err)
	}
}

func TestDiscardFailedRemovesJobArtifacts(t *testing.T) {
	root := t.TempDir()
	t.Setenv("PURPORY_RECONCILE_DIR", filepath.Join(root, "queue"))
	transcript := filepath.Join(root, "session.jsonl")
	if err := os.WriteFile(transcript, []byte("original"), 0o600); err != nil {
		t.Fatal(err)
	}
	jobPath, err := Enqueue("hermes", "hermes:one", "demo", root, filepath.Join(root, "purpory.db"), transcript, "exit")
	if err != nil {
		t.Fatal(err)
	}
	job, err := LoadJob(jobPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := Process(jobPath, func(Job) error { return errors.New("model unavailable") }); err == nil {
		t.Fatal("failed job was reported as complete")
	}
	if err := Discard(job.ID); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{jobPath, job.TranscriptPath, filepath.Join(filepath.Dir(jobPath), job.ID+".error.json")} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("discard retained %s: %v", path, err)
		}
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

func TestPruneCompletedKeepsNewestPerProject(t *testing.T) {
	root := filepath.Join(t.TempDir(), "queue")
	t.Setenv("PURPORY_RECONCILE_DIR", root)
	completed, err := queueDirectory("completed")
	if err != nil {
		t.Fatal(err)
	}
	for _, projectID := range []string{"first", "second"} {
		for index := 1; index <= 3; index++ {
			id := projectID + string(rune('0'+index))
			job := Job{ID: id, ProjectID: projectID, QueuedAt: int64(index), UpdatedAt: int64(index)}
			if err := atomicJSON(filepath.Join(completed, id+".json"), completedJob{Job: job, CompletedAt: int64(index)}); err != nil {
				t.Fatal(err)
			}
		}
	}
	removed, err := PruneCompleted(2)
	if err != nil || removed != 2 {
		t.Fatalf("pruned completed jobs = %d, %v", removed, err)
	}
	for _, projectID := range []string{"first", "second"} {
		runs, err := Runs(projectID, 10)
		if err != nil || len(runs) != 2 || runs[0].ID != projectID+"3" || runs[1].ID != projectID+"2" {
			t.Fatalf("retained %s jobs = %#v, %v", projectID, runs, err)
		}
	}
}
