"""Push the canonical graph to supported external graph databases."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import networkx as nx

from purpory.analyze import _node_community_map


def _safe_identifier(value: str, fallback: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", value.replace(" ", "_").replace("-", "_"))
    return sanitized or fallback


def _properties(data: dict) -> dict:
    return {
        key: value
        for key, value in data.items()
        if isinstance(value, (str, int, float, bool)) and not key.startswith("_")
    }


def _push_graph(graph: nx.Graph, communities: dict[int, list[str]] | None, execute) -> None:
    node_community = _node_community_map(communities) if communities else {}
    for node_id, data in graph.nodes(data=True):
        properties = {**_properties(data), "id": node_id}
        community = node_community.get(node_id)
        if community is not None:
            properties["community"] = community
        label = _safe_identifier(str(data.get("file_type", "Entity")).capitalize(), "Entity")
        execute(
            f"MERGE (n:{label} {{id: $id}}) SET n += $props",
            {"id": node_id, "props": properties},
        )

    for source, target, data in graph.edges(data=True):
        relation = _safe_identifier(
            str(data.get("relation", "RELATED_TO")).upper(), "RELATED_TO"
        )
        execute(
            f"MATCH (a {{id: $src}}), (b {{id: $tgt}}) "
            f"MERGE (a)-[r:{relation}]->(b) SET r += $props",
            {"src": source, "tgt": target, "props": _properties(data)},
        )


def push_to_neo4j(
    graph: nx.Graph,
    uri: str,
    user: str,
    password: str,
    communities: dict[int, list[str]] | None = None,
) -> dict[str, int]:
    """Upsert the graph into Neo4j using its Python driver."""
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise ImportError("neo4j driver not installed. Run: pip install 'purpory[neo4j]'") from exc

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            _push_graph(graph, communities, lambda query, params: session.run(query, **params))
    finally:
        driver.close()
    return {"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()}


def push_to_falkordb(
    graph: nx.Graph,
    uri: str,
    user: str | None = None,
    password: str | None = None,
    communities: dict[int, list[str]] | None = None,
    graph_name: str = "purpory",
) -> dict[str, int]:
    """Upsert the graph into a named FalkorDB graph."""
    try:
        from falkordb import FalkorDB
    except ImportError as exc:
        raise ImportError("falkordb SDK not installed. Run: pip install 'purpory[falkordb]'") from exc

    parsed = urlparse(uri if "://" in uri else f"redis://{uri}")
    connect_password = parsed.password or password
    connect_user = parsed.username or (user if connect_password else None)
    target = FalkorDB(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        username=connect_user,
        password=connect_password,
    ).select_graph(graph_name)
    _push_graph(graph, communities, target.query)

    return {"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()}
