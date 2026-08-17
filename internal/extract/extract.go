// Package extract turns one Material into domain-neutral graph facts.
package extract

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/sehwan505/purpory/internal/graph"
	"github.com/sehwan505/purpory/internal/material"
)

var declarations = map[string][]pattern{
	"text/x-python": {
		{regexp.MustCompile(`^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(`), "function", "()"},
		{regexp.MustCompile(`^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b`), "type", ""},
	},
	"text/javascript": javascriptPatterns(), "text/jsx": javascriptPatterns(),
	"text/typescript": javascriptPatterns(), "text/tsx": javascriptPatterns(),
	"text/x-java": {
		{regexp.MustCompile(`^\s*(?:(?:public|protected|private|abstract|static|final|sealed|non-sealed|strictfp)\s+)*(?:class|interface|enum|record|@interface)\s+([A-Za-z_$][A-Za-z0-9_$]*)\b`), "type", ""},
		{regexp.MustCompile(`^\s*(?:(?:public|protected|private|abstract|static|final|synchronized|native|default)\s+)*(?:[A-Za-z_$][A-Za-z0-9_$.<>?\[\], ]*\s+)+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(`), "function", "()"},
	},
	"text/x-kotlin": {
		{regexp.MustCompile(`^\s*(?:(?:public|private|protected|internal|open|abstract|sealed|data|value|enum|annotation)\s+)*(?:class|interface|object)\s+([A-Za-z_][A-Za-z0-9_]*)\b`), "type", ""},
		{regexp.MustCompile(`^\s*(?:(?:public|private|protected|internal|open|abstract|override|suspend|inline|operator|infix|tailrec|external)\s+)*fun\s+(?:[A-Za-z_][A-Za-z0-9_<>?.]*\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\(`), "function", "()"},
	},
	"text/x-rust": {
		{regexp.MustCompile(`^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(`), "function", "()"},
		{regexp.MustCompile(`^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)\b`), "type", ""},
	},
}

type Facts struct {
	Nodes  []graph.Node
	Claims []graph.Claim
}

type pattern struct {
	expression *regexp.Regexp
	kind       string
	suffix     string
}

func Processor(value material.Material) string {
	switch value.MediaType {
	case "text/markdown":
		return "markdown/v1"
	case "text/x-go":
		return "go/v1"
	default:
		if len(declarations[value.MediaType]) > 0 {
			return "source-declarations/v1"
		}
		if strings.HasPrefix(value.MediaType, "text/") || value.MediaType == "application/json" || value.MediaType == "application/yaml" || value.MediaType == "application/xml" {
			return "text/v1"
		}
		return "catalog/v1"
	}
}

func Material(ctx context.Context, root string, value material.Material) (Facts, error) {
	relative := strings.TrimPrefix(value.URI, "file:")
	rootNode := graph.Node{
		ID: graph.ReferenceID(graph.KindMaterial, value.URI), Label: filepath.Base(relative), Kind: graph.KindMaterial,
		Ref: value.URI, MaterialID: value.ID, MaterialURI: value.URI,
	}
	result := Facts{Nodes: []graph.Node{rootNode}}
	path, err := material.Path(root, value)
	if err != nil {
		return result, err
	}
	var facts Facts
	switch value.MediaType {
	case "text/markdown":
		facts, err = extractMarkdown(ctx, path, relative, value.ID, result)
	case "text/x-go":
		facts, err = extractGo(ctx, path, relative, value.ID, result)
	default:
		if patterns := declarations[value.MediaType]; len(patterns) > 0 {
			facts, err = extractText(ctx, path, relative, value.ID, result, patterns)
		} else if strings.HasPrefix(value.MediaType, "text/") || value.MediaType == "application/json" || value.MediaType == "application/yaml" || value.MediaType == "application/xml" {
			facts, err = extractContent(ctx, path, result)
		} else {
			facts = result
		}
	}
	if err != nil && len(facts.Nodes) == 0 {
		facts = result
	}
	return facts, err
}

func extractMarkdown(ctx context.Context, path, relative, materialID string, result Facts) (Facts, error) {
	file, err := os.Open(path)
	if err != nil {
		return Facts{}, err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64<<10), 1<<20)
	parents := [7]string{}
	occurrences := map[string]int{}
	inFence := false
	active := 0
	for line := 1; scanner.Scan(); line++ {
		if err := ctx.Err(); err != nil {
			return Facts{}, err
		}
		text := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(text, "```") || strings.HasPrefix(text, "~~~") {
			inFence = !inFence
			continue
		}
		if inFence {
			appendContent(&result.Nodes[active].Content, scanner.Text())
			continue
		}
		level := 0
		for level < len(text) && text[level] == '#' {
			level++
		}
		if level == 0 || level > 6 || len(text) <= level || text[level] != ' ' {
			appendContent(&result.Nodes[active].Content, scanner.Text())
			continue
		}
		label := strings.TrimSpace(strings.TrimRight(text[level+1:], "#"))
		if label == "" {
			continue
		}
		key := fmt.Sprintf("%d\x00%s", level, label)
		occurrences[key]++
		ref := entityID(materialID, "section", label, occurrences[key])
		id := graph.ReferenceID(graph.KindKnowledge, ref)
		result.Nodes = append(result.Nodes, graph.Node{ID: id, Label: label, Kind: graph.KindKnowledge, Subkind: "section", Ref: ref, MaterialID: materialID, MaterialURI: "file:" + relative, Locator: fmt.Sprintf("line:%d", line)})
		active = len(result.Nodes) - 1
		parent := result.Nodes[0].ID
		for candidate := level - 1; candidate > 0; candidate-- {
			if parents[candidate] != "" {
				parent = parents[candidate]
				break
			}
		}
		result.Claims = append(result.Claims, graph.Claim{MaterialID: materialID, SourceID: parent, TargetID: id, Relation: "contains"})
		parents[level] = id
		for deeper := level + 1; deeper < len(parents); deeper++ {
			parents[deeper] = ""
		}
	}
	if err := scanner.Err(); err != nil {
		return Facts{}, err
	}
	return result, nil
}

func extractContent(ctx context.Context, path string, result Facts) (Facts, error) {
	if err := ctx.Err(); err != nil {
		return Facts{}, err
	}
	file, err := os.Open(path)
	if err != nil {
		return Facts{}, err
	}
	defer file.Close()
	content, err := io.ReadAll(io.LimitReader(file, 256<<10))
	if err != nil {
		return Facts{}, err
	}
	if !bytes.ContainsRune(content, '\x00') {
		result.Nodes[0].Content = strings.TrimSpace(string(bytes.ToValidUTF8(content, []byte("�"))))
	}
	return result, nil
}

func appendContent(destination *string, line string) {
	remaining := (32 << 10) - len(*destination)
	if remaining <= 0 {
		return
	}
	line = strings.TrimSpace(line)
	if line == "" {
		return
	}
	if *destination != "" {
		remaining--
		if remaining <= 0 {
			return
		}
		*destination += "\n"
	}
	if len(line) > remaining {
		line = line[:remaining]
		for len(line) > 0 && !utf8.ValidString(line) {
			line = line[:len(line)-1]
		}
	}
	*destination += line
}

func extractText(ctx context.Context, path, relative, materialID string, result Facts, patterns []pattern) (Facts, error) {
	file, err := os.Open(path)
	if err != nil {
		return Facts{}, err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64<<10), 1<<20)
	occurrences := map[string]int{}
	for line := 1; scanner.Scan(); line++ {
		if err := ctx.Err(); err != nil {
			return Facts{}, err
		}
		for _, candidate := range patterns {
			match := candidate.expression.FindStringSubmatch(scanner.Text())
			if len(match) != 2 {
				continue
			}
			label := match[1] + candidate.suffix
			key := candidate.kind + "\x00" + label
			occurrences[key]++
			ref := entityID(materialID, candidate.kind, label, occurrences[key])
			id := graph.ReferenceID(graph.KindKnowledge, ref)
			result.Nodes = append(result.Nodes, graph.Node{ID: id, Label: label, Kind: graph.KindKnowledge, Subkind: candidate.kind, Ref: ref, MaterialID: materialID, MaterialURI: "file:" + relative, Locator: fmt.Sprintf("line:%d", line)})
			result.Claims = append(result.Claims, graph.Claim{MaterialID: materialID, SourceID: result.Nodes[0].ID, TargetID: id, Relation: "contains"})
			break
		}
	}
	if err := scanner.Err(); err != nil {
		return Facts{}, err
	}
	return result, nil
}

func extractGo(ctx context.Context, path, relative, materialID string, result Facts) (Facts, error) {
	set := token.NewFileSet()
	parsed, err := parser.ParseFile(set, path, nil, parser.SkipObjectResolution)
	if err != nil {
		return Facts{}, err
	}
	for _, declaration := range parsed.Decls {
		if err := ctx.Err(); err != nil {
			return Facts{}, err
		}
		switch value := declaration.(type) {
		case *ast.FuncDecl:
			label := value.Name.Name + "()"
			if value.Recv != nil && len(value.Recv.List) > 0 {
				label = receiverName(value.Recv.List[0].Type) + "." + label
			}
			ref := entityID(materialID, "function", label, 1)
			id := graph.ReferenceID(graph.KindKnowledge, ref)
			result.Nodes = append(result.Nodes, graph.Node{ID: id, Label: label, Kind: graph.KindKnowledge, Subkind: "function", Ref: ref, MaterialID: materialID, MaterialURI: "file:" + relative, Locator: fmt.Sprintf("line:%d", set.Position(value.Pos()).Line)})
			result.Claims = append(result.Claims, graph.Claim{MaterialID: materialID, SourceID: result.Nodes[0].ID, TargetID: id, Relation: "contains"})
			seenCalls := map[string]bool{}
			ast.Inspect(value.Body, func(node ast.Node) bool {
				invocation, ok := node.(*ast.CallExpr)
				if ok {
					if callee := calledName(invocation.Fun); callee != "" {
						target := callee + "()"
						if !seenCalls[target] {
							seenCalls[target] = true
							result.Claims = append(result.Claims, graph.Claim{MaterialID: materialID, SourceID: id, TargetLabel: target, Relation: "calls"})
						}
					}
				}
				return true
			})
		case *ast.GenDecl:
			for _, specification := range value.Specs {
				typeSpec, ok := specification.(*ast.TypeSpec)
				if !ok {
					continue
				}
				ref := entityID(materialID, "type", typeSpec.Name.Name, 1)
				id := graph.ReferenceID(graph.KindKnowledge, ref)
				result.Nodes = append(result.Nodes, graph.Node{ID: id, Label: typeSpec.Name.Name, Kind: graph.KindKnowledge, Subkind: "type", Ref: ref, MaterialID: materialID, MaterialURI: "file:" + relative, Locator: fmt.Sprintf("line:%d", set.Position(typeSpec.Pos()).Line)})
				result.Claims = append(result.Claims, graph.Claim{MaterialID: materialID, SourceID: result.Nodes[0].ID, TargetID: id, Relation: "contains"})
			}
		}
	}
	return result, nil
}

func javascriptPatterns() []pattern {
	return []pattern{
		{regexp.MustCompile(`^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(`), "function", "()"},
		{regexp.MustCompile(`^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)\b`), "type", ""},
		{regexp.MustCompile(`^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>`), "function", "()"},
	}
}

func calledName(expression ast.Expr) string {
	switch value := expression.(type) {
	case *ast.Ident:
		return value.Name
	case *ast.SelectorExpr:
		return value.Sel.Name
	case *ast.IndexExpr:
		return calledName(value.X)
	case *ast.IndexListExpr:
		return calledName(value.X)
	default:
		return ""
	}
}

func receiverName(expression ast.Expr) string {
	switch value := expression.(type) {
	case *ast.Ident:
		return value.Name
	case *ast.StarExpr:
		return receiverName(value.X)
	case *ast.IndexExpr:
		return receiverName(value.X)
	case *ast.IndexListExpr:
		return receiverName(value.X)
	default:
		return "receiver"
	}
}

func entityID(materialID, kind, label string, occurrence int) string {
	sum := sha256.Sum256([]byte(strings.Join([]string{"entity", materialID, kind, label, fmt.Sprint(occurrence)}, "\x00")))
	return hex.EncodeToString(sum[:])
}
