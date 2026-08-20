// Package integration installs Purpory guidance into supported coding agents.
package integration

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const (
	startMarker = "<!-- purpory:start -->"
	endMarker   = "<!-- purpory:end -->"
	section     = startMarker + "\n## Purpory\n\n" +
		"- Preflight provides graph hints, not source content. Inspect only relevant node IDs.\n" +
		"- Before answering codebase questions, run `purpory query \"<question>\"`.\n" +
		"- Use `purpory explain \"<concept>\"` or `purpory path \"<A>\" \"<B>\"` for focused relationships.\n" +
		"- After modifying code, run `purpory update`.\n" + endMarker
)

func Install(agent string) (string, error) {
	directory, err := configDirectory(agent)
	if err != nil {
		return "", err
	}
	path, err := agentFile(directory, agent)
	if err != nil {
		return "", err
	}
	content, mode, err := read(path)
	if err != nil {
		return "", err
	}
	updated := replace(content, section)
	changed := updated != content
	if changed {
		if err := atomicWrite(path, updated, mode); err != nil {
			return "", err
		}
	}
	hooksChanged, err := configureHooks(directory, agent, true)
	if err != nil {
		return "", err
	}
	if changed || hooksChanged {
		return "installed", nil
	}
	return "unchanged", nil
}

func Uninstall(agent string) (string, error) {
	directory, err := configDirectory(agent)
	if err != nil {
		return "", err
	}
	path, err := agentFile(directory, agent)
	if err != nil {
		return "", err
	}
	content, mode, err := read(path)
	if errors.Is(err, os.ErrNotExist) {
		return "unchanged", nil
	}
	if err != nil {
		return "", err
	}
	updated := strings.TrimSpace(remove(content))
	changed := updated != strings.TrimSpace(content)
	if changed {
		if updated != "" {
			updated += "\n"
		}
		if err := atomicWrite(path, updated, mode); err != nil {
			return "", err
		}
	}
	hooksChanged, err := configureHooks(directory, agent, false)
	if err != nil {
		return "", err
	}
	if changed || hooksChanged {
		return "uninstalled", nil
	}
	return "unchanged", nil
}

func configDirectory(agent string) (string, error) {
	agent = strings.ToLower(strings.TrimSpace(agent))
	if agent == "codex" {
		if directory := strings.TrimSpace(os.Getenv("CODEX_HOME")); directory != "" {
			return directory, nil
		}
	} else if agent != "claude" {
		return "", errors.New("configure integration: agent must be codex or claude")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("configure integration: resolve home directory: %w", err)
	}
	if agent == "codex" {
		return filepath.Join(home, ".codex"), nil
	}
	return filepath.Join(home, ".claude"), nil
}

func agentFile(directory, agent string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(agent)) {
	case "codex":
		return filepath.Join(directory, "AGENTS.md"), nil
	case "claude":
		return filepath.Join(directory, "CLAUDE.md"), nil
	default:
		return "", errors.New("configure integration: agent must be codex or claude")
	}
}

func configureHooks(directory, agent string, install bool) (bool, error) {
	path, err := hooksFile(directory, agent)
	if err != nil {
		return false, err
	}
	content, mode, err := read(path)
	if err != nil {
		return false, err
	}
	if !install && strings.TrimSpace(content) == "" {
		return false, nil
	}
	settings := map[string]any{}
	if strings.TrimSpace(content) != "" {
		if err := json.Unmarshal([]byte(content), &settings); err != nil {
			return false, fmt.Errorf("configure integration: parse %s: %w", path, err)
		}
	}
	hooks, _ := settings["hooks"].(map[string]any)
	if hooks == nil {
		hooks = map[string]any{}
		settings["hooks"] = hooks
	}
	removeHooks(hooks)
	if install {
		executable, err := os.Executable()
		if err != nil {
			return false, fmt.Errorf("configure integration: executable: %w", err)
		}
		command := executable
		if strings.ContainsAny(command, " \t") {
			command = `"` + command + `"`
		}
		addHook(hooks, "UserPromptSubmit", command+" preflight "+agent, "Purpory is preparing context")
		addHook(hooks, "SessionEnd", command+" session-end "+agent, "Purpory is closing the session")
	}
	if len(hooks) == 0 {
		delete(settings, "hooks")
	}
	encoded, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return false, fmt.Errorf("configure integration: encode hooks: %w", err)
	}
	updated := string(encoded) + "\n"
	if updated == content {
		return false, nil
	}
	if err := atomicWrite(path, updated, mode); err != nil {
		return false, err
	}
	return true, nil
}

func hooksFile(directory, agent string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(agent)) {
	case "codex":
		return filepath.Join(directory, "hooks.json"), nil
	case "claude":
		return filepath.Join(directory, "settings.json"), nil
	default:
		return "", errors.New("configure integration: agent must be codex or claude")
	}
}

func addHook(hooks map[string]any, event, command, status string) {
	entry := map[string]any{"hooks": []any{map[string]any{
		"type": "command", "command": command, "commandWindows": command, "timeout": float64(30), "statusMessage": status,
	}}}
	entries, _ := hooks[event].([]any)
	hooks[event] = append(entries, entry)
}

func removeHooks(hooks map[string]any) bool {
	changed := false
	for event, raw := range hooks {
		entries, ok := raw.([]any)
		if !ok {
			continue
		}
		retained := entries[:0]
		for _, entry := range entries {
			encoded, _ := json.Marshal(entry)
			text := strings.ToLower(string(encoded))
			if strings.Contains(text, "preflight codex") || strings.Contains(text, "preflight claude") || strings.Contains(text, "session-end codex") || strings.Contains(text, "session-end claude") {
				changed = true
				continue
			}
			retained = append(retained, entry)
		}
		if len(retained) == 0 {
			delete(hooks, event)
		} else {
			hooks[event] = retained
		}
	}
	return changed
}

func read(path string) (string, os.FileMode, error) {
	content, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return "", 0o600, nil
	}
	if err != nil {
		return "", 0, fmt.Errorf("configure integration: read %s: %w", path, err)
	}
	info, err := os.Stat(path)
	if err != nil {
		return "", 0, fmt.Errorf("configure integration: inspect %s: %w", path, err)
	}
	return string(content), info.Mode().Perm(), nil
}

func replace(content, section string) string {
	without := strings.TrimSpace(remove(content))
	if without == "" {
		return section + "\n"
	}
	return without + "\n\n" + section + "\n"
}

func remove(content string) string {
	from := strings.Index(content, startMarker)
	if from < 0 {
		return content
	}
	to := strings.Index(content[from:], endMarker)
	if to < 0 {
		return content
	}
	return content[:from] + content[from+to+len(endMarker):]
}

func atomicWrite(path, content string, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("configure integration: create directory: %w", err)
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".purpory-*")
	if err != nil {
		return fmt.Errorf("configure integration: create temporary file: %w", err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if _, err := temporary.WriteString(content); err != nil {
		temporary.Close()
		return fmt.Errorf("configure integration: write temporary file: %w", err)
	}
	if err := temporary.Chmod(mode); err != nil {
		temporary.Close()
		return fmt.Errorf("configure integration: set permissions: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("configure integration: close temporary file: %w", err)
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return fmt.Errorf("configure integration: replace %s: %w", path, err)
	}
	return nil
}
