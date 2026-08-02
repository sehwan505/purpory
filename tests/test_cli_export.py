from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import networkx as nx
import pytest

from purpory.cli import dispatch_command


def _graph(path) -> None:
    path.write_text(
        json.dumps(
            {
                "nodes": [{"id": "a", "label": "A", "community": 0}],
                "links": [],
                "analysis": {
                    "communities": {"0": ["a"]},
                    "cohesion": {"0": 1.0},
                    "gods": [],
                },
            }
        ),
        encoding="utf-8",
    )


def test_export_json_from_explicit_compatibility_graph(tmp_path) -> None:
    source = tmp_path / "source.json"
    output = tmp_path / "output.json"
    _graph(source)

    dispatch_command("export", ["json", "--graph", str(source), "--output", str(output)])

    assert json.loads(output.read_text(encoding="utf-8"))["nodes"][0]["id"] == "a"


def test_export_report_and_wiki_are_retained(tmp_path) -> None:
    source = tmp_path / "source.json"
    report = tmp_path / "REPORT.md"
    wiki = tmp_path / "wiki"
    _graph(source)

    dispatch_command("export", ["report", "--graph", str(source), "--output", str(report)])
    dispatch_command("export", ["wiki", "--graph", str(source), "--output", str(wiki)])

    assert report.is_file()
    assert (wiki / "index.md").is_file()


def test_removed_visualization_format_is_rejected() -> None:
    with pytest.raises(SystemExit):
        dispatch_command("export", ["html"])


@pytest.mark.parametrize(
    ("format_name", "uri", "environment"),
    (
        ("neo4j", "bolt://localhost:7687", "NEO4J_PASSWORD"),
        ("falkordb", "falkordb://localhost:6379", "FALKORDB_PASSWORD"),
    ),
)
def test_graph_database_push_is_retained(
    tmp_path, monkeypatch, format_name, uri, environment
) -> None:
    source = tmp_path / "source.json"
    _graph(source)
    captured = {}

    def fake_push(graph, **kwargs):
        captured.update(kwargs)
        return {"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()}

    monkeypatch.setattr(
        f"purpory.exporters.graphdb.push_to_{format_name}", fake_push
    )
    monkeypatch.setenv(environment, "secret")

    dispatch_command(
        "export",
        [format_name, "--graph", str(source), "--push", uri],
    )

    assert captured["uri"] == uri
    assert captured["password"] == "secret"
    assert captured["communities"] == {0: ["a"]}


def test_neo4j_push_requires_password(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.json"
    _graph(source)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    with pytest.raises(SystemExit) as exc:
        dispatch_command(
            "export",
            ["neo4j", "--graph", str(source), "--push", "bolt://localhost:7687"],
        )

    assert exc.value.code == 2


def test_graph_database_exporters_upsert_nodes_and_edges(monkeypatch) -> None:
    from purpory.exporters.graphdb import push_to_falkordb, push_to_neo4j

    graph = nx.Graph()
    graph.add_node("a", file_type="code")
    graph.add_node("b", file_type="document")
    graph.add_edge("a", "b", relation="calls")
    neo4j = MagicMock()
    monkeypatch.setitem(sys.modules, "neo4j", neo4j)
    falkordb = MagicMock()
    monkeypatch.setitem(sys.modules, "falkordb", falkordb)
    neo4j_session = neo4j.GraphDatabase.driver.return_value.session.return_value.__enter__.return_value
    falkor_graph = falkordb.FalkorDB.return_value.select_graph.return_value

    assert push_to_neo4j(graph, "bolt://localhost", "neo4j", "secret") == {
        "nodes": 2,
        "edges": 1,
    }
    assert push_to_falkordb(graph, "localhost:6379") == {"nodes": 2, "edges": 1}
    assert neo4j_session.run.call_count == falkor_graph.query.call_count == 3
