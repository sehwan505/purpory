# DO NOT import from purpory.extract here — direction is extract.py → extractors/ only.
from __future__ import annotations

from pathlib import Path

from purpory.ids import make_id

# Language built-in globals that AST may classify as call targets when used as
# constructors or coercion functions (e.g. String(x), Number(x), Boolean(x)).
# Without this filter they become god-nodes accumulating spurious edges from
# every call site. Filter applied at same-file and cross-file resolution.
# See issue #726.
_LANGUAGE_BUILTIN_GLOBALS: frozenset[str] = frozenset({
    # JavaScript / TypeScript ECMAScript built-ins
    "String", "Number", "Boolean", "Object", "Array", "Symbol", "BigInt",
    "Date", "RegExp", "Error", "TypeError", "RangeError", "SyntaxError",
    "ReferenceError", "EvalError", "URIError",
    "Promise", "Map", "Set", "WeakMap", "WeakSet", "JSON", "Math",
    "Reflect", "Proxy", "Intl",
    "parseInt", "parseFloat", "isNaN", "isFinite",
    "encodeURIComponent", "decodeURIComponent", "encodeURI", "decodeURI",
    # Browser / Node common globals
    "URL", "URLSearchParams", "FormData", "Blob", "File",
    "Headers", "Request", "Response", "AbortController", "AbortSignal",
    "TextEncoder", "TextDecoder", "console",
    # Python built-in callables
    "str", "int", "float", "bool", "list", "dict", "set", "tuple", "bytes",
    "len", "range", "enumerate", "zip", "map", "filter", "sum", "min", "max",
    "print", "open", "isinstance", "type", "super", "sorted", "reversed",
    "any", "all", "abs", "round", "next", "iter", "hash", "id", "repr",
    "callable", "getattr", "setattr", "hasattr", "delattr", "vars", "dir",
})


def _make_id(*parts: str) -> str:
    return make_id(*parts)


def _file_stem(path: Path) -> str:
    """Stem used as the node-ID prefix for a file and its symbols.

    The full path (extension dropped) is preserved as path segments; ``make_id``
    later collapses the separators to underscores. Using every segment — not just
    the immediate parent dir (#1504) — means same-named files in different
    directories get distinct IDs instead of colliding into one
    last-writer-wins node:

        docs/v1/api/README.md -> docs/v1/api/README -> docs_v1_api_readme
        docs/v2/api/README.md -> docs/v2/api/README -> docs_v2_api_readme

    Top-level files keep a bare stem (``setup.py`` -> ``setup``). When passed an
    absolute path the whole path is encoded; the extract() id-remap post-pass
    re-derives the canonical repo-relative form from ``source_file`` so the on-disk
    location can't leak into the persisted IDs (#502).

    Returns "" for a path with no name (``Path('.')`` — a source_file that equals
    the scan root, so it has no per-file stem). Guarding here keeps
    ``path.with_suffix("")`` from raising ``ValueError: '.' has an empty name`` and
    protects every caller, not just ``_semantic_id_remap`` (#1618)."""
    if not path.name:
        return ""
    return path.with_suffix("").as_posix()


def _read_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


class ExtractionContext:
    def __init__(self, path: Path, language: str):
        self.path = path
        self.str_path = str(path)
        self.stem = _file_stem(path)
        self.language = language
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.raw_calls: list[dict] = []
        self.seen_ids: set[str] = set()

    @property
    def file_nid(self) -> str:
        return _make_id(self.str_path)

    def add_node(self, nid: str, label: str, line: int, kind: str = "code", metadata: dict | None = None) -> None:
        if nid and nid not in self.seen_ids:
            self.seen_ids.add(nid)
            node = {
                "id": nid,
                "label": label,
                "file_type": "code",
                "source_file": self.str_path,
                "source_location": f"L{line}",
            }
            merged = dict(metadata or {})
            merged.setdefault("language", self.language)
            merged.setdefault("kind", kind)

            from purpory.security import sanitize_metadata
            node["metadata"] = sanitize_metadata(merged)
            self.nodes.append(node)

    def add_edge(self, src: str, tgt: str, relation: str, line: int,
                 confidence: str = "EXTRACTED", weight: float = 1.0,
                 context: str | None = None, metadata: dict | None = None) -> None:
        if not src or not tgt or src == tgt:
            return
        edge = {
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": confidence,
            "source_file": self.str_path,
            "source_location": f"L{line}",
            "weight": weight,
        }
        if context:
            edge["context"] = context
        if metadata:
            from purpory.security import sanitize_metadata
            edge["metadata"] = sanitize_metadata(metadata)
        self.edges.append(edge)

    def ensure_named_node(self, name: str, line: int) -> str:
        nid = _make_id(self.stem, name)
        if nid in self.seen_ids:
            return nid
        nid = _make_id(name)
        if nid not in self.seen_ids:
            self.seen_ids.add(nid)
            self.nodes.append({
                "id": nid,
                "label": name,
                "file_type": "code",
                "source_file": "",
                "source_location": "",
                "origin_file": self.str_path,
            })
        return nid

    def get_result(self) -> dict:
        valid_ids = self.seen_ids
        clean_edges = []
        for edge in self.edges:
            src, tgt = edge["source"], edge["target"]
            if src in valid_ids and (
                tgt in valid_ids or
                edge["relation"] in ("imports", "imports_from", "depends_on") or
                edge.get("context") == "script_invocation"
            ):
                clean_edges.append(edge)
        return {
            "nodes": self.nodes,
            "edges": clean_edges,
            "raw_calls": self.raw_calls,
        }
