package project

import (
	"context"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func observeGit(ctx context.Context, activeRoot string) (Workspace, error) {
	commandContext, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	common, err := git(commandContext, activeRoot, "rev-parse", "--path-format=absolute", "--git-common-dir")
	if err != nil {
		return Workspace{}, fmt.Errorf("observe git workspace: common directory: %w", err)
	}
	common = filepath.Clean(common)
	output, err := gitBytes(commandContext, activeRoot, "worktree", "list", "--porcelain", "-z")
	if err != nil {
		return Workspace{}, fmt.Errorf("observe git workspace: list worktrees: %w", err)
	}
	views, err := parseWorktrees(output)
	if err != nil {
		return Workspace{}, fmt.Errorf("observe git workspace: %w", err)
	}
	if len(views) == 0 {
		return Workspace{}, fmt.Errorf("observe git workspace: no worktrees")
	}
	for index := range views {
		view := &views[index]
		view.ID = ViewID(view.Root)
		view.Available = true
		view.ObservedAt = time.Now().UTC().Format(time.RFC3339)
		dirty, dirtyErr := git(commandContext, view.Root, "status", "--porcelain", "--untracked-files=no")
		view.Dirty = dirtyErr == nil && dirty != ""
	}
	primary := views[0].Root
	resource := Resource{
		ID: ResourceID("git", common), Provider: "git", Label: filepath.Base(primary), Identity: common, Views: views,
	}
	return Workspace{
		Project:   Project{ID: primary, Name: filepath.Base(primary), Root: activeRoot},
		Resources: []Resource{resource},
	}, nil
}

func parseWorktrees(output []byte) ([]View, error) {
	var views []View
	for _, record := range strings.Split(string(output), "\x00\x00") {
		if record == "" {
			continue
		}
		var view View
		for _, field := range strings.Split(record, "\x00") {
			key, value, found := strings.Cut(field, " ")
			if !found {
				continue
			}
			switch key {
			case "worktree":
				root, err := filepath.EvalSymlinks(value)
				if err != nil {
					return nil, fmt.Errorf("resolve worktree %q: %w", value, err)
				}
				view.Root = root
			case "HEAD":
				view.Revision = value
			case "branch":
				view.Branch = strings.TrimPrefix(value, "refs/heads/")
			}
		}
		if view.Root != "" {
			views = append(views, view)
		}
	}
	return views, nil
}

func git(ctx context.Context, root string, arguments ...string) (string, error) {
	output, err := gitBytes(ctx, root, arguments...)
	return strings.TrimSpace(string(output)), err
}

func gitBytes(ctx context.Context, root string, arguments ...string) ([]byte, error) {
	command := exec.CommandContext(ctx, "git", append([]string{"-C", root}, arguments...)...)
	return command.Output()
}
