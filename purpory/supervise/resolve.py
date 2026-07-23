"""Live pointer resolution and bounded code-graph enrichment."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from purpory.supervise.bridge import graph_payload
from purpory.supervise.repository import ContextGraphRepository, stable_json

MAX_POINTER_BYTES = 1_048_576
MAX_DIRECTORY_FILES = 250
DEFAULT_GOD_NODE_LIMIT = 5
DEFAULT_EDGE_LIMIT = 10
CONFIDENCE_ORDER = {"EXTRACTED": 0, "INFERRED": 1, "AMBIGUOUS": 2}


def _safe_repo_path(source: str, root: Path) -> tuple[Path | None, str | None]:
    prefix = next((value for value in ("@root/", "@repo/") if source.startswith(value)), None)
    if prefix is None:
        return None, "unsupported pointer scheme"
    relative = source[len(prefix) :]
    if not relative:
        return None, "pointer path is empty"
    root = root.expanduser().resolve()
    candidate = (root / relative).resolve()
    try:
        if os.path.commonpath((str(root), str(candidate))) != str(root):
            return None, "pointer escapes repository root"
    except ValueError:
        return None, "pointer escapes repository root"
    return candidate, None


def _read_file(path: Path) -> tuple[str, bool]:
    with path.open("rb") as handle:
        data = handle.read(MAX_POINTER_BYTES + 1)
    truncated = len(data) > MAX_POINTER_BYTES
    content = data[:MAX_POINTER_BYTES].decode("utf-8", errors="replace")
    if truncated:
        content += f"\n\n[truncated after {MAX_POINTER_BYTES} bytes]"
    return content, truncated


def _list_directory(path: Path, root: Path) -> tuple[str, bool]:
    files: list[str] = []
    for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if len(files) >= MAX_DIRECTORY_FILES:
            break
        try:
            resolved = candidate.resolve()
            if not candidate.is_file() or os.path.commonpath((str(root), str(resolved))) != str(
                root
            ):
                continue
        except (OSError, ValueError):
            continue
        files.append(resolved.relative_to(root).as_posix())
    truncated = len(files) >= MAX_DIRECTORY_FILES
    content = "\n".join(files)
    if truncated:
        content += f"\n[listing capped at {MAX_DIRECTORY_FILES} files]"
    return content, truncated


def graph_slice(
    graph_source: str | Path | ContextGraphRepository,
    scope: str,
    *,
    project: str | None = None,
    god_node_limit: int = DEFAULT_GOD_NODE_LIMIT,
    edge_limit: int = DEFAULT_EDGE_LIMIT,
) -> dict[str, Any] | None:
    """Return a deterministic, explicitly capped graph slice for a path scope."""
    payload = _graph_payload(graph_source, scope=scope, project=project)
    nodes = payload["nodes"]
    links = payload["links"]
    if not nodes:
        return None

    try:
        full_payload = _graph_payload(graph_source, project=project)
    except (OSError, ValueError, json.JSONDecodeError):
        full_payload = payload
    degree: dict[str, int] = {}
    for link in full_payload["links"]:
        source = str(link.get("source", ""))
        target = str(link.get("target", ""))
        degree[source] = degree.get(source, 0) + 1
        degree[target] = degree.get(target, 0) + 1

    ranked_nodes = sorted(
        nodes,
        key=lambda node: (
            -degree.get(str(node.get("id", "")), 0),
            str(node.get("label", node.get("id", ""))).lower(),
            str(node.get("id", "")),
        ),
    )
    ranked_links = sorted(
        links,
        key=lambda link: (
            CONFIDENCE_ORDER.get(str(link.get("confidence", "AMBIGUOUS")).upper(), 3),
            -_weight(link.get("weight", 0)),
            str(link.get("source", "")),
            str(link.get("target", "")),
            str(link.get("relation", "")),
        ),
    )
    return {
        "scope": scope,
        "godNodes": [
            {
                "id": str(node.get("id", "")),
                "label": str(node.get("label", node.get("id", ""))),
                "degree": degree.get(str(node.get("id", "")), 0),
                "sourceFile": node.get("source_file"),
            }
            for node in ranked_nodes[:god_node_limit]
        ],
        "edges": [
            {
                "source": link.get("source"),
                "target": link.get("target"),
                "relation": link.get("relation"),
                "confidence": link.get("confidence"),
                "weight": link.get("weight"),
            }
            for link in ranked_links[:edge_limit]
        ],
        "truncated": {
            "godNodes": max(0, len(ranked_nodes) - god_node_limit),
            "edges": max(0, len(ranked_links) - edge_limit),
        },
    }


def resolve_topic(
    topic: dict[str, Any],
    *,
    root: str | Path,
    graph_path: str | Path | None = None,
    repository: ContextGraphRepository | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Resolve a topic at read time; pointer content is never cached."""
    if topic.get("value") is not None:
        return {"mode": "inline", "value": str(topic["value"]), "truncated": False}

    source = str(topic.get("source") or "")
    if source.startswith(("http://", "https://")):
        return {"mode": "external", "value": source, "truncated": False}
    if source.startswith("graph-node:"):
        return {"mode": "graph-node", "value": source, "truncated": False}

    root_path = Path(root).expanduser().resolve()
    path, error = _safe_repo_path(source, root_path)
    if path is None:
        return {"mode": "unresolved", "value": source, "error": error, "truncated": False}
    try:
        if path.is_file():
            value, truncated = _read_file(path)
            mode = "pointer-file"
        elif path.is_dir():
            value, truncated = _list_directory(path, root_path)
            mode = "pointer-dir"
        else:
            return {
                "mode": "unresolved",
                "value": source,
                "error": "pointer target does not exist",
                "truncated": False,
            }
    except (OSError, UnicodeError) as exc:
        return {
            "mode": "unresolved",
            "value": source,
            "error": f"pointer could not be read: {exc}",
            "truncated": False,
        }

    result: dict[str, Any] = {"mode": mode, "value": value, "truncated": truncated}
    graph_source: str | Path | ContextGraphRepository | None = repository or graph_path
    if graph_source is not None and topic.get("kind") == "code-area":
        relative_scope = path.relative_to(root_path).as_posix()
        enrichment = graph_slice(graph_source, relative_scope, project=project)
        if enrichment is not None:
            result["graph"] = enrichment
    return result


def _weight(value: object) -> float:
    if not isinstance(value, (int, float, str)):
        return 0.0
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _graph_payload(
    source: str | Path | ContextGraphRepository,
    *,
    scope: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    if isinstance(source, ContextGraphRepository):
        if not project:
            raise ValueError("project is required for repository graph queries")
        return source.graph_payload(project=project, scope=scope)
    return graph_payload(source, scope=scope)


def rendered_injection(topic: dict[str, Any], resolved: dict[str, Any]) -> str:
    body = [f"## {topic['key']}", "", resolved["value"]]
    if topic.get("stale"):
        body.extend(("", "[stale: human confirmation recommended]"))
    if resolved.get("graph") is not None:
        body.extend(("", "### Code graph", "", stable_json(resolved["graph"])))
    if resolved.get("error"):
        body.extend(("", f"[unresolved: {resolved['error']}]"))
    return "\n".join(body).rstrip() + "\n"
