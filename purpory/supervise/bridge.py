"""The only seam between context supervision and code-graph artifacts."""

from __future__ import annotations

import json
import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from purpory.supervise.repository import ContextGraphRepository


def _load_object(path: str | Path) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _slug(value: object) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or "unknown"


def _community_label(labels: dict[str, Any], community_id: object) -> str:
    key = str(community_id)
    candidates: list[Any] = [labels.get(key)]
    for container_key in ("labels", "communities", "community_labels"):
        container = labels.get(container_key)
        if isinstance(container, dict):
            candidates.append(container.get(key))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, dict):
            for name_key in ("label", "name", "title"):
                name = candidate.get(name_key)
                if isinstance(name, str) and name.strip():
                    return name.strip()
    return f"community-{key}"


def candidate_topics(
    graph_path: str | Path,
    *,
    labels_path: str | Path | None = None,
    per_community: int = 3,
) -> dict[str, Any]:
    """Derive deterministic topic candidates without importing graph libraries."""
    if per_community < 1 or per_community > 100:
        raise ValueError("per_community must be between 1 and 100")
    graph_file = Path(graph_path).expanduser().resolve()
    raw = _load_object(graph_file)
    if raw is None or not isinstance(raw.get("nodes"), list):
        return {"graph": str(graph_file), "candidates": [], "liveNodeIds": []}
    nodes = [node for node in raw["nodes"] if isinstance(node, dict)]
    links_value = raw.get("links", raw.get("edges", []))
    links = links_value if isinstance(links_value, list) else []
    labels = _load_object(labels_path) if labels_path else None
    labels = labels or {}

    degree: dict[str, int] = defaultdict(int)
    for link in links:
        if not isinstance(link, dict):
            continue
        source = str(link.get("source", ""))
        target = str(link.get("target", ""))
        if source:
            degree[source] += 1
        if target:
            degree[target] += 1

    communities: dict[str, list[dict[str, Any]]] = defaultdict(list)
    live_node_ids: set[str] = set()
    for node in nodes:
        node_id = str(node.get("id", ""))
        if not node_id:
            continue
        live_node_ids.add(node_id)
        community_id = str(node.get("community", "unassigned"))
        communities[community_id].append(node)

    candidates: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for community_id in sorted(communities):
        namespace = _community_label(labels, community_id)
        ranked = sorted(
            communities[community_id],
            key=lambda node: (
                -degree.get(str(node.get("id", "")), 0),
                str(node.get("label", node.get("id", ""))).lower(),
                str(node.get("id", "")),
            ),
        )
        for node in ranked[:per_community]:
            node_id = str(node.get("id", ""))
            label = str(node.get("label", node_id))
            source_file = str(node.get("source_file", "")).replace("\\", "/").lstrip("/")
            source = f"@repo/{source_file}" if source_file else f"graph-node:{node_id}"
            base_key = f"{_slug(namespace)}.{_slug(label)}"
            key = base_key
            if key in used_keys:
                suffix = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:8]
                key = f"{base_key}-{suffix}"
            used_keys.add(key)
            candidates.append(
                {
                    "key": key,
                    "source": source,
                    "kind": "code-area",
                    "origin": "graph-seed",
                    "nodeId": node_id,
                    "community": community_id,
                    "degree": degree.get(node_id, 0),
                }
            )
    candidates.sort(key=lambda candidate: candidate["key"])
    return {
        "graph": str(graph_file),
        "candidates": candidates,
        "liveNodeIds": sorted(live_node_ids),
    }


def seed_from_graph(
    repository: ContextGraphRepository,
    graph_path: str | Path,
    *,
    project: str | None = None,
    labels_path: str | Path | None = None,
    per_community: int = 3,
    prune: bool = True,
) -> dict[str, Any]:
    graph_file = Path(graph_path).expanduser().resolve()
    selected_project = project or graph_file.parent.parent.name or graph_file.parent.name
    try:
        imported = repository.import_graph(graph_file, project=selected_project)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        imported = {"imported": False, "project": selected_project, "nodes": 0, "edges": 0}
    derived = candidate_topics(
        graph_file, labels_path=labels_path, per_community=per_community
    )
    seeded: list[str] = []
    kept: list[str] = []
    for candidate in derived["candidates"]:
        action = repository.set_topic(
            candidate["key"],
            source=candidate["source"],
            kind=candidate["kind"],
            origin="graph-seed",
            seed_node_id=candidate["nodeId"],
            seed_graph=derived["graph"],
        )
        (kept if action == "kept" else seeded).append(candidate["key"])
    pruned = (
        repository.prune_orphaned_seeds(
            seed_graph=derived["graph"],
            candidate_keys={candidate["key"] for candidate in derived["candidates"]},
            live_node_ids=set(derived["liveNodeIds"]),
        )
        if prune
        else []
    )
    return {"seeded": seeded, "kept": kept, "pruned": pruned, "graph": imported}


def graph_payload(graph_path: str | Path, *, scope: str | None = None) -> dict[str, Any]:
    raw = _load_object(Path(graph_path).expanduser().resolve())
    if raw is None or not isinstance(raw.get("nodes"), list):
        return {"nodes": [], "links": []}
    nodes = [node for node in raw["nodes"] if isinstance(node, dict)]
    links_value = raw.get("links", raw.get("edges", []))
    links = [link for link in links_value if isinstance(link, dict)] if isinstance(links_value, list) else []
    if scope:
        normalized = scope.replace("\\", "/").strip("/")
        scoped = [
            node
            for node in nodes
            if _path_in_scope(str(node.get("source_file", "")), normalized)
        ]
        node_ids = {str(node.get("id", "")) for node in scoped}
        links = [
            link
            for link in links
            if str(link.get("source", "")) in node_ids
            and str(link.get("target", "")) in node_ids
        ]
        nodes = scoped
    return {"nodes": nodes, "links": links}


def _path_in_scope(source_file: str, scope: str) -> bool:
    source_parts = tuple(part for part in source_file.replace("\\", "/").strip("/").split("/") if part)
    scope_parts = tuple(part for part in scope.replace("\\", "/").strip("/").split("/") if part)
    return bool(scope_parts) and source_parts[: len(scope_parts)] == scope_parts
