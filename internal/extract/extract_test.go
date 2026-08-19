package extract

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sehwan505/purpory/internal/material"
)

func TestMaterialExtractsDocumentsAndJVMSource(t *testing.T) {
	root := t.TempDir()
	tests := []struct {
		name, content, mediaType, kind, label string
	}{
		{"guide.md", "# Guide\n```\n# Not a section\n```\n## Install\n", "text/markdown", "section", "Install"},
		{"Demo.java", "public class Demo {}\npublic void run() {}\n", "text/x-java", "function", "run()"},
		{"Worker.kt", "data class Worker(val id: String)\nsuspend fun process() {}\n", "text/x-kotlin", "function", "process()"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := os.WriteFile(filepath.Join(root, test.name), []byte(test.content), 0o600); err != nil {
				t.Fatal(err)
			}
			value := localMaterial(test.name, test.mediaType)
			facts, err := Material(context.Background(), root, value)
			if err != nil {
				t.Fatal(err)
			}
			found := false
			for _, node := range facts.Nodes {
				found = found || node.Kind == "knowledge" && node.Subkind == test.kind && node.Label == test.label
				if test.mediaType == "text/markdown" && node.Label == "Guide" && !strings.Contains(node.Content, "# Not a section") {
					t.Fatalf("markdown body content missing: %#v", facts.Nodes)
				}
			}
			if !found {
				t.Fatalf("%s missing from %#v", test.label, facts.Nodes)
			}
		})
	}
}

func TestMaterialExtractsGoCallClaim(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "main.go"), []byte("package demo\nfunc helper() {}\nfunc run() { helper(); helper() }\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	facts, err := Material(context.Background(), root, localMaterial("main.go", "text/x-go"))
	if err != nil {
		t.Fatal(err)
	}
	count := 0
	for _, claim := range facts.Claims {
		if claim.Relation == "calls" && claim.TargetLabel == "helper()" {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("call claims were not normalized: %#v", facts.Claims)
	}
}

func localMaterial(path, mediaType string) material.Material {
	sum := sha256.Sum256([]byte(path))
	return material.Material{ID: hex.EncodeToString(sum[:]), URI: "file:" + path, MediaType: mediaType}
}
