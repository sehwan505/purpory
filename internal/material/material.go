// Package material discovers the inputs that make up a project.
package material

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"io/fs"
	"mime"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

var ignoredDirectories = map[string]bool{
	".git": true, ".idea": true, ".vscode": true, "build": true, "dist": true,
	"node_modules": true, "purpory-out": true, "vendor": true, "wailsjs": true,
}

var mediaTypes = map[string]string{
	".go": "text/x-go", ".java": "text/x-java", ".kt": "text/x-kotlin", ".kts": "text/x-kotlin",
	".py": "text/x-python", ".rs": "text/x-rust", ".ts": "text/typescript", ".tsx": "text/tsx",
	".js": "text/javascript", ".jsx": "text/jsx", ".md": "text/markdown", ".markdown": "text/markdown",
	".txt": "text/plain", ".yaml": "application/yaml", ".yml": "application/yaml",
}

type Material struct {
	ID         string `json:"id"`
	URI        string `json:"uri"`
	MediaType  string `json:"mediaType"`
	Processor  string `json:"processor"`
	Hash       string `json:"hash"`
	Size       int64  `json:"size"`
	ModifiedAt int64  `json:"modifiedAt"`
}

type Changes struct {
	Added     int `json:"added"`
	Modified  int `json:"modified"`
	Removed   int `json:"removed"`
	Unchanged int `json:"unchanged"`
}

// Discover catalogs local files without assuming that a project contains code or Git.
func Discover(ctx context.Context, root string) ([]Material, error) {
	var materials []Material
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		if entry.IsDir() {
			if path != root && IgnorePath(root, path) {
				return filepath.SkipDir
			}
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 || IgnorePath(root, path) {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return fmt.Errorf("discover material %s: %w", path, err)
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return fmt.Errorf("discover material path: %w", err)
		}
		relative = filepath.ToSlash(relative)
		hash, err := fileHash(ctx, path)
		if err != nil {
			return fmt.Errorf("discover material %s: %w", relative, err)
		}
		uri := "file:" + relative
		materials = append(materials, Material{
			ID: stableID(uri), URI: uri, MediaType: mediaType(relative), Hash: hash,
			Size: info.Size(), ModifiedAt: info.ModTime().Unix(),
		})
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("discover materials: %w", err)
	}
	sort.Slice(materials, func(i, j int) bool { return materials[i].URI < materials[j].URI })
	return materials, nil
}

// Diff reports manifest changes and the materials that need extraction.
func Diff(previous, current []Material) (Changes, []Material) {
	before := make(map[string]Material, len(previous))
	for _, value := range previous {
		before[value.ID] = value
	}
	after := make(map[string]bool, len(current))
	var changes Changes
	var changed []Material
	for _, value := range current {
		after[value.ID] = true
		old, found := before[value.ID]
		switch {
		case !found:
			changes.Added++
			changed = append(changed, value)
		case old.Hash != value.Hash || old.MediaType != value.MediaType || old.Processor != value.Processor:
			changes.Modified++
			changed = append(changed, value)
		default:
			changes.Unchanged++
		}
	}
	for _, value := range previous {
		if !after[value.ID] {
			changes.Removed++
		}
	}
	return changes, changed
}

// Scope prevents equal relative paths in different Resources from colliding.
func Scope(resourceID string, values []Material) {
	for index := range values {
		values[index].URI = "resource:" + resourceID + "/" + values[index].URI
		values[index].ID = stableID(values[index].URI)
	}
}

func RelativePath(value Material) (string, error) {
	uri := value.URI
	if strings.HasPrefix(uri, "resource:") {
		_, scoped, found := strings.Cut(uri, "/")
		if !found {
			return "", fmt.Errorf("material path: unsupported URI %q", value.URI)
		}
		uri = scoped
	}
	relative, found := strings.CutPrefix(uri, "file:")
	if !found || relative == "" || filepath.IsAbs(relative) {
		return "", fmt.Errorf("material path: unsupported URI %q", value.URI)
	}
	return relative, nil
}

func Path(root string, value Material) (string, error) {
	relative, err := RelativePath(value)
	if err != nil {
		return "", err
	}
	path := filepath.Join(root, filepath.FromSlash(relative))
	cleanRoot, err := filepath.Abs(root)
	if err != nil {
		return "", fmt.Errorf("material path: %w", err)
	}
	cleanPath, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("material path: %w", err)
	}
	relativeToRoot, err := filepath.Rel(cleanRoot, cleanPath)
	if err != nil || relativeToRoot == ".." || strings.HasPrefix(relativeToRoot, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("material path: URI escapes project root %q", value.URI)
	}
	return cleanPath, nil
}

func IgnorePath(root, path string) bool {
	relative, err := filepath.Rel(root, path)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return true
	}
	parts := strings.Split(filepath.ToSlash(relative), "/")
	for index, part := range parts {
		if ignoredDirectories[part] {
			return true
		}
		if index == len(parts)-1 && sensitiveName(part) {
			return true
		}
	}
	return false
}

func sensitiveName(name string) bool {
	lower := strings.ToLower(name)
	return lower == ".env" || strings.HasPrefix(lower, ".env.") || lower == "id_rsa" || lower == "id_ed25519" ||
		strings.HasSuffix(lower, ".pem") || strings.HasSuffix(lower, ".key")
}

func mediaType(path string) string {
	extension := strings.ToLower(filepath.Ext(path))
	if value := mediaTypes[extension]; value != "" {
		return value
	}
	if value := mime.TypeByExtension(extension); value != "" {
		return strings.SplitN(value, ";", 2)[0]
	}
	return "application/octet-stream"
}

func fileHash(ctx context.Context, path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	buffer := make([]byte, 128<<10)
	for {
		if err := ctx.Err(); err != nil {
			return "", err
		}
		count, readErr := file.Read(buffer)
		if count > 0 {
			if _, err := hash.Write(buffer[:count]); err != nil {
				return "", err
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return "", readErr
		}
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func stableID(value string) string {
	sum := sha256.Sum256([]byte("material\x00" + value))
	return hex.EncodeToString(sum[:])
}
