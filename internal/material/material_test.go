package material

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestDiscoverAndDiff(t *testing.T) {
	root := t.TempDir()
	write := func(name, content string) {
		t.Helper()
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write("README.md", "# Demo")
	write("main.java", "class Main {}")
	write("build/output.txt", "ignored")
	write(".env", "SECRET=value")

	first, err := Discover(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != 2 || first[0].URI != "file:README.md" || first[0].MediaType != "text/markdown" {
		t.Fatalf("unexpected materials: %#v", first)
	}
	changes, changed := Diff(nil, first)
	if changes.Added != 2 || len(changed) != 2 {
		t.Fatalf("unexpected first diff: %#v, %#v", changes, changed)
	}

	write("README.md", "# Changed")
	if err := os.Remove(filepath.Join(root, "main.java")); err != nil {
		t.Fatal(err)
	}
	second, err := Discover(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	changes, changed = Diff(first, second)
	if changes.Modified != 1 || changes.Removed != 1 || len(changed) != 1 {
		t.Fatalf("unexpected second diff: %#v, %#v", changes, changed)
	}
	processed := append([]Material(nil), second...)
	processed[0].Processor = "text/v2"
	changes, changed = Diff(second, processed)
	if changes.Modified != 1 || len(changed) != 1 {
		t.Fatalf("processor change was not detected: %#v, %#v", changes, changed)
	}
}

func TestPathRejectsEscapes(t *testing.T) {
	if _, err := Path(t.TempDir(), Material{URI: "file:../secret"}); err == nil {
		t.Fatal("escaping material URI was accepted")
	}
}

func TestDiscoverGitUsesGitIgnoreRules(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	root := t.TempDir()
	if output, err := exec.Command("git", "init", "--quiet", root).CombinedOutput(); err != nil {
		t.Fatalf("git init: %s: %v", output, err)
	}
	write := func(name, content string) {
		t.Helper()
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write(".gitignore", "ignored.txt\ncache/\nnested/*.tmp\n!nested/keep.tmp\n")
	write("README.md", "kept")
	write("ignored.txt", "ignored")
	write("cache/data.txt", "ignored")
	write("nested/drop.tmp", "ignored")
	write("nested/keep.tmp", "kept")

	materials, err := DiscoverGit(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"file:.gitignore", "file:README.md", "file:nested/keep.tmp"}
	if len(materials) != len(want) {
		t.Fatalf("unexpected materials: %#v", materials)
	}
	for index := range want {
		if materials[index].URI != want[index] {
			t.Fatalf("material %d = %q, want %q", index, materials[index].URI, want[index])
		}
	}
}
