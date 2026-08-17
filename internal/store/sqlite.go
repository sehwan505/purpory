// Package store persists Purpory data in the user-global SQLite database.
package store

import (
	"context"
	"database/sql"
	"embed"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/memory"
	_ "modernc.org/sqlite"
)

//go:embed migrations/*.sql
var migrations embed.FS

type Store struct {
	db *sql.DB
}

type SaveResult struct {
	Action    string `json:"action"`
	VersionID int64  `json:"versionId"`
}

var ErrMemoryConflict = errors.New("reconcile memory: concurrent change")

type MemoryProposal struct {
	Memory       memory.Memory
	ExpectedHash *string
	EvidenceIDs  []string
	Links        []graph.Link
}

func DefaultPath() (string, error) {
	if configured := strings.TrimSpace(os.Getenv("PURPORY_DATABASE")); configured != "" {
		return configured, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("database path: %w", err)
	}
	return filepath.Join(home, ".purpory", "purpory.db"), nil
}

func Open(ctx context.Context, path string) (*Store, error) {
	if strings.TrimSpace(path) == "" {
		return nil, errors.New("open store: path is empty")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, fmt.Errorf("open store: create directory: %w", err)
	}

	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open store: %w", err)
	}
	db.SetMaxOpenConns(1)
	store := &Store{db: db}
	if err := store.configure(ctx); err != nil {
		db.Close()
		return nil, err
	}
	if err := store.migrate(ctx); err != nil {
		db.Close()
		return nil, err
	}
	return store, nil
}

func (s *Store) Close() error {
	return s.db.Close()
}

func escapeLike(value string) string {
	value = strings.ReplaceAll(value, "\\", "\\\\")
	value = strings.ReplaceAll(value, "%", "\\%")
	return strings.ReplaceAll(value, "_", "\\_")
}

func (s *Store) configure(ctx context.Context) error {
	for _, statement := range []string{
		"PRAGMA foreign_keys = ON",
		"PRAGMA journal_mode = WAL",
		"PRAGMA synchronous = NORMAL",
		"PRAGMA busy_timeout = 5000",
	} {
		if _, err := s.db.ExecContext(ctx, statement); err != nil {
			return fmt.Errorf("open store: configure sqlite: %w", err)
		}
	}
	return nil
}

func (s *Store) migrate(ctx context.Context) error {
	if _, err := s.db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS schema_migrations (
			version INTEGER PRIMARY KEY,
			applied_at INTEGER NOT NULL DEFAULT (unixepoch())
		) STRICT
	`); err != nil {
		return fmt.Errorf("migrate store: initialize: %w", err)
	}

	entries, err := migrations.ReadDir("migrations")
	if err != nil {
		return fmt.Errorf("migrate store: read migrations: %w", err)
	}
	for _, entry := range entries {
		versionText, _, ok := strings.Cut(entry.Name(), "_")
		if !ok {
			return fmt.Errorf("migrate store: invalid filename %q", entry.Name())
		}
		version, err := strconv.Atoi(versionText)
		if err != nil {
			return fmt.Errorf("migrate store: invalid filename %q", entry.Name())
		}
		var applied bool
		if err := s.db.QueryRowContext(ctx,
			"SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version = ?)", version,
		).Scan(&applied); err != nil {
			return fmt.Errorf("migrate store: check version %d: %w", version, err)
		}
		if applied {
			continue
		}
		script, err := migrations.ReadFile("migrations/" + entry.Name())
		if err != nil {
			return fmt.Errorf("migrate store: read version %d: %w", version, err)
		}
		if err := s.applyMigration(ctx, version, string(script)); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) applyMigration(ctx context.Context, version int, script string) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("migrate store: begin version %d: %w", version, err)
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, script); err != nil {
		return fmt.Errorf("migrate store: apply version %d: %w", version, err)
	}
	if _, err := tx.ExecContext(ctx,
		"INSERT INTO schema_migrations(version) VALUES (?)", version,
	); err != nil {
		return fmt.Errorf("migrate store: record version %d: %w", version, err)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("migrate store: commit version %d: %w", version, err)
	}
	return nil
}
