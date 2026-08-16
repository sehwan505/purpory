package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/sehwan505/purpory/internal/project"
)

func (s *Store) SaveProject(ctx context.Context, value project.Project) error {
	if strings.TrimSpace(value.ID) == "" || strings.TrimSpace(value.Root) == "" || strings.TrimSpace(value.Name) == "" {
		return errors.New("save project: id, name, and root are required")
	}
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO projects (id, name, root, registered) VALUES (?, ?, ?, 1)
		ON CONFLICT(id) DO UPDATE SET
			name = excluded.name,
			root = excluded.root,
			registered = 1,
			updated_at = unixepoch()
	`, value.ID, value.Name, value.Root)
	if err != nil {
		return fmt.Errorf("save project: %w", err)
	}
	return nil
}

func (s *Store) Project(ctx context.Context, id string) (project.Project, error) {
	var value project.Project
	err := s.db.QueryRowContext(ctx,
		"SELECT id, name, root FROM projects WHERE id = ? AND registered = 1", id,
	).Scan(&value.ID, &value.Name, &value.Root)
	if err != nil {
		return project.Project{}, fmt.Errorf("load project: %w", err)
	}
	return value, nil
}

func (s *Store) Projects(ctx context.Context) ([]project.Project, error) {
	rows, err := s.db.QueryContext(ctx, "SELECT id, name, root FROM projects WHERE registered = 1 ORDER BY updated_at DESC, created_at DESC")
	if err != nil {
		return nil, fmt.Errorf("list projects: %w", err)
	}
	defer rows.Close()
	var values []project.Project
	for rows.Next() {
		var value project.Project
		if err := rows.Scan(&value.ID, &value.Name, &value.Root); err != nil {
			return nil, fmt.Errorf("list projects: scan: %w", err)
		}
		values = append(values, value)
	}
	return values, rows.Err()
}

func (s *Store) RemoveProject(ctx context.Context, id string) (bool, error) {
	result, err := s.db.ExecContext(ctx, "UPDATE projects SET registered = 0, updated_at = unixepoch() WHERE id = ? AND registered = 1", strings.TrimSpace(id))
	if err != nil {
		return false, fmt.Errorf("remove project: %w", err)
	}
	changed, err := result.RowsAffected()
	return changed > 0, err
}

func (s *Store) ProjectForWorkspace(ctx context.Context, current project.Workspace, explicitID string) (project.Project, error) {
	if id := strings.TrimSpace(explicitID); id != "" {
		value, err := s.Project(ctx, id)
		if errors.Is(err, sql.ErrNoRows) {
			return project.Project{}, fmt.Errorf("%w: %s", project.ErrNotRegistered, id)
		}
		return value, err
	}
	registered, err := s.Projects(ctx)
	if err != nil {
		return project.Project{}, err
	}
	resourceMatches := map[string]project.Project{}
	for _, resource := range current.Resources {
		rows, err := s.db.QueryContext(ctx, `
			SELECT p.id, p.name, p.root
			FROM projects p
			JOIN resources r ON r.project_id = p.id
			WHERE p.registered = 1 AND r.provider = ? AND r.identity = ?
		`, resource.Provider, resource.Identity)
		if err != nil {
			return project.Project{}, fmt.Errorf("resolve project: resources: %w", err)
		}
		for rows.Next() {
			var candidate project.Project
			if err := rows.Scan(&candidate.ID, &candidate.Name, &candidate.Root); err != nil {
				rows.Close()
				return project.Project{}, fmt.Errorf("resolve project: scan: %w", err)
			}
			resourceMatches[candidate.ID] = candidate
		}
		if err := rows.Close(); err != nil {
			return project.Project{}, fmt.Errorf("resolve project: close: %w", err)
		}
	}
	if len(resourceMatches) > 0 {
		return oneProject(resourceMatches)
	}
	pathMatches := map[string]project.Project{}
	longest := 0
	for _, candidate := range registered {
		if !pathWithin(current.Project.Root, candidate.Root) {
			continue
		}
		length := len(filepath.Clean(candidate.Root))
		if length > longest {
			pathMatches = map[string]project.Project{}
			longest = length
		}
		if length == longest {
			pathMatches[candidate.ID] = candidate
		}
	}
	if len(pathMatches) == 0 {
		return project.Project{}, fmt.Errorf("%w: run `purpory project add .`", project.ErrNotRegistered)
	}
	return oneProject(pathMatches)
}

func oneProject(matches map[string]project.Project) (project.Project, error) {
	if len(matches) > 1 {
		return project.Project{}, fmt.Errorf("%w: use --project ID", project.ErrAmbiguous)
	}
	for _, match := range matches {
		return match, nil
	}
	return project.Project{}, project.ErrNotRegistered
}

func pathWithin(path, root string) bool {
	path, pathErr := filepath.Abs(path)
	root, rootErr := filepath.Abs(root)
	if pathErr != nil || rootErr != nil {
		return false
	}
	relative, err := filepath.Rel(root, path)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}
