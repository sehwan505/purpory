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
	"sort"
	"strings"
	"time"
)

const (
	PhaseQueued    = "queued"
	PhaseRunning   = "running"
	PhaseReading   = "reading"
	PhaseUpdating  = "updating"
	PhaseProposing = "proposing"
	PhaseApplying  = "applying"
	PhaseCompleted = "completed"
	PhaseFailed    = "failed"
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
	Phase          string `json:"phase,omitempty"`
	Detail         string `json:"detail,omitempty"`
	UpdatedAt      int64  `json:"updatedAt,omitempty"`
}

type Run struct {
	ID        string `json:"id"`
	SessionID string `json:"sessionId"`
	Agent     string `json:"agent"`
	ProjectID string `json:"projectId"`
	CWD       string `json:"cwd"`
	Reason    string `json:"reason,omitempty"`
	Phase     string `json:"phase"`
	Detail    string `json:"detail,omitempty"`
	QueuedAt  string `json:"queuedAt"`
	UpdatedAt string `json:"updatedAt"`
}

type completedJob struct {
	Job         Job   `json:"job"`
	CompletedAt int64 `json:"completedAt"`
}

var ErrJobLocked = errors.New("process reconciliation: job is already running")

func Enqueue(agent, sessionID, projectID, cwd, dbPath, transcriptPath, reason string) (string, error) {
	agent = strings.ToLower(strings.TrimSpace(agent))
	if agent != "codex" && agent != "claude" && agent != "hermes" {
		return "", errors.New("queue reconciliation: agent must be codex, claude, or hermes")
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
		now := time.Now().Unix()
		job := Job{SchemaVersion: 1, ID: id, Agent: agent, SessionID: sessionID, ProjectID: projectID, CWD: root, DBPath: dbPath, TranscriptPath: snapshot, Reason: reason, QueuedAt: now, Phase: PhaseQueued, UpdatedAt: now}
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
	if job.Phase == PhaseFailed {
		return nil
	}
	if err := SetPhase(jobPath, PhaseRunning, ""); err != nil {
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
		_ = SetPhase(jobPath, PhaseFailed, err.Error())
		failure := strings.TrimSuffix(jobPath, ".json") + ".error.json"
		_ = atomicJSON(failure, map[string]any{"error": err.Error(), "failedAt": time.Now().Unix()})
		return err
	}
	if err := SetPhase(jobPath, PhaseCompleted, ""); err != nil {
		return err
	}
	job, err = LoadJob(jobPath)
	if err != nil {
		return err
	}
	if err := atomicJSON(marker, completedJob{Job: job, CompletedAt: time.Now().Unix()}); err != nil {
		return err
	}
	failure := strings.TrimSuffix(jobPath, ".json") + ".error.json"
	return cleanup(jobPath, job.TranscriptPath, failure)
}

func SetPhase(jobPath, phase, detail string) error {
	if !validPhase(phase) {
		return errors.New("update reconciliation phase: invalid phase")
	}
	job, err := LoadJob(jobPath)
	if err != nil {
		return err
	}
	job.Phase = phase
	if detail != "" {
		job.Detail = detail
	}
	job.UpdatedAt = time.Now().Unix()
	return atomicJSON(jobPath, job)
}

func Runs(projectID string, limit int) ([]Run, error) {
	projectID = strings.TrimSpace(projectID)
	if projectID == "" {
		return nil, errors.New("list reconciliations: project is required")
	}
	return runs(projectID, limit)
}

// Queue returns recent jobs across projects for queue administration.
func Queue(limit int) ([]Run, error) {
	return runs("", limit)
}

func runs(projectID string, limit int) ([]Run, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	pending, err := jobPaths()
	if err != nil {
		return nil, err
	}
	var runs []Run
	for _, path := range pending {
		job, err := LoadJob(path)
		if err != nil || projectID != "" && job.ProjectID != projectID {
			continue
		}
		phase := job.Phase
		if phase == "" {
			phase = PhaseQueued
		}
		runs = append(runs, run(job, phase, job.UpdatedAt))
	}
	completed, err := queueDirectory("completed")
	if err != nil {
		return nil, err
	}
	markers, err := filepath.Glob(filepath.Join(completed, "*.json"))
	if err != nil {
		return nil, fmt.Errorf("list completed reconciliations: %w", err)
	}
	// ponytail: completed markers are scanned directly; index them only if real histories make this slow.
	for _, path := range markers {
		content, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var completed completedJob
		if json.Unmarshal(content, &completed) != nil || projectID != "" && completed.Job.ProjectID != projectID {
			continue
		}
		runs = append(runs, run(completed.Job, PhaseCompleted, completed.CompletedAt))
	}
	sort.Slice(runs, func(i, j int) bool { return runs[i].UpdatedAt > runs[j].UpdatedAt })
	if len(runs) > limit {
		runs = runs[:limit]
	}
	return runs, nil
}

func run(job Job, phase string, updatedAt int64) Run {
	if updatedAt == 0 {
		updatedAt = job.QueuedAt
	}
	return Run{
		ID: job.ID, SessionID: job.SessionID, Agent: job.Agent, ProjectID: job.ProjectID,
		CWD: job.CWD, Reason: job.Reason,
		Phase: phase, Detail: job.Detail,
		QueuedAt:  time.Unix(job.QueuedAt, 0).UTC().Format(time.RFC3339),
		UpdatedAt: time.Unix(updatedAt, 0).UTC().Format(time.RFC3339),
	}
}

func validPhase(phase string) bool {
	return phase == PhaseQueued || phase == PhaseRunning || phase == PhaseReading ||
		phase == PhaseUpdating || phase == PhaseProposing || phase == PhaseApplying ||
		phase == PhaseCompleted || phase == PhaseFailed
}

func Pending() ([]string, error) {
	paths, err := jobPaths()
	if err != nil {
		return nil, err
	}
	result := paths[:0]
	for _, path := range paths {
		job, err := LoadJob(path)
		if err == nil && job.Phase == PhaseFailed {
			continue
		}
		result = append(result, path)
	}
	return result, nil
}

func jobPaths() ([]string, error) {
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

// Retry moves one failed job back to the active queue.
func Retry(id string) error {
	path, err := pathForJob(id)
	if err != nil {
		return err
	}
	job, err := LoadJob(path)
	if err != nil {
		return err
	}
	if job.ID != id || job.Phase != PhaseFailed {
		return errors.New("retry reconciliation job: job is not failed")
	}
	job.Phase, job.Detail, job.UpdatedAt = PhaseQueued, "", time.Now().Unix()
	if err := atomicJSON(path, job); err != nil {
		return err
	}
	return cleanup(strings.TrimSuffix(path, ".json") + ".error.json")
}

// Discard removes one failed job and its private transcript snapshot.
func Discard(id string) error {
	path, err := pathForJob(id)
	if err != nil {
		return err
	}
	job, err := LoadJob(path)
	if err != nil {
		return err
	}
	if job.ID != id || job.Phase != PhaseFailed {
		return errors.New("discard reconciliation job: job is not failed")
	}
	return cleanup(job.TranscriptPath, strings.TrimSuffix(path, ".json")+".error.json", path+".lock", path)
}

func DiscardFailed() (int, error) {
	paths, err := jobPaths()
	if err != nil {
		return 0, err
	}
	discarded := 0
	for _, path := range paths {
		job, err := LoadJob(path)
		if err != nil || job.Phase != PhaseFailed {
			continue
		}
		if err := Discard(job.ID); err != nil {
			return discarded, err
		}
		discarded++
	}
	return discarded, nil
}

// PruneCompleted keeps the newest completed jobs for each project.
func PruneCompleted(keep int) (int, error) {
	if keep <= 0 {
		return 0, errors.New("prune completed reconciliations: keep must be positive")
	}
	completed, err := queueDirectory("completed")
	if err != nil {
		return 0, err
	}
	paths, err := filepath.Glob(filepath.Join(completed, "*.json"))
	if err != nil {
		return 0, fmt.Errorf("prune completed reconciliations: %w", err)
	}
	type marker struct {
		path string
		at   int64
	}
	byProject := map[string][]marker{}
	for _, path := range paths {
		content, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var completed completedJob
		if json.Unmarshal(content, &completed) != nil || completed.Job.ProjectID == "" {
			continue
		}
		byProject[completed.Job.ProjectID] = append(byProject[completed.Job.ProjectID], marker{path: path, at: completed.CompletedAt})
	}
	removed := 0
	for _, markers := range byProject {
		if len(markers) <= keep {
			continue
		}
		sort.Slice(markers, func(i, j int) bool {
			if markers[i].at != markers[j].at {
				return markers[i].at > markers[j].at
			}
			return markers[i].path > markers[j].path
		})
		for _, marker := range markers[keep:] {
			if err := os.Remove(marker.path); err != nil && !errors.Is(err, os.ErrNotExist) {
				return removed, fmt.Errorf("prune completed reconciliations: %w", err)
			}
			removed++
		}
	}
	return removed, nil
}

func pathForJob(id string) (string, error) {
	id = strings.TrimSpace(id)
	decoded, err := hex.DecodeString(id)
	if err != nil || len(decoded) != sha256.Size || id != strings.ToLower(id) {
		return "", errors.New("reconciliation job ID must be 64 lowercase hexadecimal characters")
	}
	pending, err := queueDirectory("pending")
	if err != nil {
		return "", err
	}
	return filepath.Join(pending, id+".json"), nil
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
