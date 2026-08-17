package reconcile

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type Job struct {
	SchemaVersion  int    `json:"schemaVersion"`
	ID             string `json:"id"`
	Agent          string `json:"agent"`
	SessionID      string `json:"sessionId"`
	ProjectID      string `json:"projectId"`
	CWD            string `json:"cwd"`
	DBPath         string `json:"database"`
	TranscriptPath string `json:"transcript"`
	Reason         string `json:"reason,omitempty"`
	QueuedAt       int64  `json:"queuedAt"`
}

var ErrJobLocked = errors.New("process reconciliation: job is already running")

func Enqueue(agent, sessionID, projectID, cwd, dbPath, transcriptPath, reason string) (string, error) {
	agent = strings.ToLower(strings.TrimSpace(agent))
	if agent != "codex" && agent != "claude" {
		return "", errors.New("queue reconciliation: agent must be codex or claude")
	}
	if strings.TrimSpace(sessionID) == "" || strings.TrimSpace(projectID) == "" || strings.TrimSpace(dbPath) == "" {
		return "", errors.New("queue reconciliation: session, project, and database are required")
	}
	root, err := filepath.Abs(cwd)
	if err != nil {
		return "", fmt.Errorf("queue reconciliation: resolve working directory: %w", err)
	}
	if info, err := os.Stat(root); err != nil || !info.IsDir() {
		return "", errors.New("queue reconciliation: working directory is not a directory")
	}
	source, err := filepath.Abs(transcriptPath)
	if err != nil {
		return "", fmt.Errorf("queue reconciliation: resolve transcript: %w", err)
	}
	info, err := os.Stat(source)
	if err != nil || !info.Mode().IsRegular() {
		return "", errors.New("queue reconciliation: transcript is not a file")
	}
	identity := strings.Join([]string{agent, sessionID, projectID, source, fmt.Sprint(info.Size()), fmt.Sprint(info.ModTime().UnixNano())}, "\x00")
	digest := sha256.Sum256([]byte(identity))
	id := hex.EncodeToString(digest[:])
	pending, err := queueDirectory("pending")
	if err != nil {
		return "", err
	}
	jobPath := filepath.Join(pending, id+".json")
	snapshot := filepath.Join(pending, id+".jsonl")
	if _, err := os.Stat(snapshot); errors.Is(err, os.ErrNotExist) {
		if err := copyFile(source, snapshot); err != nil {
			return "", err
		}
	} else if err != nil {
		return "", fmt.Errorf("queue reconciliation: inspect snapshot: %w", err)
	}
	if _, err := os.Stat(jobPath); errors.Is(err, os.ErrNotExist) {
		job := Job{SchemaVersion: 1, ID: id, Agent: agent, SessionID: sessionID, ProjectID: projectID, CWD: root, DBPath: dbPath, TranscriptPath: snapshot, Reason: reason, QueuedAt: time.Now().Unix()}
		if err := atomicJSON(jobPath, job); err != nil {
			return "", err
		}
	} else if err != nil {
		return "", fmt.Errorf("queue reconciliation: inspect job: %w", err)
	}
	return jobPath, nil
}

func StartWorker(executable string) error {
	command := exec.Command(executable, "reconcile-drain")
	detach(command)
	if err := command.Start(); err != nil {
		return fmt.Errorf("start reconciliation worker: %w", err)
	}
	if err := command.Process.Release(); err != nil {
		return fmt.Errorf("start reconciliation worker: release: %w", err)
	}
	return nil
}

func LoadJob(path string) (Job, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return Job{}, fmt.Errorf("load reconciliation job: %w", err)
	}
	var job Job
	if err := json.Unmarshal(content, &job); err != nil {
		return Job{}, fmt.Errorf("load reconciliation job: %w", err)
	}
	if job.SchemaVersion != 1 || job.ID == "" || job.SessionID == "" || job.ProjectID == "" || job.CWD == "" || job.DBPath == "" || job.TranscriptPath == "" {
		return Job{}, errors.New("load reconciliation job: invalid job")
	}
	return job, nil
}

func Process(jobPath string, reconcile func(Job) error) error {
	lock := jobPath + ".lock"
	owner, err := os.OpenFile(lock, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if errors.Is(err, os.ErrExist) {
		info, statErr := os.Stat(lock)
		if statErr == nil && time.Since(info.ModTime()) > time.Hour {
			_ = os.Remove(lock)
			return Process(jobPath, reconcile)
		}
		return ErrJobLocked
	}
	if err != nil {
		return fmt.Errorf("process reconciliation: lock: %w", err)
	}
	_, _ = fmt.Fprintf(owner, "%d\n", os.Getpid())
	_ = owner.Close()
	defer os.Remove(lock)
	job, err := LoadJob(jobPath)
	if err != nil {
		return err
	}
	completed, err := queueDirectory("completed")
	if err != nil {
		return err
	}
	marker := filepath.Join(completed, job.ID+".json")
	if _, err := os.Stat(marker); err == nil {
		return cleanup(jobPath, job.TranscriptPath)
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("process reconciliation: inspect marker: %w", err)
	}
	if err := reconcile(job); err != nil {
		failure := strings.TrimSuffix(jobPath, ".json") + ".error.json"
		_ = atomicJSON(failure, map[string]any{"error": err.Error(), "failedAt": time.Now().Unix()})
		return err
	}
	if err := atomicJSON(marker, map[string]any{"job": job, "completedAt": time.Now().Unix()}); err != nil {
		return err
	}
	failure := strings.TrimSuffix(jobPath, ".json") + ".error.json"
	return cleanup(jobPath, job.TranscriptPath, failure)
}

func Pending() ([]string, error) {
	pending, err := queueDirectory("pending")
	if err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(pending, "*.json"))
	if err != nil {
		return nil, fmt.Errorf("list reconciliation jobs: %w", err)
	}
	result := paths[:0]
	for _, path := range paths {
		if !strings.HasSuffix(path, ".error.json") && !strings.HasSuffix(path, ".invalid.json") {
			result = append(result, path)
		}
	}
	return result, nil
}

// Reject preserves an unreadable job while keeping it out of the retry queue.
func Reject(jobPath string, cause error) error {
	if cause == nil || !strings.HasSuffix(jobPath, ".json") {
		return errors.New("reject reconciliation job: path and cause are required")
	}
	failure := strings.TrimSuffix(jobPath, ".json") + ".error.json"
	if err := atomicJSON(failure, map[string]any{"error": cause.Error(), "failedAt": time.Now().Unix()}); err != nil {
		return err
	}
	if err := os.Rename(jobPath, strings.TrimSuffix(jobPath, ".json")+".invalid.json"); err != nil {
		return fmt.Errorf("reject reconciliation job: %w", err)
	}
	return nil
}

func queueDirectory(name string) (string, error) {
	root := strings.TrimSpace(os.Getenv("PURPORY_RECONCILE_DIR"))
	if root == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("reconciliation queue: %w", err)
		}
		root = filepath.Join(home, ".purpory", "reconcile")
	}
	path := filepath.Join(root, name)
	if err := os.MkdirAll(path, 0o700); err != nil {
		return "", fmt.Errorf("reconciliation queue: create directory: %w", err)
	}
	return path, nil
}

func copyFile(source, target string) error {
	input, err := os.Open(source)
	if err != nil {
		return fmt.Errorf("queue reconciliation: open transcript: %w", err)
	}
	defer input.Close()
	temporary, err := os.CreateTemp(filepath.Dir(target), ".snapshot-*")
	if err != nil {
		return fmt.Errorf("queue reconciliation: create snapshot: %w", err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return fmt.Errorf("queue reconciliation: protect snapshot: %w", err)
	}
	if _, err := io.Copy(temporary, input); err != nil {
		temporary.Close()
		return fmt.Errorf("queue reconciliation: copy transcript: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("queue reconciliation: close snapshot: %w", err)
	}
	if err := os.Rename(temporaryPath, target); err != nil {
		return fmt.Errorf("queue reconciliation: publish snapshot: %w", err)
	}
	return nil
}

func atomicJSON(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("write reconciliation state: %w", err)
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".reconcile-*")
	if err != nil {
		return fmt.Errorf("write reconciliation state: %w", err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return fmt.Errorf("write reconciliation state: %w", err)
	}
	encoder := json.NewEncoder(temporary)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(value); err != nil {
		temporary.Close()
		return fmt.Errorf("write reconciliation state: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("write reconciliation state: %w", err)
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return fmt.Errorf("write reconciliation state: %w", err)
	}
	return nil
}

func cleanup(paths ...string) error {
	for _, path := range paths {
		if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("complete reconciliation: %w", err)
		}
	}
	return nil
}
