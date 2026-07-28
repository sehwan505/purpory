from __future__ import annotations
import json
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
import networkx as nx
from networkx.readwrite import json_graph as _jg

_GLOBAL_DIR = Path.home() / ".purpory"
_GLOBAL_GRAPH = _GLOBAL_DIR / "global-graph.json"
_GLOBAL_MANIFEST = _GLOBAL_DIR / "global-manifest.json"


def _load_manifest() -> dict:
    if _GLOBAL_MANIFEST.exists():
        try:
            return json.loads(_GLOBAL_MANIFEST.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"global manifest at {_GLOBAL_MANIFEST} is unreadable; "
                "repair or remove it explicitly before continuing"
            ) from exc
    return {"version": 1, "repos": {}}


def _save_manifest(manifest: dict) -> None:
    _GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    from purpory.paths import write_json_atomic
    write_json_atomic(_GLOBAL_MANIFEST, manifest, indent=2)


def _load_global_graph() -> nx.Graph:
    if _GLOBAL_GRAPH.exists():
        from purpory.security import check_graph_file_size_cap
        check_graph_file_size_cap(_GLOBAL_GRAPH)
        data = json.loads(_GLOBAL_GRAPH.read_text(encoding="utf-8"))
        if "links" not in data and "edges" in data:
            data = dict(data, links=data["edges"])
        try:
            return _jg.node_link_graph(data, edges="links")
        except TypeError:
            return _jg.node_link_graph(data)
    return nx.Graph()


def _save_global_graph(G: nx.Graph) -> None:
    _GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = _jg.node_link_data(G, edges="links")
    except TypeError:
        data = _jg.node_link_data(G)
    from purpory.paths import write_json_atomic
    write_json_atomic(_GLOBAL_GRAPH, data, indent=2)


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def global_add(source_path: Path, repo_tag: str) -> dict:
    """Add or update a project graph in the global graph.

    Returns a summary dict with keys: repo_tag, nodes_added, nodes_removed, skipped.
    Skipped=True means the source graph hasn't changed since last add.
    """
    if not source_path.exists():
        raise FileNotFoundError(f"graph not found: {source_path}")
    from purpory.security import check_graph_file_size_cap

    check_graph_file_size_cap(source_path)
    data = json.loads(source_path.read_text(encoding="utf-8"))
    return _global_add(
        data,
        repo_tag,
        source=str(source_path.resolve()),
        source_hash=_file_hash(source_path),
    )


def global_add_data(data: dict, repo_tag: str, *, source: str = "sqlite") -> dict:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    source_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return _global_add(data, repo_tag, source=source, source_hash=source_hash)


def _global_add(
    data: dict,
    repo_tag: str,
    *,
    source: str,
    source_hash: str,
) -> dict:
    from purpory.build import prefix_graph_for_global, prune_repo_from_graph

    manifest = _load_manifest()
    existing = manifest["repos"].get(repo_tag, {})
    existing_path = existing.get("source_path", "")
    if existing_path and existing_path != source:
        print(
            f"[purpory global] warning: repo tag '{repo_tag}' previously pointed to "
            f"{existing_path!r}, now updating to {source!r}. "
            f"Use --as <tag> to give it a different name.",
            file=sys.stderr,
        )
    if existing.get("source_hash") == source_hash:
        return {"repo_tag": repo_tag, "nodes_added": 0, "nodes_removed": 0, "skipped": True}

    if "links" not in data and "edges" in data:
        data = dict(data, links=data["edges"])
    try:
        src_G = _jg.node_link_graph(data, edges="links")
    except TypeError:
        src_G = _jg.node_link_graph(data)

    # Prefix IDs for cross-project isolation
    prefixed = prefix_graph_for_global(src_G, repo_tag)

    # Load global graph and prune stale nodes for this repo
    G = _load_global_graph()
    removed = prune_repo_from_graph(G, repo_tag)

    # Merge external-library nodes (no source_file) by label to avoid duplication
    external_labels = {
        d.get("label", ""): n
        for n, d in G.nodes(data=True)
        if not d.get("source_file") and d.get("label")
    }
    # Map each deduplicated external onto the existing global node so that
    # edges incident to it can be rewired instead of dropped.
    remap = {}
    for node, data in prefixed.nodes(data=True):
        if not data.get("source_file") and data.get("label") in external_labels:
            remap[node] = external_labels[data["label"]]

    # Compose: add prefixed nodes (except deduplicated externals) into global graph
    for node, data in prefixed.nodes(data=True):
        if node not in remap:
            G.add_node(node, **data)
    for u, v, data in prefixed.edges(data=True):
        u = remap.get(u, u)
        v = remap.get(v, v)
        if u != v:  # don't introduce self-loops via remapping
            G.add_edge(u, v, **data)

    added = prefixed.number_of_nodes() - len(remap)
    _save_global_graph(G)

    manifest["repos"][repo_tag] = {
        "added_at": datetime.now(timezone.utc).isoformat(),
        "source_path": source,
        "node_count": added,
        "edge_count": prefixed.number_of_edges(),
        "source_hash": source_hash,
    }
    _save_manifest(manifest)

    return {"repo_tag": repo_tag, "nodes_added": added, "nodes_removed": removed, "skipped": False}


def global_remove(repo_tag: str) -> int:
    """Remove all nodes for repo_tag from the global graph. Returns count removed."""
    from purpory.build import prune_repo_from_graph

    manifest = _load_manifest()
    if repo_tag not in manifest["repos"]:
        raise KeyError(f"repo '{repo_tag}' not in global graph")

    G = _load_global_graph()
    removed = prune_repo_from_graph(G, repo_tag)
    _save_global_graph(G)

    del manifest["repos"][repo_tag]
    _save_manifest(manifest)
    return removed


def global_list() -> dict:
    """Return the manifest repos dict."""
    return _load_manifest().get("repos", {})


def global_path() -> Path:
    return _GLOBAL_GRAPH
