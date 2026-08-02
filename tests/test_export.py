from __future__ import annotations

import json

import networkx as nx

from purpory.export import attach_hyperedges, graph_data, to_json


def test_json_export_preserves_communities_edges_and_hyperedges(tmp_path) -> None:
    graph = nx.Graph()
    graph.add_node("a", label="Áuth")
    graph.add_node("b", label="Database")
    graph.add_edge(
        "a",
        "b",
        relation="calls",
        confidence="EXTRACTED",
        _src="a",
        _tgt="b",
    )
    attach_hyperedges(graph, [{"id": "h1", "members": ["a", "b"]}])

    output = tmp_path / "graph.json"
    assert to_json(graph, {1: ["a", "b"]}, str(output), community_labels={1: "Core"})
    data = json.loads(output.read_text(encoding="utf-8"))

    assert {node["community_name"] for node in data["nodes"]} == {"Core"}
    assert data["links"][0]["source"] == "a"
    assert data["links"][0]["confidence_score"] == 1.0
    assert data["hyperedges"][0]["id"] == "h1"


def test_graph_data_is_an_explicit_compatibility_payload() -> None:
    graph = nx.Graph()
    graph.add_node("node", label="Node")

    data = graph_data(graph, {})

    assert data["nodes"][0]["id"] == "node"
    assert "links" in data
