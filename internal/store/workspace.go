package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/sehwan505/purpory/internal/project"
)

func (s *Store) SaveWorkspace(ctx context.Context, projectID string, resources []project.Resource) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("save workspace: begin: %w", err)
	}
	defer tx.Rollback()
	if err := saveWorkspace(ctx, tx, projectID, resources); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("save workspace: commit: %w", err)
	}
	return nil
}

func saveWorkspace(ctx context.Context, tx *sql.Tx, projectID string, resources []project.Resource) error {
	for _, resource := range resources {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO resources(project_id, id, provider, label, identity) VALUES (?, ?, ?, ?, ?)
			ON CONFLICT(project_id, id) DO UPDATE SET provider=excluded.provider, label=excluded.label, identity=excluded.identity
		`, projectID, resource.ID, resource.Provider, resource.Label, resource.Identity); err != nil {
			return fmt.Errorf("save workspace: resource: %w", err)
		}
		for _, view := range resource.Views {
			if _, err := tx.ExecContext(ctx, `
				INSERT INTO views(project_id, resource_id, id, root, branch, revision, dirty, available, observed_at)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?, unixepoch())
				ON CONFLICT(project_id, id) DO UPDATE SET resource_id=excluded.resource_id, root=excluded.root,
				branch=excluded.branch, revision=excluded.revision, dirty=excluded.dirty,
				available=excluded.available, observed_at=unixepoch()
			`, projectID, resource.ID, view.ID, view.Root, view.Branch, view.Revision, view.Dirty, view.Available); err != nil {
				return fmt.Errorf("save workspace: view: %w", err)
			}
		}
	}
	return nil
}

func (s *Store) SaveSession(ctx context.Context, projectID, viewID, sessionID, agent, status string) error {
	if strings.TrimSpace(sessionID) == "" || strings.TrimSpace(agent) == "" {
		return errors.New("save session: ID and agent are required")
	}
	if status != "active" && status != "ended" {
		return errors.New("save session: status must be active or ended")
	}
	var linkedView any
	if strings.TrimSpace(viewID) != "" {
		linkedView = viewID
	}
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO sessions(project_id, id, view_id, agent, status) VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(project_id, id) DO UPDATE SET view_id=excluded.view_id, agent=excluded.agent,
		status=excluded.status, updated_at=unixepoch()
	`, projectID, sessionID, linkedView, agent, status)
	if err != nil {
		return fmt.Errorf("save session: %w", err)
	}
	return nil
}

func (s *Store) Workspace(ctx context.Context, current project.Project) (project.Workspace, error) {
	result := project.Workspace{Project: current}
	rows, err := s.db.QueryContext(ctx, "SELECT id, provider, label, identity FROM resources WHERE project_id = ? ORDER BY label", current.ID)
	if err != nil {
		return result, fmt.Errorf("load workspace: resources: %w", err)
	}
	for rows.Next() {
		var resource project.Resource
		if err := rows.Scan(&resource.ID, &resource.Provider, &resource.Label, &resource.Identity); err != nil {
			rows.Close()
			return result, fmt.Errorf("load workspace: scan resource: %w", err)
		}
		result.Resources = append(result.Resources, resource)
	}
	if err := rows.Close(); err != nil {
		return result, err
	}
	for resourceIndex := range result.Resources {
		resource := &result.Resources[resourceIndex]
		viewRows, err := s.db.QueryContext(ctx, `SELECT id, root, branch, revision, dirty, available, observed_at FROM views WHERE project_id = ? AND resource_id = ? ORDER BY observed_at DESC`, current.ID, resource.ID)
		if err != nil {
			return result, fmt.Errorf("load workspace: views: %w", err)
		}
		for viewRows.Next() {
			var view project.View
			var observed int64
			if err := viewRows.Scan(&view.ID, &view.Root, &view.Branch, &view.Revision, &view.Dirty, &view.Available, &observed); err != nil {
				viewRows.Close()
				return result, fmt.Errorf("load workspace: scan view: %w", err)
			}
			view.ObservedAt = time.Unix(observed, 0).UTC().Format(time.RFC3339)
			resource.Views = append(resource.Views, view)
		}
		if err := viewRows.Close(); err != nil {
			return result, fmt.Errorf("load workspace: close views: %w", err)
		}
	}
	views := map[string]*project.View{}
	for resourceIndex := range result.Resources {
		for viewIndex := range result.Resources[resourceIndex].Views {
			view := &result.Resources[resourceIndex].Views[viewIndex]
			views[view.ID] = view
		}
	}
	sessionRows, err := s.db.QueryContext(ctx, `SELECT id, view_id, agent, status, started_at, updated_at FROM sessions WHERE project_id = ? ORDER BY updated_at DESC`, current.ID)
	if err != nil {
		return result, fmt.Errorf("load workspace: sessions: %w", err)
	}
	for sessionRows.Next() {
		var session project.Session
		var viewID sql.NullString
		var started, updated int64
		if err := sessionRows.Scan(&session.ID, &viewID, &session.Agent, &session.Status, &started, &updated); err != nil {
			sessionRows.Close()
			return result, fmt.Errorf("load workspace: scan session: %w", err)
		}
		session.StartedAt = time.Unix(started, 0).UTC().Format(time.RFC3339)
		session.UpdatedAt = time.Unix(updated, 0).UTC().Format(time.RFC3339)
		if viewID.Valid {
			session.ViewID = viewID.String
		}
		if view := views[session.ViewID]; view != nil {
			view.Sessions = append(view.Sessions, session)
		} else {
			result.UnmappedSessions = append(result.UnmappedSessions, session)
		}
	}
	if err := sessionRows.Close(); err != nil {
		return result, fmt.Errorf("load workspace: close sessions: %w", err)
	}
	deliveries := map[string][]project.Delivery{}
	deliveryRows, err := s.db.QueryContext(ctx, `
		SELECT session_id, key, kind, label, source, preview, value_hash, delivered_at
		FROM session_items WHERE project_id = ? ORDER BY delivered_at DESC, key
	`, current.ID)
	if err != nil {
		return result, fmt.Errorf("load workspace: session items: %w", err)
	}
	for deliveryRows.Next() {
		var sessionID string
		var delivery project.Delivery
		var delivered int64
		if err := deliveryRows.Scan(&sessionID, &delivery.Key, &delivery.Kind, &delivery.Label, &delivery.Source, &delivery.Preview, &delivery.Hash, &delivered); err != nil {
			deliveryRows.Close()
			return result, fmt.Errorf("load workspace: scan session item: %w", err)
		}
		delivery.DeliveredAt = time.Unix(delivered, 0).UTC().Format(time.RFC3339)
		deliveries[sessionID] = append(deliveries[sessionID], delivery)
	}
	if err := deliveryRows.Close(); err != nil {
		return result, fmt.Errorf("load workspace: close session items: %w", err)
	}
	for resourceIndex := range result.Resources {
		for viewIndex := range result.Resources[resourceIndex].Views {
			for sessionIndex := range result.Resources[resourceIndex].Views[viewIndex].Sessions {
				session := &result.Resources[resourceIndex].Views[viewIndex].Sessions[sessionIndex]
				session.Deliveries = deliveries[session.ID]
			}
		}
	}
	for index := range result.UnmappedSessions {
		result.UnmappedSessions[index].Deliveries = deliveries[result.UnmappedSessions[index].ID]
	}
	return result, nil
}

func (s *Store) SaveDeliveries(ctx context.Context, projectID, sessionID string, deliveries []project.Delivery) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("save deliveries: begin: %w", err)
	}
	defer tx.Rollback()
	for _, delivery := range deliveries {
		if strings.TrimSpace(delivery.Key) == "" {
			return errors.New("save deliveries: key is required")
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO session_items(project_id, session_id, key, kind, label, source, preview, value_hash)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?)
			ON CONFLICT(project_id, session_id, key, value_hash) DO UPDATE SET
			kind=excluded.kind, label=excluded.label, source=excluded.source, preview=excluded.preview, delivered_at=unixepoch()
		`, projectID, sessionID, delivery.Key, delivery.Kind, delivery.Label, delivery.Source, delivery.Preview, delivery.Hash); err != nil {
			return fmt.Errorf("save deliveries: item: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("save deliveries: commit: %w", err)
	}
	return nil
}
