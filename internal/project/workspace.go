package project

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type Resource struct {
	ID       string `json:"id"`
	Provider string `json:"provider"`
	Label    string `json:"label"`
	Identity string `json:"identity"`
	Views    []View `json:"views"`
}

type View struct {
	ID         string    `json:"id"`
	Root       string    `json:"root"`
	Branch     string    `json:"branch"`
	Revision   string    `json:"revision"`
	Dirty      bool      `json:"dirty"`
	Available  bool      `json:"available"`
	ObservedAt string    `json:"observedAt"`
	Sessions   []Session `json:"sessions"`
}

type Session struct {
	ID         string     `json:"id"`
	ViewID     string     `json:"viewId,omitempty"`
	Agent      string     `json:"agent"`
	Status     string     `json:"status"`
	StartedAt  string     `json:"startedAt"`
	UpdatedAt  string     `json:"updatedAt"`
	Deliveries []Delivery `json:"items"`
}

type Delivery struct {
	Key         string `json:"key"`
	Kind        string `json:"kind,omitempty"`
	Label       string `json:"label,omitempty"`
	Source      string `json:"source,omitempty"`
	Preview     string `json:"preview,omitempty"`
	Hash        string `json:"valueHash,omitempty"`
	DeliveredAt string `json:"deliveredAt"`
}

type Workspace struct {
	Project          Project    `json:"project"`
	Resources        []Resource `json:"resources"`
	UnmappedSessions []Session  `json:"unmappedSessions"`
}

// Local observes the current machine. Git is one provider; ordinary folders
// use the same Project → Resource → View shape.
type Local struct{}

func (Local) Observe(ctx context.Context, path string) (Workspace, error) {
	root, repository, err := localRoot(path)
	if err != nil {
		return Workspace{}, err
	}
	if repository {
		return observeGit(ctx, root)
	}
	resource := Resource{
		ID: ResourceID("folder", root), Provider: "folder", Label: filepath.Base(root), Identity: root,
		Views: []View{{ID: ViewID(root), Root: root, Available: true, ObservedAt: time.Now().UTC().Format(time.RFC3339)}},
	}
	return Workspace{Project: Project{ID: root, Name: filepath.Base(root), Root: root}, Resources: []Resource{resource}}, nil
}

func localRoot(path string) (string, bool, error) {
	if path == "" {
		return "", false, fmt.Errorf("observe workspace: path is empty")
	}
	root, err := filepath.Abs(path)
	if err != nil {
		return "", false, fmt.Errorf("observe workspace: resolve path: %w", err)
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", false, fmt.Errorf("observe workspace: resolve links: %w", err)
	}
	info, err := os.Stat(root)
	if err != nil {
		return "", false, fmt.Errorf("observe workspace: inspect path: %w", err)
	}
	if !info.IsDir() {
		return "", false, fmt.Errorf("observe workspace: path is not a directory")
	}
	for candidate := root; ; candidate = filepath.Dir(candidate) {
		if _, err := os.Stat(filepath.Join(candidate, ".git")); err == nil {
			return candidate, true, nil
		} else if !os.IsNotExist(err) {
			return "", false, fmt.Errorf("observe workspace: inspect repository marker: %w", err)
		}
		parent := filepath.Dir(candidate)
		if parent == candidate {
			return root, false, nil
		}
	}
}

func ResourceID(provider, identity string) string { return stableID(provider, identity) }
func ViewID(root string) string                   { return stableID("view", root) }

func stableID(kind, identity string) string {
	sum := sha256.Sum256([]byte(kind + "\x00" + identity))
	return hex.EncodeToString(sum[:])
}
