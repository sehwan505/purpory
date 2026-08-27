package cli

import (
	"fmt"
	"strconv"
	"strings"

	product "github.com/sehwan505/purpory/internal/app"
	"github.com/sehwan505/purpory/internal/graph"
)

const (
	defaultQueryLimit    = 5
	maximumQueryLimit    = 20
	queryCharacterBudget = 4_000
	evidenceCharBudget   = 12_000
)

func renderQuery(result product.QueryResult, limit int) string {
	lines := []string{
		"[PURPORY QUERY — CONTENT NOT LOADED]",
		"Candidates are navigation hints. Open only the evidence needed for the task.",
		"Candidates:",
	}
	if len(result.Matches) == 0 {
		lines = append(lines, "- none")
	}
	for index, match := range result.Matches {
		if index == limit {
			break
		}
		details := []string{nodeKind(match.Node)}
		for _, signal := range match.Signals {
			value := signal.Kind
			if signal.Score != 0 {
				value += " " + strconv.FormatFloat(signal.Score, 'f', 3, 64)
			}
			details = append(details, value)
		}
		lines = append(lines, fmt.Sprintf("- N%d `%s` (ID `%s`) [%s] %s", index+1, nodeAddress(match.Node), match.Node.ID, strings.Join(details, "; "), oneLine(match.Node.Label)))
	}
	if len(result.Paths) > 0 {
		lines = append(lines, "Branches:")
		for index, path := range result.Paths {
			if index == limit {
				break
			}
			lines = append(lines, "- `"+oneLine(path)+"`")
		}
	}
	lines = append(lines,
		"Open evidence: `purpory explain \"<path or node ID>\" [\"<path or node ID>\" ...]`",
		"Connect candidates: `purpory path \"<A>\" \"<B>\"`",
		"Machine-readable navigation result: `purpory query --json \"<question>\"`",
	)
	return fitOutput(strings.Join(lines, "\n"), queryCharacterBudget)
}

func renderExplanations(results []product.ExplainResult) string {
	lines := []string{"[PURPORY EVIDENCE]", "Selected node content is loaded below. Connected nodes remain navigation-only."}
	for index, result := range results {
		if result.Graph == nil {
			continue
		}
		node := result.Graph.Node
		lines = append(lines, "", fmt.Sprintf("Node %d: `%s` (ID `%s`) [%s; %s; %s]", index+1, nodeAddress(node), node.ID, nodeKind(node), node.Owner, node.State), "Label: "+oneLine(node.Label))
		if source := nodeSource(node); source != "" {
			lines = append(lines, "Source: `"+source+"`")
		}
		if node.Provenance != "" {
			lines = append(lines, "Provenance: "+oneLine(node.Provenance))
		}
		lines = append(lines, "Content:", node.Content)
		if len(result.Graph.Connections) > 0 {
			lines = append(lines, "Connections (content not loaded):")
			for _, connection := range result.Graph.Connections {
				lines = append(lines, fmt.Sprintf("- %s --%s--> `%s` (ID `%s`) [%s] %s", connection.Direction, connection.Relation, nodeAddress(connection.Node), connection.Node.ID, nodeKind(connection.Node), oneLine(connection.Node.Label)))
			}
		}
	}
	lines = append(lines, "", "Follow a connection with `purpory explain \"<path or node ID>\"` or `purpory path \"<A>\" \"<B>\"`.")
	return fitOutput(strings.Join(lines, "\n"), evidenceCharBudget)
}

func renderPath(path graph.Path) string {
	lines := []string{"[PURPORY PATH — CONTENT NOT LOADED]", "Nodes:"}
	aliases := make(map[string]string, len(path.Nodes))
	for index, node := range path.Nodes {
		alias := "N" + strconv.Itoa(index+1)
		aliases[node.ID] = alias
		lines = append(lines, fmt.Sprintf("- %s `%s` (ID `%s`) [%s] %s", alias, nodeAddress(node), node.ID, nodeKind(node), oneLine(node.Label)))
	}
	if len(path.Edges) > 0 {
		lines = append(lines, "Edges:")
		for _, edge := range path.Edges {
			lines = append(lines, fmt.Sprintf("- %s --%s--> %s", aliases[edge.SourceID], edge.Relation, aliases[edge.TargetID]))
		}
	}
	if len(path.TopicPaths) > 0 {
		lines = append(lines, "Topic path:", "- "+strings.Join(path.TopicPaths, " -> "))
	}
	lines = append(lines, "Open evidence: `purpory explain \"<path or node ID>\"`")
	return fitOutput(strings.Join(lines, "\n"), queryCharacterBudget)
}

func nodeAddress(node graph.Node) string {
	if node.Path != "" {
		return oneLine(node.Path)
	}
	return oneLine(node.ID)
}

func nodeSource(node graph.Node) string {
	if node.MaterialURI == "" {
		return ""
	}
	if node.Locator == "" {
		return oneLine(node.MaterialURI)
	}
	return oneLine(node.MaterialURI + "#" + node.Locator)
}

func nodeKind(node graph.Node) string {
	if node.Subkind == "" {
		return node.Kind
	}
	return node.Kind + "/" + node.Subkind
}

func oneLine(value string) string {
	return strings.Join(strings.Fields(value), " ")
}

func fitOutput(value string, limit int) string {
	runes := []rune(value)
	if len(runes) <= limit {
		return value
	}
	suffix := []rune("\n[truncated; request fewer nodes or use --json for the full result]")
	return string(runes[:limit-len(suffix)]) + string(suffix)
}
