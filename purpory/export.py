"""Explicit JSON compatibility export for the canonical context graph."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph

from purpory.analyze import _node_community_map
_BACKUP_ARTIFACTS = [
    "graph.json",
    "GRAPH_REPORT.md",
    ".purpory_labels.json",
    ".purpory_analysis.json",
    "manifest.json",
    ".purpory_semantic_marker",
    "cost.json",
]
_CONFIDENCE_SCORE_DEFAULTS = {"EXTRACTED": 1.0, "INFERRED": 0.5, "AMBIGUOUS": 0.2}
MALFORMED_GRAPH = object()


def backup_if_protected(out_dir: Path) -> Path | None:
    if os.environ.get("PURPORY_NO_BACKUP"):
        return None
    out = Path(out_dir)
    graph = out / "graph.json"
    if not graph.exists():
        return None
    semantic = (out / ".purpory_semantic_marker").exists()
    curated = False
    labels_file = out / ".purpory_labels.json"
    if labels_file.exists():
        try:
            labels = json.loads(labels_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not inspect curated labels in {labels_file}: {exc}") from exc
        if not isinstance(labels, dict):
            raise ValueError(f"community labels must contain a JSON object: {labels_file}")
        curated = any(value != f"Community {key}" for key, value in labels.items())
    if not semantic and not curated:
        return None

    backup = out / date.today().isoformat()
    if (backup / "graph.json").exists():
        if hashlib.sha256(graph.read_bytes()).digest() == hashlib.sha256(
            (backup / "graph.json").read_bytes()
        ).digest():
            return backup
    try:
        backup.mkdir(parents=True, exist_ok=True)
        copied = 0
        for name in _BACKUP_ARTIFACTS:
            source = out / name
            if source.exists():
                shutil.copy2(source, backup / name)
                copied += 1
    except (OSError, shutil.Error) as exc:
        raise RuntimeError(f"could not back up protected graph in {out}: {exc}") from exc
    if not copied:
        raise RuntimeError(f"protected graph in {out} had no readable artifacts to back up")
    reason = "+".join(filter(None, ("semantic" if semantic else "", "curated" if curated else "")))
    print(f"[purpory] backed up {reason} graph ({copied} files) -> {backup.name}/")
    return backup


def attach_hyperedges(graph: nx.Graph, hyperedges: list) -> None:
    existing = graph.graph.get("hyperedges", [])
    seen = {item["id"] for item in existing}
    for hyperedge in hyperedges:
        if hyperedge.get("id") and hyperedge["id"] not in seen:
            existing.append(hyperedge)
            seen.add(hyperedge["id"])
    graph.graph["hyperedges"] = existing


def _git_head() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def existing_graph_node_count(path: str | Path):
    target = Path(path)
    if not target.exists():
        return None
    from purpory.security import check_graph_file_size_cap

    try:
        check_graph_file_size_cap(target)
        raw = target.read_text(encoding="utf-8")
    except Exception:
        try:
            return MALFORMED_GRAPH if target.stat().st_size else None
        except Exception:
            return None
    if not raw.strip():
        return None
    try:
        nodes = json.loads(raw).get("nodes")
    except Exception:
        return MALFORMED_GRAPH
    return len(nodes) if isinstance(nodes, list) else MALFORMED_GRAPH


def to_json(
    graph: nx.Graph,
    communities: dict[int, list[str]],
    output_path: str,
    *,
    force: bool = False,
    built_at_commit: str | None = None,
    community_labels: dict[int, str] | None = None,
) -> bool:
    existing = existing_graph_node_count(output_path) if not force else None
    if existing is MALFORMED_GRAPH:
        print(
            f"[purpory] WARNING: existing {output_path} is malformed; refusing to overwrite. "
            "Pass force=True to override.",
            file=sys.stderr,
        )
        return False
    if isinstance(existing, int) and graph.number_of_nodes() < existing:
        print(
            f"[purpory] WARNING: new graph has {graph.number_of_nodes()} nodes but existing "
            f"graph.json has {existing}. Refusing to overwrite; pass force=True after verifying.",
            file=sys.stderr,
        )
        return False
    from purpory.paths import write_json_atomic

    write_json_atomic(
        output_path,
        graph_data(
            graph,
            communities,
            built_at_commit=built_at_commit,
            community_labels=community_labels,
        ),
        indent=2,
    )
    return True


def graph_data(
    graph: nx.Graph,
    communities: dict[int, list[str]],
    *,
    built_at_commit: str | None = None,
    community_labels: dict[int, str] | None = None,
) -> dict:
    node_community = _node_community_map(communities)
    labels = {int(key): value for key, value in (community_labels or {}).items()}
    try:
        data = json_graph.node_link_data(graph, edges="links")
    except TypeError:
        data = json_graph.node_link_data(graph)
    for node in data["nodes"]:
        community = node_community.get(node["id"])
        node["community"] = community
        if community is not None and labels:
            node["community_name"] = labels.get(community, f"Community {community}")
        node["norm_label"] = str(node.get("label") or "").casefold()
    for link in data["links"]:
        link.setdefault(
            "confidence_score",
            _CONFIDENCE_SCORE_DEFAULTS.get(link.get("confidence", "EXTRACTED"), 1.0),
        )
        source = link.pop("_src", None)
        target = link.pop("_tgt", None)
        if source is not None and target is not None:
            link["source"], link["target"] = source, target
    data["hyperedges"] = graph.graph.get("hyperedges", [])
    commit = built_at_commit if built_at_commit is not None else _git_head()
    if commit:
        data["built_at_commit"] = commit
    return data


def prune_dangling_edges(data: dict) -> tuple[dict, int]:
    node_ids = {node["id"] for node in data["nodes"]}
    key = "links" if "links" in data else "edges"
    before = len(data[key])
    data[key] = [
        edge for edge in data[key] if edge["source"] in node_ids and edge["target"] in node_ids
    ]
    return data, before - len(data[key])
