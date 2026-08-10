"""purpory command dispatch — every non-install subcommand.

Extracted verbatim from __main__.main(); __main__ now calls dispatch_command(cmd)
after the install/platform dispatch. Kept out of __main__ to shrink the CLI entry
module. The path-redirect (`purpory <path>` -> extract) re-enters via a lazy
import of main to avoid a cli<->__main__ import cycle.
"""

from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

from purpory.paths import PURPORY_OUT as _PURPORY_OUT

_COMMANDS = {
    "add": "fetch a URL and update the graph",
    "affected": "find nodes affected by a symbol",
    "benchmark": "benchmark graph queries",
    "check-update": "check for pending semantic updates",
    "claude": "manage Claude Code integration",
    "cluster-only": "rerun graph clustering",
    "codex": "manage Codex integration",
    "dashboard": "launch the supervision dashboard",
    "embed": "materialize embeddings for used context",
    "explain": "explain a graph node",
    "export": "export json, report, wiki, or push to a graph database",
    "extract": "run structural and semantic extraction",
    "hook": "manage Git hooks",
    "import": "import a graph.json compatibility artifact",
    "label": "label graph communities",
    "model": "manage local models in Ollama",
    "path": "find a path between graph nodes",
    "preflight": "run agent prompt preflight",
    "prepare": "prepare context for an agent request",
    "prs": "inspect pull requests",
    "query": "query the canonical graph",
    "remember": "store durable human context",
    "session-end": "queue or process session memory reconciliation",
    "update": "rebuild the structural graph",
}


def _root_parser(version: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="purpory")
    parser.add_argument("--version", "-v", action="version", version=f"purpory {version}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    for command, help_text in _COMMANDS.items():
        commands.add_parser(command, add_help=False, help=help_text)
    return parser


def run_cli(arguments: list[str] | None = None, *, version: str = "unknown") -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    raw = list(sys.argv[1:] if arguments is None else arguments)
    if "-?" in raw:
        raw[raw.index("-?")] = "--help"
    parser = _root_parser(version)
    try:
        options, remaining = parser.parse_known_args(raw)
    except SystemExit as exc:
        if exc.code in (None, 0):
            return
        raise
    if options.command is None:
        parser.print_help()
        return
    from purpory.install import dispatch_install_cli

    if not dispatch_install_cli(options.command, remaining):
        dispatch_command(options.command, remaining)


def _load_graph_data(
    graph_path: str | Path | None = None,
    *,
    root: str | Path | None = None,
) -> tuple[dict, str, Path | None]:
    if graph_path is not None:
        path = Path(graph_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"graph file not found: {path}")
        if path.suffix != ".json":
            raise ValueError("graph file must be a .json file")
        from purpory.security import check_graph_file_size_cap

        check_graph_file_size_cap(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("nodes"), list):
            raise ValueError("graph file must contain a nodes list")
        return value, str(path), path

    from purpory.supervise.identity import resolve_project_id, resolve_project_root
    from purpory.supervise.repository import ContextGraphRepository

    root = resolve_project_root(root or Path.cwd())
    repository = ContextGraphRepository()
    value = repository.structural_graph(project=resolve_project_id(root))
    if value is None:
        raise FileNotFoundError(f"no structural graph in {repository.path}; run purpory extract")
    return value, f"{repository.path}#{resolve_project_id(root)}", None


def _networkx_graph(graph: dict, *, directed: bool = False):
    from networkx.readwrite import json_graph

    raw = graph
    if "links" not in raw and "edges" in raw:
        raw = {**raw, "links": raw["edges"]}
    if directed:
        raw = {**raw, "directed": True}
    try:
        return json_graph.node_link_graph(raw, edges="links")
    except TypeError:
        return json_graph.node_link_graph(raw)


def _stamped_manifest_files(
    files_by_type: dict[str, list[str]],
    sem_result: dict,
    root: Path,
    partial_source_files: "set[str] | None" = None,
) -> dict[str, list[str]]:
    """Manifest-safe files dict: only stamp semantic files that actually
    produced output (cache hit or fresh extraction). Files whose chunk failed
    have no source_file entry in sem_result — leaving their semantic_hash
    empty so detect_incremental re-queues them (#933).

    A file in ``partial_source_files`` DID produce output this run, but only a
    truncated fragment of it, so it is excluded from stamping too — otherwise
    detect_incremental would see it "done" and never re-dispatch it, leaving the
    incomplete node set live forever on the warm-incremental path. Same #933
    mechanism: leave it unstamped and it is re-queued next run.

    Both sides of the membership test are resolved against the scan ``root``
    before comparing (#1897): node/edge/hyperedge ``source_file`` values are
    root-relative on a fresh extraction while ``files_by_type`` entries are
    absolute (from detect()), so a raw string comparison never matched and
    every freshly-extracted semantic doc was dropped from the manifest.
    Mirrors the #1890 path normalization in purpory.llm.

    Hyperedges are counted as output (#1920): a chunk whose only result for a
    document is a hyperedge (3+ nodes sharing a concept) is valid output that
    the semantic cache persists per-``source_file`` — omitting it here left the
    doc unstamped, so detect_incremental re-queued it on every run. The stamping
    condition mirrors the cache-write keying (a hyperedge carries its own
    ``source_file``); do not derive it from member nodes.
    """
    root = Path(root)

    def _resolve(value: str) -> Path:
        p = Path(value)
        if not p.is_absolute():
            p = root / p
        try:
            return p.resolve()
        except (OSError, RuntimeError):
            return p

    sem_extracted: set[Path] = set()
    for coll in ("nodes", "edges", "hyperedges"):
        for item in sem_result.get(coll, []):
            sf = item.get("source_file", "")
            if sf:
                sem_extracted.add(_resolve(sf))
    partial_resolved = {_resolve(p) for p in (partial_source_files or set())}
    sem_types = {"document", "paper", "image"}
    return {
        ftype: [
            f
            for f in flist
            if ftype not in sem_types
            or (_resolve(f) in sem_extracted and _resolve(f) not in partial_resolved)
        ]
        for ftype, flist in files_by_type.items()
    }


def _stale_graph_sources(
    graph_source: Path | dict,
    scan_root: Path,
    seen_files: set[str],
) -> list[str]:
    """Source files graph.json still references but the current scan no longer
    contains (#1909).

    Incremental extract's prune set was historically derived from the manifest
    alone (``manifest - corpus``), so a file that became EXCLUDED
    (.purporyignore/.gitignore/--exclude changed) without being listed in the
    manifest kept its stale nodes in graph.json forever. Derive prune
    candidates from the graph's own node ``source_file``s instead: anything
    the graph references that the post-exclude detect corpus no longer
    contains is stale, whether the file was deleted or newly excluded.

    Only IN-ROOT paths are candidates: out-of-root/absolute entries
    (--include sources, symlinked external corpora) are never walked by
    detect, so their absence from the corpus is not staleness evidence.
    Relative entries are re-anchored against both the scan root and the
    graph's own output root; only anchors that land inside the scan root
    count. Since #1941 extracts always store source_file relative to the SCAN
    root, so the scan-root anchor is the live one; the out-root anchor stays
    for graphs written by <=0.9.16, which stored them relative to the OUT root
    (e.g. ``../project/x.py``, #555/#1899).
    ``seen_files`` must be the FULL detect output including unclassified
    files, so nodes from walked-but-unsupported sources (e.g. introspected
    Cargo.toml manifests) are not misread as stale.
    """
    if isinstance(graph_source, dict):
        data = graph_source
        out_base = scan_root
    else:
        try:
            data = json.loads(graph_source.read_text(encoding="utf-8"))
        except Exception:
            return []
        out_base = graph_source.parent.parent
    if not isinstance(data, dict):
        return []
    try:
        root_res = scan_root.resolve()
    except (OSError, RuntimeError):
        root_res = scan_root
    # <out>/purpory-out/graph.json — relative source_files may be anchored here.
    try:
        out_base = out_base.resolve()
    except (OSError, RuntimeError):
        pass

    def _within_root(p: Path) -> bool:
        try:
            p.relative_to(root_res)
            return True
        except ValueError:
            pass
        try:
            p.resolve().relative_to(root_res)
            return True
        except (ValueError, OSError, RuntimeError):
            return False

    def _in_seen(p: Path) -> bool:
        if str(p) in seen_files:
            return True
        try:
            return str(p.resolve()) in seen_files
        except (OSError, RuntimeError):
            return False

    stale: list[str] = []
    checked: set[str] = set()
    for n in data.get("nodes", []):
        if not isinstance(n, dict):
            continue
        sf = n.get("source_file")
        if not sf or not isinstance(sf, str) or sf in checked:
            continue
        checked.add(sf)
        if "://" in sf:
            continue  # remote/virtual source (e.g. Google Workspace), not a scanned path
        p = Path(sf)
        if p.is_absolute():
            candidates = [p]
        else:
            rel = sf.replace("\\", "/")
            bases = [root_res]
            if out_base != root_res:
                bases.append(out_base)
            candidates = [Path(os.path.normpath(str(base / rel))) for base in bases]
        in_root = [c for c in candidates if _within_root(c)]
        if not in_root:
            continue  # out-of-root under every anchor: never prune
        if any(_in_seen(c) for c in in_root):
            continue  # still part of the scan corpus
        stale.append(sf)
    return stale


def _prune_graph_sources(graph_source: Path | dict, stale_sources: list[str]) -> int:
    """Drop nodes, edges, and hyperedges owned by ``stale_sources``.

    Used by the ``--no-cluster`` incremental early-exit: that path never runs
    ``build_merge``, so an exclusion-only change must prune the existing raw
    graph directly or the newly-excluded file's nodes survive forever (#1909).
    ``stale_sources`` comes from :func:`_stale_graph_sources`, i.e. the
    graph's own ``source_file`` spellings, so exact string matching is enough.
    Dictionary inputs are mutated in memory; path inputs retain legacy
    artifact compatibility.
    """
    if isinstance(graph_source, dict):
        data = graph_source
    else:
        try:
            data = json.loads(graph_source.read_text(encoding="utf-8"))
        except Exception:
            return 0
    if not isinstance(data, dict):
        return 0
    stale = set(stale_sources)
    links_key = "links" if "links" in data else "edges"
    nodes = [n for n in data.get("nodes", []) if isinstance(n, dict)]
    kept_nodes = [n for n in nodes if n.get("source_file") not in stale]
    removed_ids = {n.get("id") for n in nodes if n.get("source_file") in stale}
    n_removed = len(nodes) - len(kept_nodes)
    kept_edges = [
        e
        for e in data.get(links_key, [])
        if isinstance(e, dict)
        and e.get("source_file") not in stale
        and e.get("source") not in removed_ids
        and e.get("target") not in removed_ids
    ]
    kept_hyper = [
        h
        for h in data.get("hyperedges", [])
        if isinstance(h, dict) and h.get("source_file") not in stale
    ]
    if (
        n_removed == 0
        and len(kept_edges) == len(data.get(links_key, []))
        and (len(kept_hyper) == len(data.get("hyperedges", [])))
    ):
        return 0
    data["nodes"] = kept_nodes
    data[links_key] = kept_edges
    if "hyperedges" in data:
        data["hyperedges"] = kept_hyper
    if not isinstance(graph_source, dict):
        from purpory.export import backup_if_protected as _backup

        _backup(graph_source.parent)
        from purpory.paths import write_json_atomic

        write_json_atomic(graph_source, data, indent=2)
    return n_removed


class _StageTimer:
    """Print per-stage wall-clock timings to stderr when --timing is set (#1490).

    Monotonic (perf_counter), diagnostic-only: emits ``[purpory timing] <stage>:
    N.Ns`` after each stage and a final total. Off by default, so normal output is
    byte-identical and machine-read stdout is untouched.
    """

    def __init__(self, enabled: bool) -> None:
        import time as _time

        self._now = _time.perf_counter
        self.enabled = enabled
        self.start = self._now()
        self._last = self.start

    def mark(self, stage: str) -> None:
        now = self._now()
        if self.enabled:
            print(f"[purpory timing] {stage}: {now - self._last:.1f}s", file=sys.stderr)
        self._last = now

    def total(self) -> None:
        if self.enabled:
            print(f"[purpory timing] total: {self._now() - self.start:.1f}s", file=sys.stderr)


def _enforce_graph_size_cap_or_exit(gp: Path) -> None:
    """Reject oversized graph files before parsing (CLI exit-on-fail flavor).

    Delegates to ``purpory.security.check_graph_file_size_cap`` and turns the
    raised ``ValueError`` into a CLI-style ``error: ...`` message + exit 1.
    Use this from ``__main__.py`` subcommands that already use the ``print +
    sys.exit(1)`` idiom. Library/MCP/loader callers (``serve._load_graph``,
    ``build``, ``benchmark``, ``prs``, ``watch``, and
    ``export``) call the security helper directly
    and let the ``ValueError`` propagate.
    """
    from purpory.security import check_graph_file_size_cap

    try:
        check_graph_file_size_cap(gp)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def dispatch_command(cmd: str, arguments: list[str] | tuple[str, ...] = ()) -> None:
    argv = ["purpory", cmd, *arguments]
    if cmd in {"remember", "prepare", "dashboard", "embed"}:
        from purpory.supervise.cli import dispatch_product_command

        dispatch_product_command(cmd, argv[2:])
    elif cmd == "preflight":
        from purpory.supervise.preflight import run_preflight

        if len(argv) != 3:
            print("Usage: purpory preflight [claude|codex]", file=sys.stderr)
            raise SystemExit(2)
        run_preflight(argv[2])
    elif cmd == "session-end":
        from purpory.supervise.session_reconcile import run_session_end

        run_session_end(argv[2:])
    elif cmd == "model":
        from purpory.supervise.model_cli import dispatch_model

        dispatch_model(argv[2:])
    elif cmd == "prs":
        from purpory.prs import cmd_prs

        cmd_prs(argv[2:])
    elif cmd == "import":
        import argparse as _ap

        parser = _ap.ArgumentParser(prog="purpory import")
        parser.add_argument("graph")
        parser.add_argument("--root", default=".")
        options = parser.parse_args(argv[2:])
        from purpory.supervise.identity import resolve_project_id, resolve_project_root
        from purpory.supervise.repository import ContextGraphRepository

        project_root = resolve_project_root(options.root)
        result = ContextGraphRepository().import_graph(
            options.graph,
            project=resolve_project_id(project_root),
        )
        action = "Imported" if result["imported"] else "Already current"
        print(
            f"{action}: {result['nodes']} nodes, {result['edges']} edges, "
            f"{result['hyperedges']} hyperedges."
        )
    elif cmd == "hook":
        from purpory.hooks import (
            install as hook_install,
            uninstall as hook_uninstall,
            status as hook_status,
        )

        subcmd = argv[2] if len(argv) > 2 else ""
        if subcmd == "install":
            print(hook_install(Path(".")))
        elif subcmd == "uninstall":
            print(hook_uninstall(Path(".")))
        elif subcmd == "status":
            print(hook_status(Path(".")))
        else:
            print("Usage: purpory hook [install|uninstall|status]", file=sys.stderr)
            sys.exit(1)
    elif cmd == "query":
        if len(argv) < 3:
            print(
                'Usage: purpory query "<question>" [--dfs] [--context C] [--budget N] [--graph path]',
                file=sys.stderr,
            )
            sys.exit(1)
        from purpory.serve import _query_graph_text
        from purpory import querylog

        question = argv[2]
        use_dfs = "--dfs" in argv
        budget = 2000
        graph_path: str | None = None
        context_filters: list[str] = []
        args = argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--budget" and i + 1 < len(args):
                try:
                    budget = int(args[i + 1])
                except ValueError:
                    print(f"error: --budget must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 2
            elif args[i].startswith("--budget="):
                try:
                    budget = int(args[i].split("=", 1)[1])
                except ValueError:
                    print(f"error: --budget must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 1
            elif args[i] == "--context" and i + 1 < len(args):
                context_filters.append(args[i + 1])
                i += 2
            elif args[i].startswith("--context="):
                context_filters.append(args[i].split("=", 1)[1])
                i += 1
            elif args[i] == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]
                i += 2
            else:
                i += 1
        try:
            _raw, _corpus, _ = _load_graph_data(graph_path)
            G = _networkx_graph(_raw)
            try:
                from purpory.build import graph_has_legacy_ids as _legacy

                if _legacy(_raw.get("nodes", [])):
                    print(
                        "[purpory] note: this graph uses the pre-#1504 node-ID scheme; "
                        "rebuild with `purpory extract --force` to get path-qualified IDs "
                        "(fixes same-name-file collisions).",
                        file=sys.stderr,
                    )
            except Exception:
                pass
        except Exception as exc:
            print(f"error: could not load graph: {exc}", file=sys.stderr)
            sys.exit(1)
        import time as _time

        _t0 = _time.perf_counter()
        _mode = "dfs" if use_dfs else "bfs"
        _result = _query_graph_text(
            G,
            question,
            mode=_mode,
            depth=2,
            token_budget=budget,
            context_filters=context_filters,
        )
        querylog.log_query(
            kind="query",
            question=question,
            corpus=_corpus,
            result=_result,
            mode=_mode,
            depth=2,
            token_budget=budget,
            duration_ms=(_time.perf_counter() - _t0) * 1000,
        )
        print(_result)
    elif cmd == "affected":
        if len(argv) < 3:
            print(
                'Usage: purpory affected "<node-or-label>" [--relation R] [--depth N] [--graph path]',
                file=sys.stderr,
            )
            sys.exit(1)
        from purpory.affected import DEFAULT_AFFECTED_RELATIONS, format_affected

        query = argv[2]
        graph_path: str | None = None
        depth = 2
        relations: list[str] = []
        args = argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]
                i += 2
            elif args[i].startswith("--graph="):
                graph_path = args[i].split("=", 1)[1]
                i += 1
            elif args[i] == "--depth" and i + 1 < len(args):
                try:
                    depth = int(args[i + 1])
                except ValueError:
                    print("error: --depth must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 2
            elif args[i].startswith("--depth="):
                try:
                    depth = int(args[i].split("=", 1)[1])
                except ValueError:
                    print("error: --depth must be an integer", file=sys.stderr)
                    sys.exit(1)
                i += 1
            elif args[i] == "--relation" and i + 1 < len(args):
                relations.append(args[i + 1])
                i += 2
            elif args[i].startswith("--relation="):
                relations.append(args[i].split("=", 1)[1])
                i += 1
            else:
                i += 1
        try:
            raw, _, _ = _load_graph_data(graph_path)
            graph = _networkx_graph(raw, directed=True)
        except Exception as exc:
            print(f"error: could not load graph: {exc}", file=sys.stderr)
            sys.exit(1)
        print(
            format_affected(
                graph,
                query,
                relations=relations or DEFAULT_AFFECTED_RELATIONS,
                depth=depth,
            )
        )
    elif cmd == "path":
        if len(argv) < 4:
            print(
                'Usage: purpory path "<source>" "<target>" [--graph path]',
                file=sys.stderr,
            )
            sys.exit(1)
        from purpory.serve import _pick_scored_endpoint, _score_nodes
        import networkx as _nx

        source_label = argv[2]
        target_label = argv[3]
        graph_path: str | None = None
        args = argv[4:]
        for i, a in enumerate(args):
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]
        try:
            _raw, _corpus, _ = _load_graph_data(graph_path)
            G = _networkx_graph(_raw, directed=True)
        except Exception as exc:
            print(f"error: could not load graph: {exc}", file=sys.stderr)
            sys.exit(1)
        src_scored = _score_nodes(G, [t.lower() for t in source_label.split()])
        tgt_scored = _score_nodes(G, [t.lower() for t in target_label.split()])
        if not src_scored:
            print(f"No node matching '{source_label}' found.", file=sys.stderr)
            sys.exit(1)
        if not tgt_scored:
            print(f"No node matching '{target_label}' found.", file=sys.stderr)
            sys.exit(1)
        src_nid = _pick_scored_endpoint(G, src_scored, source_label)
        tgt_nid = _pick_scored_endpoint(G, tgt_scored, target_label)
        # Ambiguity guard: when both queries resolve to the same node, the
        # shortest path is trivially zero hops, which is almost never what the
        # caller wanted (see bug #828).
        if src_nid == tgt_nid:
            print(
                f"'{source_label}' and '{target_label}' both resolved to the same "
                f"node '{src_nid}'. Use a more specific label or the exact node ID.",
                file=sys.stderr,
            )
            sys.exit(1)
        for _name, _scored, _nid in (
            ("source", src_scored, src_nid),
            ("target", tgt_scored, tgt_nid),
        ):
            # A close runner-up only made the resolution ambiguous when the raw
            # score head is what got picked; a full-token override was chosen on
            # token coverage, not score, so the head's margin is irrelevant.
            if len(_scored) >= 2 and _nid == _scored[0][1]:
                _top, _runner = _scored[0][0], _scored[1][0]
                if _top > 0 and (_top - _runner) / _top < 0.10:
                    print(
                        f"warning: {_name} match was ambiguous "
                        f"(top score {_top:g}, runner-up {_runner:g})",
                        file=sys.stderr,
                    )
        try:
            path_nodes = _nx.shortest_path(G.to_undirected(as_view=True), src_nid, tgt_nid)
        except (_nx.NetworkXNoPath, _nx.NodeNotFound):
            print(f"No path found between '{source_label}' and '{target_label}'.")
            sys.exit(0)
        hops = len(path_nodes) - 1
        segments = []
        from purpory.build import edge_data

        for i in range(len(path_nodes) - 1):
            u, v = path_nodes[i], path_nodes[i + 1]
            # Check which direction the stored edge points.
            if G.has_edge(u, v):
                edata = edge_data(G, u, v)
                forward = True
            else:
                edata = edge_data(G, v, u)
                forward = False
            rel = edata.get("relation", "")
            conf = edata.get("confidence", "")
            conf_str = f" [{conf}]" if conf else ""
            if i == 0:
                segments.append(G.nodes[u].get("label", u))
            if forward:
                segments.append(f"--{rel}{conf_str}--> {G.nodes[v].get('label', v)}")
            else:
                segments.append(f"<--{rel}{conf_str}-- {G.nodes[v].get('label', v)}")
        print(f"Shortest path ({hops} hops):\n  " + " ".join(segments))
        from purpory import querylog

        querylog.log_query(
            kind="path",
            question=f"{argv[2]} -> {argv[3]}",
            corpus=_corpus,
            nodes_returned=hops,
        )

    elif cmd == "explain":
        if len(argv) < 3:
            print('Usage: purpory explain "<node>" [--graph path]', file=sys.stderr)
            sys.exit(1)
        from purpory.serve import _find_node

        label = argv[2]
        graph_path: str | None = None
        args = argv[3:]
        for i, a in enumerate(args):
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]
        try:
            _raw, _corpus, _graph_file = _load_graph_data(graph_path)
            G = _networkx_graph(_raw, directed=True)
        except Exception as exc:
            print(f"error: could not load graph: {exc}", file=sys.stderr)
            sys.exit(1)
        matches = _find_node(G, label)
        if not matches:
            print(f"No node matching '{label}' found.")
            sys.exit(0)
        nid = matches[0]
        d = G.nodes[nid]
        print(f"Node: {d.get('label', nid)}")
        print(f"  ID:        {nid}")
        print(f"  Source:    {d.get('source_file', '')} {d.get('source_location', '')}".rstrip())
        print(f"  Type:      {d.get('file_type', '')}")
        print(f"  Community: {d.get('community_name') or d.get('community', '')}")
        print(f"  Degree:    {G.degree(nid)}")
        from purpory.build import edge_data

        connections: list[tuple[str, str, dict]] = []  # (direction, neighbor_id, edge_data)
        for nb in G.successors(nid):
            connections.append(("out", nb, edge_data(G, nid, nb)))
        for nb in G.predecessors(nid):
            connections.append(("in", nb, edge_data(G, nb, nid)))
        if connections:
            print(f"\nConnections ({len(connections)}):")
            connections.sort(key=lambda c: G.degree(c[1]), reverse=True)
            for direction, nb, edata in connections[:20]:
                rel = edata.get("relation", "")
                conf = edata.get("confidence", "")
                arrow = "-->" if direction == "out" else "<--"
                print(f"  {arrow} {G.nodes[nb].get('label', nb)} [{rel}] [{conf}]")
            if len(connections) > 20:
                print(f"  ... and {len(connections) - 20} more")
        from purpory import querylog

        querylog.log_query(
            kind="explain",
            question=argv[2],
            corpus=_corpus,
            nodes_returned=len(connections),
        )

    elif cmd == "add":
        if len(argv) < 3:
            print(
                "Usage: purpory add <url> [--author Name] [--contributor Name] [--dir ./raw]",
                file=sys.stderr,
            )
            sys.exit(1)
        from purpory.ingest import ingest as _ingest

        url = argv[2]
        author: str | None = None
        contributor: str | None = None
        target_dir = Path("raw")
        args = argv[3:]
        i = 0
        while i < len(args):
            if args[i] == "--author" and i + 1 < len(args):
                author = args[i + 1]
                i += 2
            elif args[i] == "--contributor" and i + 1 < len(args):
                contributor = args[i + 1]
                i += 2
            elif args[i] == "--dir" and i + 1 < len(args):
                target_dir = Path(args[i + 1])
                i += 2
            else:
                i += 1
        try:
            saved = _ingest(url, target_dir, author=author, contributor=contributor)
            print(f"Saved to {saved}")
            print("Run /purpory --update in your AI assistant to update the graph.")
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    elif cmd in ("cluster-only", "label"):
        # `label` is `cluster-only` that always (re)generates community names with
        # the configured backend, even when a .purpory_labels.json already exists.
        force_relabel = cmd == "label"
        # Mirror the tree/export arg-parsing pattern: walk argv so flags and
        # the optional positional path can appear in any order (#724).
        no_label = "--no-label" in argv
        missing_only = "--missing-only" in argv
        co_timing = "--timing" in argv
        _backend_arg = next((a for a in argv if a.startswith("--backend=")), None)
        label_backend = _backend_arg.split("=", 1)[1] if _backend_arg else None
        _model_arg = next((a for a in argv if a.startswith("--model=")), None)
        label_model = _model_arg.split("=", 1)[1] if _model_arg else None
        args = argv[2:]
        watch_path: Path | None = None
        graph_override: Path | None = None
        co_resolution: float = 1.0
        co_exclude_hubs: float | None = None
        label_max_concurrency: int = 4
        label_batch_size: int = 100
        i_arg = 0
        while i_arg < len(args):
            a = args[i_arg]
            if a == "--graph" and i_arg + 1 < len(args):
                graph_override = Path(args[i_arg + 1])
                i_arg += 2
            elif a == "--backend" and i_arg + 1 < len(args):
                label_backend = args[i_arg + 1]
                i_arg += 2
            elif a.startswith("--backend="):
                label_backend = a.split("=", 1)[1]
                i_arg += 1
            elif a == "--model" and i_arg + 1 < len(args):
                label_model = args[i_arg + 1]
                i_arg += 2
            elif a.startswith("--model="):
                label_model = a.split("=", 1)[1]
                i_arg += 1
            elif a == "--resolution" and i_arg + 1 < len(args):
                co_resolution = float(args[i_arg + 1])
                i_arg += 2
            elif a.startswith("--resolution="):
                co_resolution = float(a.split("=", 1)[1])
                i_arg += 1
            elif a == "--exclude-hubs" and i_arg + 1 < len(args):
                co_exclude_hubs = float(args[i_arg + 1])
                i_arg += 2
            elif a.startswith("--exclude-hubs="):
                co_exclude_hubs = float(a.split("=", 1)[1])
                i_arg += 1
            elif a == "--max-concurrency" and i_arg + 1 < len(args):
                label_max_concurrency = int(args[i_arg + 1])
                i_arg += 2
            elif a.startswith("--max-concurrency="):
                label_max_concurrency = int(a.split("=", 1)[1])
                i_arg += 1
            elif a == "--batch-size" and i_arg + 1 < len(args):
                label_batch_size = int(args[i_arg + 1])
                i_arg += 2
            elif a.startswith("--batch-size="):
                label_batch_size = int(a.split("=", 1)[1])
                i_arg += 1
            elif a in ("--no-viz", "--missing-only") or a.startswith("--min-community-size="):
                i_arg += 1
            elif a.startswith("--"):
                i_arg += 1
            elif watch_path is None:
                watch_path = Path(a)
                i_arg += 1
            else:
                i_arg += 1
        if watch_path is None:
            watch_path = Path(".")
        from purpory.build import build_from_json
        from purpory.cluster import cluster, score_all, remap_communities_to_previous
        from purpory.analyze import (
            god_nodes,
            surprising_connections,
            suggest_questions,
        )
        stages = _StageTimer(co_timing)
        print("Loading existing graph...")
        try:
            _raw, _, _ = _load_graph_data(graph_override, root=watch_path)
        except Exception as exc:
            print(f"error: could not load graph: {exc}", file=sys.stderr)
            sys.exit(1)
        _directed = bool(_raw.get("directed", False))
        G = build_from_json(_raw, directed=_directed)
        print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        stages.mark("load")
        print("Re-clustering...")
        communities = cluster(G, resolution=co_resolution, exclude_hubs_percentile=co_exclude_hubs)
        # Mirror the watch/update path (#822): map new cids to prior ones by
        # node-overlap so the existing .purpory_labels.json keeps attaching
        # to the same conceptual community after re-clustering. Without this,
        # labels follow raw cid index and become misaligned whenever the
        # graph has changed between labeling and cluster-only (#1027).
        previous_node_community = {
            n["id"]: n["community"]
            for n in _raw.get("nodes", [])
            if n.get("community") is not None and n.get("id") is not None
        }
        if previous_node_community:
            communities = remap_communities_to_previous(communities, previous_node_community)
        stages.mark("cluster")
        cohesion = score_all(G, communities)
        gods = god_nodes(G)
        surprises = surprising_connections(G, communities)
        stages.mark("analyze")
        existing_labels = {
            int(node["community"]): str(node["community_name"])
            for node in _raw.get("nodes", [])
            if node.get("community") is not None and node.get("community_name")
        }
        # Accumulate token usage from the labeling LLM calls so cluster-only mode
        # reports real cost instead of a hardcoded zero (#1694). Stays {0, 0} on
        # the reuse / no-label paths, which make no LLM calls.
        label_token_usage = {"input": 0, "output": 0}
        if existing_labels and not force_relabel:
            # Reuse saved labels, but don't blindly trust them: the graph may have
            # been re-scoped/re-clustered since labeling, in which case a cid now
            # covers a DIFFERENT community and its old (LLM) name is wrong (#label-stale).
            # Validate each community against the membership signature saved beside the
            # labels; any community that changed (or has no saved label) is renamed by
            # its current hub — deterministic and correct-by-construction — and the user
            # is told to `purpory label` for fresh LLM names. Unchanged communities keep
            # their saved label. When no signature sidecar exists (labels predate this),
            # fall back to hub-filling only the communities missing a label.
            from purpory.cluster import community_member_sigs, label_communities_by_hub

            stored_analysis = _raw.get("analysis", {})
            raw_sigs = (
                stored_analysis.get("communitySignatures", {})
                if isinstance(stored_analysis, dict)
                else {}
            )
            saved_sigs = {
                int(k): v for k, v in raw_sigs.items() if isinstance(v, str)
            }
            cur_sigs = community_member_sigs(communities)
            count_mismatch = len(existing_labels) != len(communities)
            labels = {}
            hub_labels: dict[int, str] | None = None
            changed = 0
            for cid in communities:
                have_label = cid in existing_labels
                if saved_sigs:
                    # Precise: the membership signature tells us if this exact
                    # community changed since it was labeled.
                    fresh = have_label and saved_sigs.get(cid) == cur_sigs.get(cid)
                else:
                    # No signature sidecar (labels predate it). A differing community
                    # COUNT means the labels describe a different clustering, so a cid's
                    # old label can't be trusted; equal count is the best "same" signal.
                    fresh = have_label and not count_mismatch
                if fresh:
                    labels[cid] = existing_labels[cid]
                else:
                    if hub_labels is None:
                        hub_labels = label_communities_by_hub(G, communities)
                    labels[cid] = hub_labels[cid]
                    if have_label:
                        changed += 1
            if changed:
                print(
                    f"[purpory] community set changed since labeling "
                    f"({len(existing_labels)} saved labels, {len(communities)} communities now; "
                    f"renamed {changed} community(ies) by their hub). "
                    f"Run `purpory label` to refresh names with the LLM.",
                    file=sys.stderr,
                )
        elif no_label and not force_relabel:
            labels = {cid: f"Community {cid}" for cid in communities}
        else:
            # No labels file yet (or `purpory label` forced a refresh), so
            # auto-name communities rather than leave "Community N" (#1097).
            from purpory.cluster import label_communities_by_hub
            from purpory.llm import generate_community_labels

            print("Labeling communities...")
            # Deterministic, LLM-free base labels: name each community after its
            # highest-degree hub, so the report is readable even with no backend
            # (previously bare "Community N"). A configured LLM backend overrides these
            # with richer names below; its no-backend placeholder fallback does NOT.
            hub_labels = label_communities_by_hub(G, communities)
            label_communities_input = communities
            labels = dict(hub_labels)
            if missing_only:
                labels = {cid: existing_labels.get(cid, hub_labels[cid]) for cid in communities}
                label_communities_input = {
                    cid: members
                    for cid, members in communities.items()
                    if cid not in existing_labels or existing_labels.get(cid) == f"Community {cid}"
                }
            generated_labels, _ = generate_community_labels(
                G,
                label_communities_input,
                backend=label_backend,
                model=label_model,
                gods=gods,
                max_concurrency=label_max_concurrency,
                batch_size=label_batch_size,
                usage_out=label_token_usage,
            )
            # Only let the LLM OVERRIDE where it produced a real name — its no-backend
            # fallback returns "Community {cid}" placeholders, which must not clobber
            # the deterministic hub labels.
            labels.update(
                {cid: v for cid, v in generated_labels.items() if v and v != f"Community {cid}"}
            )
        stages.mark("label")
        questions = suggest_questions(G, communities, labels)
        from purpory.cluster import community_member_sigs

        analysis = {
            "communities": {str(k): v for k, v in communities.items()},
            "cohesion": {str(k): v for k, v in cohesion.items()},
            "gods": gods,
            "surprises": surprises,
            "questions": questions,
            "tokens": label_token_usage,
            "communitySignatures": {
                str(k): v for k, v in community_member_sigs(communities).items()
            },
        }
        from purpory.export import graph_data as _graph_data
        from purpory.supervise.structural import store_structural_graph

        store_structural_graph(
            {
                **_graph_data(G, communities, community_labels=labels),
                "analysis": analysis,
            },
            root=watch_path,
        )
        stages.mark("store")
        stages.total()
        print(f"Done - {len(communities)} communities stored in SQLite.")

    elif cmd == "update":
        force = os.environ.get("PURPORY_FORCE", "").lower() in ("1", "true", "yes")
        no_cluster = False
        args = argv[2:]
        watch_arg: str | None = None
        for a in args:
            if a == "--force":
                force = True
                continue
            if a == "--no-cluster":
                no_cluster = True
                continue
            if a.startswith("-"):
                print(f"error: unknown update option: {a}", file=sys.stderr)
                sys.exit(2)
            if watch_arg is not None:
                print("error: update accepts at most one path argument", file=sys.stderr)
                sys.exit(2)
            watch_arg = a

        if watch_arg is not None:
            watch_path = Path(watch_arg)
        else:
            watch_path = Path(".")
        if not watch_path.exists():
            print(f"error: path not found: {watch_path}", file=sys.stderr)
            sys.exit(1)
        from purpory.watch import _rebuild_code

        print(f"Re-extracting code files in {watch_path} (no LLM needed)...")
        # Interactive CLI: block on the per-repo lock rather than skip, so the
        # user sees their explicit `purpory update` complete instead of
        # exiting silently when a hook-driven rebuild happens to be running.
        ok = _rebuild_code(watch_path, force=force, no_cluster=no_cluster, block_on_lock=True)
        if ok:
            print(
                "Code graph updated. For doc/paper/image changes run /purpory --update in your AI assistant."
            )
            if not (
                os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
                or os.environ.get("MOONSHOT_API_KEY")
                or os.environ.get("DEEPSEEK_API_KEY")
                or os.environ.get("PURPORY_NO_TIPS")
            ):
                print(
                    "Tip: set GEMINI_API_KEY or GOOGLE_API_KEY to use Gemini for semantic extraction."
                )
        else:
            print(
                "Nothing to update or rebuild failed — check output above.",
                file=sys.stderr,
            )
            sys.exit(1)

    elif cmd == "check-update":
        if len(argv) < 3:
            print("Usage: purpory check-update <path>", file=sys.stderr)
            sys.exit(1)
        from purpory.watch import check_update

        check_update(Path(argv[2]).resolve())
        sys.exit(0)
    elif cmd == "export":
        import argparse

        parser = argparse.ArgumentParser(prog="purpory export")
        formats = parser.add_subparsers(dest="format", required=True)
        for name in ("json", "report", "wiki"):
            sub = formats.add_parser(name)
            sub.add_argument("--graph")
            sub.add_argument("--root", default=".")
            sub.add_argument("--output")
        for name in ("neo4j", "falkordb"):
            sub = formats.add_parser(name)
            sub.add_argument("--graph")
            sub.add_argument("--root", default=".")
            sub.add_argument("--push", required=True, metavar="URI")
            sub.add_argument("--user", default="neo4j" if name == "neo4j" else None)
            sub.add_argument(
                "--password",
                help=f"defaults to {name.upper()}_PASSWORD",
            )
            if name == "falkordb":
                sub.add_argument("--graph-name", default="purpory")
        options = parser.parse_args(argv[2:])
        raw, _, explicit_graph = _load_graph_data(options.graph, root=options.root)

        if options.format == "json":
            from purpory.paths import write_json_atomic

            output = Path(options.output or "graph.json").expanduser()
            write_json_atomic(output, raw, indent=2)
            print(f"graph JSON written: {output}")
            return

        graph = _networkx_graph(raw)
        communities: dict[int, list[str]] = {}
        analysis = raw.get("analysis")
        sidecar_dir = explicit_graph.parent if explicit_graph else Path(_PURPORY_OUT)
        analysis_path = sidecar_dir / ".purpory_analysis.json"
        if not isinstance(analysis, dict) and analysis_path.exists():
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        if isinstance(analysis, dict):
            communities = {
                int(key): value for key, value in analysis.get("communities", {}).items()
            }
        if not communities:
            for node_id, data in graph.nodes(data=True):
                community = data.get("community")
                if community is not None:
                    communities.setdefault(int(community), []).append(str(node_id))
        labels = {
            int(data["community"]): data["community_name"]
            for _, data in graph.nodes(data=True)
            if data.get("community") is not None and data.get("community_name")
        }

        if options.format == "neo4j":
            from purpory.exporters.graphdb import push_to_neo4j

            password = options.password or os.environ.get("NEO4J_PASSWORD")
            if not password:
                parser.error("neo4j push requires --password or NEO4J_PASSWORD")
            result = push_to_neo4j(
                graph,
                uri=options.push,
                user=options.user,
                password=password,
                communities=communities,
            )
            print(f"Pushed to Neo4j: {result['nodes']} nodes, {result['edges']} edges")
            return

        if options.format == "falkordb":
            from purpory.exporters.graphdb import push_to_falkordb

            result = push_to_falkordb(
                graph,
                uri=options.push,
                user=options.user,
                password=options.password or os.environ.get("FALKORDB_PASSWORD"),
                communities=communities,
                graph_name=options.graph_name,
            )
            print(f"Pushed to FalkorDB: {result['nodes']} nodes, {result['edges']} edges")
            return

        if options.format == "report":
            from purpory.report import generate

            output = Path(options.output or "GRAPH_REPORT.md").expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            analysis = analysis if isinstance(analysis, dict) else {}
            output.write_text(
                generate(
                    graph,
                    communities,
                    {int(key): value for key, value in analysis.get("cohesion", {}).items()},
                    labels,
                    analysis.get("gods", []),
                    analysis.get("surprises", []),
                    {"warning": "exported from canonical SQLite graph"},
                    analysis.get("tokens", {"input": 0, "output": 0}),
                    str(Path(options.root).resolve()),
                    suggested_questions=analysis.get("questions", []),
                    built_at_commit=raw.get("built_at_commit"),
                ),
                encoding="utf-8",
            )
            print(f"graph report written: {output}")
            return

        if not communities:
            parser.error("wiki export requires clustered community data")
        from purpory.analyze import god_nodes
        from purpory.wiki import to_wiki

        output = Path(options.output or sidecar_dir / "wiki").expanduser()
        analysis = analysis if isinstance(analysis, dict) else {}
        count = to_wiki(
            graph,
            communities,
            str(output),
            community_labels=labels or None,
            cohesion={
                int(key): value for key, value in analysis.get("cohesion", {}).items()
            } or None,
            god_nodes_data=analysis.get("gods") or god_nodes(graph),
        )
        print(f"Wiki: {count} articles written to {output}")
        print(f"  {output / 'index.md'}  ->  agent entry point")

    elif cmd == "benchmark":
        from purpory.benchmark import run_benchmark, print_benchmark

        graph_path = argv[2] if len(argv) > 2 else None
        graph_data = None
        if graph_path is not None:
            _enforce_graph_size_cap_or_exit(Path(graph_path))
        else:
            try:
                graph_data, _, _ = _load_graph_data()
            except Exception as exc:
                print(f"error: could not load graph: {exc}", file=sys.stderr)
                sys.exit(1)
        # Try to load corpus_words from detect output
        corpus_words = None
        detect_path = Path(".purpory_detect.json")
        if detect_path.exists():
            try:
                detect_data = json.loads(detect_path.read_text(encoding="utf-8"))
                corpus_words = detect_data.get("total_words")
            except Exception:
                pass
        result = run_benchmark(
            graph_path,
            corpus_words=corpus_words,
            graph_data=graph_data,
        )
        print_benchmark(result)

    elif cmd == "extract":
        # Headless full-pipeline extraction for CI / scripts (#698).
        # Runs detect -> AST extraction on code -> semantic LLM extraction on
        # docs/papers/images -> merge -> build -> cluster -> write outputs.
        # Calls extract_corpus_parallel directly using whichever backend has an
        # API key set.
        if len(argv) < 3:
            print(
                "Usage: purpory extract <path> [--backend gemini|kimi|claude|openai|deepseek|ollama] "
                "[--model M] [--mode deep] [--out DIR] [--google-workspace] [--no-cluster] "
                "[--max-workers N] [--token-budget N] [--max-concurrency N] "
                "[--api-timeout S] [--postgres DSN] [--cargo] [--allow-partial] [--timing]",
                file=sys.stderr,
            )
            sys.exit(1)

        has_path = True
        if argv[2].startswith("-"):
            has_path = False
            target = Path(".").resolve()
        else:
            target = Path(argv[2]).resolve()
            if not target.exists():
                print(f"error: path not found: {target}", file=sys.stderr)
                sys.exit(1)

        backend: str | None = None
        model: str | None = None
        extract_mode: str | None = None
        out_dir: Path | None = None
        cli_postgres_dsn: str | None = None
        cli_cargo: bool = False
        cli_allow_partial: bool = False
        no_cluster = False
        dedup_llm = False
        google_workspace = False
        code_only = False
        # Performance/tuning knobs (issue #792). None means "use library default".
        cli_max_workers: int | None = None
        cli_token_budget: int | None = None
        cli_max_concurrency: int | None = None
        cli_api_timeout: float | None = None
        # Clustering tuning knobs
        cli_resolution: float = 1.0
        cli_exclude_hubs: float | None = None
        cli_excludes: list[str] = []
        cli_timing: bool = False
        # --force parity with `purpory update`: the flag or PURPORY_FORCE=1
        # disables the incremental gate and skips semantic-cache reads (#1894).
        force = os.environ.get("PURPORY_FORCE", "").lower() in ("1", "true", "yes")

        def _parse_int(name: str, raw: str) -> int:
            try:
                v = int(raw)
            except ValueError:
                print(f"error: {name} must be a positive integer (got {raw!r})", file=sys.stderr)
                sys.exit(2)
            if v <= 0:
                print(f"error: {name} must be > 0 (got {v})", file=sys.stderr)
                sys.exit(2)
            return v

        def _parse_float(name: str, raw: str) -> float:
            try:
                v = float(raw)
            except ValueError:
                print(f"error: {name} must be a positive number (got {raw!r})", file=sys.stderr)
                sys.exit(2)
            if v <= 0:
                print(f"error: {name} must be > 0 (got {v})", file=sys.stderr)
                sys.exit(2)
            return v

        args = argv[3:] if has_path else argv[2:]
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--backend" and i + 1 < len(args):
                backend = args[i + 1]
                i += 2
            elif a.startswith("--backend="):
                backend = a.split("=", 1)[1]
                i += 1
            elif a == "--model" and i + 1 < len(args):
                model = args[i + 1]
                i += 2
            elif a.startswith("--model="):
                model = a.split("=", 1)[1]
                i += 1
            elif a == "--mode" and i + 1 < len(args):
                extract_mode = args[i + 1]
                i += 2
            elif a.startswith("--mode="):
                extract_mode = a.split("=", 1)[1]
                i += 1
            elif a == "--out" and i + 1 < len(args):
                out_dir = Path(args[i + 1])
                i += 2
            elif a.startswith("--out="):
                out_dir = Path(a.split("=", 1)[1])
                i += 1
            elif a == "--no-cluster":
                no_cluster = True
                i += 1
            elif a == "--dedup-llm":
                dedup_llm = True
                i += 1
            elif a == "--code-only":
                code_only = True
                i += 1
            elif a == "--google-workspace":
                google_workspace = True
                i += 1
            elif a == "--max-workers" and i + 1 < len(args):
                cli_max_workers = _parse_int("--max-workers", args[i + 1])
                i += 2
            elif a.startswith("--max-workers="):
                cli_max_workers = _parse_int("--max-workers", a.split("=", 1)[1])
                i += 1
            elif a == "--token-budget" and i + 1 < len(args):
                cli_token_budget = _parse_int("--token-budget", args[i + 1])
                i += 2
            elif a.startswith("--token-budget="):
                cli_token_budget = _parse_int("--token-budget", a.split("=", 1)[1])
                i += 1
            elif a == "--max-concurrency" and i + 1 < len(args):
                cli_max_concurrency = _parse_int("--max-concurrency", args[i + 1])
                i += 2
            elif a.startswith("--max-concurrency="):
                cli_max_concurrency = _parse_int("--max-concurrency", a.split("=", 1)[1])
                i += 1
            elif a == "--api-timeout" and i + 1 < len(args):
                cli_api_timeout = _parse_float("--api-timeout", args[i + 1])
                i += 2
            elif a.startswith("--api-timeout="):
                cli_api_timeout = _parse_float("--api-timeout", a.split("=", 1)[1])
                i += 1
            elif a == "--resolution" and i + 1 < len(args):
                cli_resolution = _parse_float("--resolution", args[i + 1])
                i += 2
            elif a.startswith("--resolution="):
                cli_resolution = _parse_float("--resolution", a.split("=", 1)[1])
                i += 1
            elif a == "--exclude-hubs" and i + 1 < len(args):
                cli_exclude_hubs = float(args[i + 1])
                i += 2
            elif a.startswith("--exclude-hubs="):
                cli_exclude_hubs = float(a.split("=", 1)[1])
                i += 1
            elif a == "--exclude" and i + 1 < len(args):
                cli_excludes.append(args[i + 1])
                i += 2
            elif a.startswith("--exclude="):
                cli_excludes.append(a.split("=", 1)[1])
                i += 1
            elif a == "--postgres" and i + 1 < len(args):
                cli_postgres_dsn = args[i + 1]
                i += 2
            elif a.startswith("--postgres="):
                cli_postgres_dsn = a.split("=", 1)[1]
                i += 1
            elif a == "--cargo":
                cli_cargo = True
                i += 1
            elif a == "--force":
                force = True
                i += 1
            elif a == "--allow-partial":
                cli_allow_partial = True
                i += 1
            elif a == "--timing":
                cli_timing = True
                i += 1
            else:
                i += 1

        if not has_path and cli_postgres_dsn is None:
            print("error: must specify a path to scan or a --postgres DSN", file=sys.stderr)
            sys.exit(1)

        _VALID_MODES = {"deep"}
        if extract_mode is not None and extract_mode not in _VALID_MODES:
            print(
                f"error: unknown --mode '{extract_mode}'. "
                f"Available: {', '.join(sorted(_VALID_MODES))}",
                file=sys.stderr,
            )
            sys.exit(2)
        deep_mode = extract_mode == "deep"
        if deep_mode:
            print("[purpory extract] deep mode enabled: richer semantic extraction")

        # CLI flag wins over the provider's environment default.
        if cli_api_timeout is not None:
            os.environ["PURPORY_API_TIMEOUT"] = str(cli_api_timeout)
        if cli_max_workers is not None:
            os.environ["PURPORY_MAX_WORKERS"] = str(cli_max_workers)

        from purpory.supervise.structural import (
            load_structural_graph,
            project_state_directory,
        )

        out_root = out_dir.resolve() if out_dir else project_state_directory(target)
        purpory_out = out_root / _PURPORY_OUT
        purpory_out.mkdir(parents=True, exist_ok=True)
        # Persist --exclude so later update/watch/hook rebuilds re-apply it
        # instead of silently re-including the excluded paths (#1886).
        from purpory.watch import _write_build_config as _write_build_cfg

        _write_build_cfg(purpory_out, excludes=cli_excludes or None)

        stages = _StageTimer(cli_timing)

        from purpory.detect import (
            detect as _detect,
            detect_incremental as _detect_incremental,
            save_manifest as _save_manifest,
        )

        manifest_path = purpory_out / "manifest.json"
        existing_graph_data = load_structural_graph(target)
        # #1925: a missing manifest.json must not degrade to a full scan that
        # discards the existing graph's semantic layer. An existing graph.json
        # is a sufficient incremental baseline: detect_incremental treats an
        # absent manifest as "everything is new" (re-extract all, nothing
        # deleted), and build_merge + _stale_graph_sources reconcile replaced
        # and genuinely-deleted sources against the current corpus, so doc/
        # paper/image nodes survive a --code-only rebuild instead of being
        # dropped with the rest of the committed graph.
        incremental_mode = existing_graph_data is not None if has_path else False
        # --force: full scan, not the manifest-gated incremental diff — a warm
        # unchanged tree would otherwise dispatch zero files (#1894).
        incremental_mode = incremental_mode and not force
        if force:
            print("[purpory extract] --force: full re-scan, semantic cache reads skipped")
        elif incremental_mode and not manifest_path.exists():
            print(
                "[purpory extract] manifest.json missing; using existing "
                "SQLite graph as the incremental baseline (all files re-checked; "
                "nodes for files outside this run's scope are preserved)"
            )

        if not has_path:
            code_files = []
            doc_files = []
            paper_files = []
            image_files = []
            deleted_files = []
            excluded_files = []
            graph_stale_sources = []
            unchanged_total = 0
            files_by_type = {}
        elif incremental_mode:
            print(f"[purpory extract] incremental scan of {target}")
            detection = _detect_incremental(
                target,
                manifest_path=str(manifest_path),
                google_workspace=google_workspace or None,
                extra_excludes=cli_excludes or None,
            )
            files_by_type = detection.get("files", {})
            new_by_type = detection.get("new_files", {})
            code_files = [Path(p) for p in new_by_type.get("code", [])]
            doc_files = [Path(p) for p in new_by_type.get("document", [])]
            paper_files = [Path(p) for p in new_by_type.get("paper", [])]
            image_files = [Path(p) for p in new_by_type.get("image", [])]
            deleted_files = list(detection.get("deleted_files", []))
            excluded_files = list(detection.get("excluded_files", []))
            unchanged_total = sum(len(v) for v in detection.get("unchanged_files", {}).values())
            # #1909: derive the prune set from the existing graph itself, not
            # just the manifest. A file that became excluded without ever
            # being manifest-listed (every pre-#1897 graph is in this state)
            # still has stale nodes carried forward by build_merge unless the
            # graph's own sources are reconciled against the current corpus.
            _seen_files = {f for _fl in files_by_type.values() for f in _fl}
            _seen_files.update(detection.get("unclassified", []))
            graph_stale_sources = _stale_graph_sources(
                existing_graph_data or {}, target, _seen_files
            )
        else:
            print(f"[purpory extract] scanning {target}")
            detection = _detect(
                target,
                google_workspace=google_workspace or None,
                extra_excludes=cli_excludes or None,
                cache_root=out_root,
            )
            files_by_type = detection.get("files", {})
            code_files = [Path(p) for p in files_by_type.get("code", [])]
            doc_files = [Path(p) for p in files_by_type.get("document", [])]
            paper_files = [Path(p) for p in files_by_type.get("paper", [])]
            image_files = [Path(p) for p in files_by_type.get("image", [])]
            deleted_files = []
            excluded_files = []
            graph_stale_sources = []
            unchanged_total = 0

        semantic_files = doc_files + paper_files + image_files
        # --code-only: index code (pure local AST, no key) and skip the semantic
        # (doc/paper/image) pass entirely, so a mixed repo doesn't hard-fail when no
        # LLM backend is configured (#1734). Report what was skipped rather than
        # silently dropping it.
        if code_only and semantic_files:
            print(
                f"[purpory extract] --code-only: skipping {len(semantic_files)} "
                f"non-code file(s) ({len(doc_files)} docs, {len(paper_files)} papers, "
                f"{len(image_files)} images) — no LLM extraction"
            )
            semantic_files = []
            doc_files = []
            paper_files = []
            image_files = []
        if deep_mode and incremental_mode and not code_only:
            # Deep mode reads/writes its own cache namespace
            # (cache/semantic-deep/), so the manifest's changed-file gate is
            # not a valid proxy for deep coverage: over a warm unchanged tree
            # it dispatches zero files and `--mode deep` silently no-ops
            # (#1894). Widen the semantic pass to the FULL live
            # doc/paper/image set (``files_by_type`` from detect_incremental,
            # which already excludes excluded files) and let the
            # mode-namespaced cache decide hits/misses — the first deep run
            # re-dispatches everything (deep namespace cold), later deep runs
            # hit the deep cache.
            _deep_all = [
                Path(p)
                for _ftype in ("document", "paper", "image")
                for p in files_by_type.get(_ftype, [])
            ]
            if len(_deep_all) != len(semantic_files):
                print(
                    f"[purpory extract] deep mode: widening semantic pass from "
                    f"{len(semantic_files)} changed to {len(_deep_all)} live "
                    f"doc/paper/image file(s); the deep semantic cache decides "
                    f"what is re-extracted"
                )
            semantic_files = _deep_all
        if incremental_mode:
            # Excluded-but-alive files are reported separately from deletions
            # (#1908): they still exist on disk, the scan just stopped
            # covering them (ignore rules / --exclude changed).
            _excl_note = f"; {len(excluded_files)} excluded" if excluded_files else ""
            print(
                f"[purpory extract] {len(code_files)} code, {len(doc_files)} docs, "
                f"{len(paper_files)} papers, {len(image_files)} images changed; "
                f"{unchanged_total} unchanged; {len(deleted_files)} deleted"
                f"{_excl_note}"
            )
        else:
            print(
                f"[purpory extract] found {len(code_files)} code, "
                f"{len(doc_files)} docs, {len(paper_files)} papers, "
                f"{len(image_files)} images"
            )
        # Surface files that were seen but not classified (extensionless non-shebang
        # project files like Dockerfile/Makefile, or unsupported extensions), so they
        # are no longer invisible in purpory's own output (#1692).
        _unclassified = detection.get("unclassified", []) if isinstance(detection, dict) else []
        if _unclassified:
            _names = ", ".join(sorted({Path(p).name for p in _unclassified})[:6])
            _more = f" (+{len(_unclassified) - 6} more)" if len(_unclassified) > 6 else ""
            print(
                f"[purpory extract] {len(_unclassified)} file(s) not classified "
                f"(no supported extension or shebang), skipped: {_names}{_more}"
            )
        stages.mark("detect")

        # Resolve the LLM backend only now that we know whether the corpus
        # needs one. A code-only corpus is pure local AST and must not require
        # an API key; the key is enforced below only when there's LLM work.
        from purpory.llm import (
            BACKENDS as _BACKENDS,
            detect_backend as _detect_backend,
            estimate_cost as _estimate_cost,
            extract_corpus_parallel as _extract_corpus_parallel,
            _format_backend_env_keys,
            _get_backend_api_key,
        )

        needs_llm = bool(semantic_files) or dedup_llm
        if backend is None and needs_llm:
            backend = _detect_backend()
        if backend is not None and backend not in _BACKENDS:
            print(
                f"error: unknown backend '{backend}'. Available: {', '.join(sorted(_BACKENDS))}",
                file=sys.stderr,
            )
            sys.exit(1)
        if needs_llm:
            if backend is None:
                reasons = []
                if semantic_files:
                    reasons.append(
                        f"{len(semantic_files)} doc/paper/image file(s) need semantic extraction"
                    )
                if dedup_llm:
                    reasons.append("--dedup-llm was passed")
                hint = ""
                if semantic_files:
                    hint = (
                        " Or pass --code-only to index just the code "
                        "(local AST, no key) and skip the non-code files."
                    )
                print(
                    "error: no LLM API key found (" + "; ".join(reasons) + "). "
                    "Set GEMINI_API_KEY or GOOGLE_API_KEY (gemini), MOONSHOT_API_KEY "
                    "(kimi), ANTHROPIC_API_KEY (claude), OPENAI_API_KEY (openai), "
                    "DEEPSEEK_API_KEY (deepseek), or pass --backend. A code-only "
                    "corpus needs no key." + hint,
                    file=sys.stderr,
                )
                sys.exit(1)
            if backend == "ollama":
                from purpory.llm import _validate_ollama_base_url

                _oll_url = os.environ.get(
                    "OLLAMA_BASE_URL", _BACKENDS["ollama"].get("base_url", "")
                )
                try:
                    _validate_ollama_base_url(_oll_url, warn=False)
                except ValueError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    sys.exit(2)
            if not _get_backend_api_key(backend):
                allow_no_key = False
                if backend == "ollama":
                    from urllib.parse import urlparse

                    ollama_url = os.environ.get(
                        "OLLAMA_BASE_URL",
                        _BACKENDS["ollama"].get("base_url", ""),
                    )
                    try:
                        host = (urlparse(ollama_url).hostname or "").lower()
                    except Exception:
                        host = ""
                    allow_no_key = host in ("localhost", "127.0.0.1", "::1") or host.startswith(
                        "127."
                    )
                elif backend == "bedrock":
                    allow_no_key = bool(
                        os.environ.get("AWS_PROFILE")
                        or os.environ.get("AWS_REGION")
                        or os.environ.get("AWS_DEFAULT_REGION")
                        or os.environ.get("AWS_ACCESS_KEY_ID")
                    )
                elif backend == "claude-cli":
                    import shutil as _shutil

                    allow_no_key = _shutil.which("claude") is not None
                    if not allow_no_key:
                        print(
                            "error: backend 'claude-cli' requires the `claude` CLI on $PATH "
                            "(install Claude Code and run `claude` once to authenticate).",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                if not allow_no_key:
                    print(
                        f"error: backend '{backend}' requires {_format_backend_env_keys(backend)} to be set.",
                        file=sys.stderr,
                    )
                    sys.exit(1)

        # Track whether this run's extraction was incomplete (a whole extractor
        # pass crashed, or some semantic chunks failed). A partial result must not
        # be force-written over a good complete graph — the final write falls back
        # to the #479 shrink guard unless --allow-partial is set.
        _extraction_incomplete = False
        # A walk that couldn't fully enumerate the corpus (permission-denied
        # subtree, I/O error) yields a legitimately smaller graph that must not
        # be force-written over a complete one — same failure class as a crashed
        # pass. detect()/detect_incremental() already record these; consume them.
        if detection.get("walk_errors"):
            _extraction_incomplete = True

        # AST extraction on code files. Empty code list (docs-only corpus) is
        # the issue #698 case — skip cleanly instead of crashing inside extract().
        ast_result: dict = {"nodes": [], "edges": [], "input_tokens": 0, "output_tokens": 0}
        if code_files:
            from purpory.extract import extract as _ast_extract

            # Anchor the cache at the output root, not the scanned project:
            # with --out, a <target>/purpory-out/cache/ would leak a
            # purpory-out/ dir into a project that asked for external output.
            # `root` stays the scanned project so source_file/ids relativize
            # against it; conflating the two basenamed every node (#1941).
            ast_kwargs: dict = {"cache_root": out_root, "root": target}
            if cli_max_workers is not None:
                ast_kwargs["max_workers"] = cli_max_workers
            print(f"[purpory extract] AST extraction on {len(code_files)} code files...")
            try:
                ast_result = _ast_extract(code_files, **ast_kwargs)
            except Exception as exc:
                print(f"[purpory extract] AST extraction failed: {exc}", file=sys.stderr)
                sys.exit(1)
        stages.mark("AST extract")

        # Semantic extraction on docs/papers/images. Check cache first.
        from purpory.cache import (
            check_semantic_cache as _check_semantic_cache,
            prune_semantic_cache as _prune_semantic_cache,
            save_semantic_cache as _save_semantic_cache,
        )

        sem_result: dict = {
            "nodes": [],
            "edges": [],
            "hyperedges": [],
            "input_tokens": 0,
            "output_tokens": 0,
        }
        # Semantic files whose extraction truncated this run. They are left
        # unstamped in the manifest so detect_incremental re-queues them next run
        # (mirrors the #933 failed-chunk handling); captured below before the
        # _partial markers are stripped from the corpus.
        _partial_semantic_files: set[str] = set()
        sem_cache_hits = 0
        sem_cache_misses = 0
        # Deep mode uses its own namespace (cache/semantic-deep/) so deep and
        # standard results for the same content never shadow each other (#1894).
        sem_cache_mode = "deep" if deep_mode else None
        # Entries are attributed to the extraction prompt that produced them, so
        # a release that changes the prompt re-extracts rather than replaying the
        # older vintage alongside the new one (#1939). Read and write must pass
        # the same prompt, or the write lands where the next read won't look.
        from purpory.llm import _extraction_system as _sem_prompt_for

        sem_prompt = _sem_prompt_for(deep=deep_mode)
        if semantic_files:
            sem_paths_str = [str(p) for p in semantic_files]
            if force:
                # --force: skip the cache READ so every semantic file is
                # re-dispatched; the save below still runs so the fresh
                # results replace the stale entries.
                cached_nodes, cached_edges, cached_hyperedges = [], [], []
                uncached_paths = list(sem_paths_str)
            else:
                cached_nodes, cached_edges, cached_hyperedges, uncached_paths = (
                    _check_semantic_cache(
                        sem_paths_str,
                        root=target,
                        mode=sem_cache_mode,
                        prompt=sem_prompt,
                        cache_root=out_root,
                    )
                )
            sem_cache_hits = len(semantic_files) - len(uncached_paths)
            sem_cache_misses = len(uncached_paths)
            sem_result["nodes"].extend(cached_nodes)
            sem_result["edges"].extend(cached_edges)
            sem_result["hyperedges"].extend(cached_hyperedges)
            if sem_cache_hits:
                print(
                    f"[purpory extract] semantic cache: {sem_cache_hits} hit / {sem_cache_misses} miss"
                )

            if uncached_paths:
                print(
                    f"[purpory extract] semantic extraction on {len(uncached_paths)} files via {backend}..."
                )
                corpus_kwargs: dict = {
                    "backend": backend,
                    "model": model,
                    "root": target,
                    "cache_root": out_root,
                }
                if deep_mode:
                    corpus_kwargs["deep_mode"] = True
                if cli_token_budget is not None:
                    corpus_kwargs["token_budget"] = cli_token_budget
                if cli_max_concurrency is not None:
                    corpus_kwargs["max_concurrency"] = cli_max_concurrency

                # Minimal progress callback so the CLI is no longer silent
                # during long local-inference runs (issue #792 addendum).
                # Also track per-chunk success so we can fail loudly when
                # every chunk errors (e.g. missing backend SDK package).
                _chunk_stats = {"total": 0, "succeeded": 0}

                def _progress(idx: int, total: int, _result: dict) -> None:
                    _chunk_stats["total"] = total
                    _chunk_stats["succeeded"] += 1
                    print(
                        f"[purpory extract] chunk {idx + 1}/{total} done",
                        flush=True,
                    )

                corpus_kwargs["on_chunk_done"] = _progress

                try:
                    fresh = _extract_corpus_parallel(
                        [Path(p) for p in uncached_paths],
                        **corpus_kwargs,
                    )
                except ImportError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    sys.exit(1)
                except Exception as exc:
                    print(
                        f"[purpory extract] semantic extraction failed: {exc}",
                        file=sys.stderr,
                    )
                    fresh = {
                        "nodes": [],
                        "edges": [],
                        "hyperedges": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                    }
                    _extraction_incomplete = True  # the semantic pass crashed

                # on_chunk_done only fires after a chunk succeeds. If fresh
                # semantic extraction was requested and no chunks completed,
                # fail instead of writing an AST-only graph with exit 0.
                if uncached_paths and _chunk_stats["succeeded"] == 0:
                    print(
                        f"[purpory extract] error: all semantic chunks failed "
                        f"for backend '{backend}' ({len(uncached_paths)} uncached files) - "
                        f"see per-chunk errors above. If you see 'requires the X package', "
                        f"run `pip install X` and retry.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                # Some (but not all) chunks failed — the graph is missing nodes
                # from the failed chunks, so it must not clobber a larger complete
                # graph without an explicit --allow-partial override.
                if _chunk_stats["total"] and _chunk_stats["succeeded"] < _chunk_stats["total"]:
                    _extraction_incomplete = True
                # Which files truncated this run (item markers + the empty-parse
                # _partial_files set). Computed BEFORE the save so it can be passed
                # as partial_source_files: without it, a file whose only truncated
                # chunk parsed empty (so it has no item markers here) would be
                # written as a complete cache entry, re-promoting it (#1950).
                from purpory.llm import (
                    _partial_source_files as _partial_sf,
                    _strip_partial_markers as _strip_partial,
                )

                _partial_semantic_files = set(_partial_sf(fresh))
                try:
                    _save_semantic_cache(
                        fresh.get("nodes", []),
                        fresh.get("edges", []),
                        fresh.get("hyperedges", []),
                        root=target,
                        allowed_source_files=uncached_paths,
                        mode=sem_cache_mode,
                        prompt=sem_prompt,
                        partial_source_files=_partial_semantic_files or None,
                        cache_root=out_root,
                    )
                except Exception as exc:
                    print(
                        f"[purpory extract] warning: could not write semantic cache: {exc}",
                        file=sys.stderr,
                    )
                # Strip the markers before the corpus feeds the graph so the
                # internal flag never leaks into graph.json.
                _strip_partial(fresh)
                sem_result["nodes"].extend(fresh.get("nodes", []))
                sem_result["edges"].extend(fresh.get("edges", []))
                sem_result["hyperedges"].extend(fresh.get("hyperedges", []))
                sem_result["input_tokens"] += fresh.get("input_tokens", 0)
                sem_result["output_tokens"] += fresh.get("output_tokens", 0)

        # Prune orphaned semantic cache entries. The semantic cache is
        # content-hash-keyed and unversioned, so it is never swept by the AST
        # version-cleanup: every content change or file deletion leaves a
        # permanent orphan that accumulates unbounded (#1527). Sweep it against
        # the FULL live document set (``files_by_type`` — present in both the
        # incremental and full branches), NOT the incremental ``semantic_files``
        # changed-subset, which would delete every unchanged doc's valid entry.
        # Best-effort: a prune failure must never break extraction.
        try:
            from purpory.cache import file_hash as _file_hash

            _live_hashes: set[str] = set()
            for _kind in ("document", "paper", "image"):
                for _fp in files_by_type.get(_kind, []):
                    _abs = Path(_fp)
                    if not _abs.is_absolute():
                        _abs = target / _abs
                    if not _abs.is_file():
                        continue  # deleted/missing — leave out so its entry is pruned
                    try:
                        _live_hashes.add(_file_hash(_abs, target, cache_root=out_root))
                    except OSError:
                        pass
            _prune_semantic_cache(target, _live_hashes, cache_root=out_root)
        except Exception as exc:
            print(
                f"[purpory extract] warning: could not prune semantic cache: {exc}", file=sys.stderr
            )
        stages.mark("semantic extract")

        pg_result: dict = {"nodes": [], "edges": []}
        if cli_postgres_dsn is not None:
            from purpory.pg_introspect import introspect_postgres

            print(f"[purpory extract] introspecting PostgreSQL schema...")
            try:
                pg_result = introspect_postgres(cli_postgres_dsn)
            except (ConnectionError, ImportError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                sys.exit(1)
            print(
                f"[purpory extract] PostgreSQL: {len(pg_result['nodes'])} nodes, "
                f"{len(pg_result['edges'])} edges"
            )

        cargo_result: dict = {"nodes": [], "edges": []}
        if cli_cargo:
            from purpory.cargo_introspect import introspect_cargo

            print("[purpory extract] introspecting Cargo workspace...")
            try:
                cargo_result = introspect_cargo(target)
            except (ConnectionError, ImportError, OSError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                sys.exit(1)
            print(
                f"[purpory extract] Cargo: {len(cargo_result['nodes'])} nodes, "
                f"{len(cargo_result['edges'])} edges"
            )

        # Merge AST + semantic + pg_result + cargo_result. Order matters for deduplication: passing AST
        # first means semantic node attributes win on collision (richer labels
        # for symbols also referenced in docs). Hyperedges only come from the
        # semantic side.
        merged: dict = {
            "nodes": list(ast_result.get("nodes", []))
            + list(sem_result.get("nodes", []))
            + list(pg_result.get("nodes", []))
            + list(cargo_result.get("nodes", [])),
            "edges": list(ast_result.get("edges", []))
            + list(sem_result.get("edges", []))
            + list(pg_result.get("edges", []))
            + list(cargo_result.get("edges", [])),
            "hyperedges": list(sem_result.get("hyperedges", [])),
            "input_tokens": ast_result.get("input_tokens", 0) + sem_result.get("input_tokens", 0),
            "output_tokens": ast_result.get("output_tokens", 0)
            + sem_result.get("output_tokens", 0),
        }

        # Build a manifest-safe files dict: only stamp semantic_hash for files
        # that actually produced output (cache hit or fresh extraction). Files
        # whose chunk failed have no source_file entry in sem_result — leaving
        # their semantic_hash empty so detect_incremental re-queues them (#933).
        # Path normalization against the scan root happens inside the helper
        # (#1897) so fresh root-relative source_files match detect()'s
        # absolute file lists.
        _manifest_files = _stamped_manifest_files(
            files_by_type, sem_result, target, partial_source_files=_partial_semantic_files
        )

        # Files dispatched this run but dropped by _stamped_manifest_files
        # above (failed chunk, LLM omission, or any future exclusion) still
        # carry a stale semantic_hash from a prior successful run in the
        # on-disk manifest; save_manifest's seed loop would otherwise copy it
        # verbatim and mask the omission (#1948). Derived from semantic_files
        # — what was actually SENT to the backend this run (narrowed by the
        # incremental gate and --code-only, widened by deep mode) — NOT from
        # files_by_type: the full live corpus includes untouched files that
        # were never dispatched, and clearing those would blank the whole
        # manifest on every partial incremental run, forcing a full-corpus
        # re-extraction on the next one.
        _stamped_semantic = {f for _flist in _manifest_files.values() for f in _flist}
        _cleared_semantic = {str(p) for p in semantic_files} - _stamped_semantic

        # Full-scan manifest saves prune rows for in-root files that left the
        # scan corpus but still exist on disk (#1908). The corpus must be the
        # RAW detect output (files_by_type), NOT the #933-stamp-filtered
        # _manifest_files above — pruning to the filtered set would erase
        # failed-chunk/omitted-doc rows and every doc row on --code-only runs.
        _scan_corpus = {f for _fl in files_by_type.values() for f in _fl} if has_path else None

        if no_cluster:
            # --no-cluster: dump the raw merged extraction as graph.json.
            # No NetworkX, no community detection, no analysis sidecar.
            # Dedupe nodes (by id) and parallel edges so the raw output matches the
            # clustered path (whose DiGraph collapses both) and stays deterministic
            # across modes (#1317; node dedup also collapses shared Swift module
            # anchors emitted per importing file, #1327).
            from purpory.build import dedupe_edges as _dedupe_edges, dedupe_nodes as _dedupe_nodes
            if (
                incremental_mode
                and not code_files
                and not semantic_files
                and not deleted_files
                and not pg_result.get("nodes")
                and not pg_result.get("edges")
                and not cargo_result.get("nodes")
                and not cargo_result.get("edges")
            ):
                # An exclusion-only change reaches this gate (excluded files
                # are deliberately NOT in deleted_files, #1908) but must still
                # scrub the newly-excluded sources from the raw graph (#1909).
                # This path never runs build_merge, so prune in place.
                if graph_stale_sources:
                    _n_pruned = _prune_graph_sources(
                        existing_graph_data or {}, graph_stale_sources
                    )
                    if _n_pruned:
                        from purpory.supervise.structural import store_structural_graph

                        store_structural_graph(existing_graph_data or {}, root=target)
                        print(
                            f"[purpory extract] pruned {_n_pruned} node(s) from "
                            f"{len(graph_stale_sources)} source file(s) no longer "
                            "in the scan (deleted or excluded)."
                        )
                print(
                    "[purpory extract] no incremental changes detected "
                    "(--no-cluster); graph left untouched."
                )
                try:
                    _save_manifest(
                        _manifest_files,
                        manifest_path=str(manifest_path),
                        kind="both",
                        root=target,
                        scan_corpus=_scan_corpus,
                        clear_semantic=_cleared_semantic,
                    )
                except Exception as exc:
                    print(
                        f"[purpory extract] warning: could not write manifest: {exc}",
                        file=sys.stderr,
                    )
                stages.total()
                sys.exit(0)

            merged["nodes"] = _dedupe_nodes(merged["nodes"])
            merged["edges"] = _dedupe_edges(merged["edges"])
            # Backfill source_file from endpoint nodes — this raw path bypasses
            # build_from_json's backfill, and semantic edges sometimes omit it (#1279).
            _node_sf = {n.get("id"): n.get("source_file") for n in merged["nodes"]}
            for _e in merged["edges"]:
                if not _e.get("source_file"):
                    _e["source_file"] = (
                        _node_sf.get(_e.get("source")) or _node_sf.get(_e.get("target")) or ""
                    )
            if incremental_mode:
                from purpory.build import build_merge as _build_merge
                from purpory.export import graph_data as _graph_data

                _prune_sources = list(deleted_files)
                for _src in list(excluded_files) + graph_stale_sources:
                    if _src not in _prune_sources:
                        _prune_sources.append(_src)
                _graph = _build_merge(
                    [merged],
                    graph_data=existing_graph_data,
                    prune_sources=_prune_sources or None,
                    root=target,
                )
                _raw = _graph_data(_graph, {})
                for _node in _raw.get("nodes", []):
                    _node.pop("community", None)
                    _node.pop("community_name", None)
                    _node.pop("norm_label", None)
                _raw["input_tokens"] = merged["input_tokens"]
                _raw["output_tokens"] = merged["output_tokens"]
                merged = _raw
            if _extraction_incomplete and not cli_allow_partial:
                _existing_n = len((existing_graph_data or {}).get("nodes", []))
                if len(merged["nodes"]) < _existing_n:
                    print(
                        "[purpory extract] error: extraction was incomplete (an AST/"
                        "semantic pass failed) and the resulting --no-cluster graph is "
                        f"smaller than the existing SQLite graph "
                        f"({len(merged['nodes'])} < {_existing_n} nodes). "
                        "Refusing to overwrite a complete graph with a partial one. Re-run after "
                        "fixing the failures, or pass --allow-partial to overwrite anyway.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            from purpory.supervise.structural import store_structural_graph

            store_structural_graph(merged, root=target)
            stages.mark("write")
            cost = _estimate_cost(backend, merged["input_tokens"], merged["output_tokens"])
            print(
                "[purpory extract] stored SQLite graph — "
                f"{len(merged['nodes'])} nodes, "
                f"{len(merged.get('links', merged.get('edges', [])))} edges "
                f"(no clustering)"
            )
            if merged["input_tokens"] or merged["output_tokens"]:
                print(
                    f"[purpory extract] tokens: "
                    f"{merged['input_tokens']:,} in / "
                    f"{merged['output_tokens']:,} out, "
                    f"est. cost: ${cost:.4f}"
                )
            try:
                _save_manifest(
                    _manifest_files,
                    manifest_path=str(manifest_path),
                    kind="both",
                    root=target,
                    scan_corpus=_scan_corpus,
                    clear_semantic=_cleared_semantic,
                )
            except Exception as exc:
                print(
                    f"[purpory extract] warning: could not write manifest: {exc}", file=sys.stderr
                )
            stages.total()
            sys.exit(0)

        # Build graph + cluster + score + write.
        from purpory.build import (
            build as _build,
            build_from_json as _build_from_json,
            build_merge as _build_merge,
        )
        from purpory.cluster import cluster as _cluster, score_all as _score_all
        from purpory.analyze import god_nodes as _god_nodes, surprising_connections as _surprising

        dedup_backend = backend if dedup_llm else None
        if incremental_mode:
            # Prune everything the current scan no longer covers: genuinely
            # deleted manifest rows, excluded-but-alive manifest rows (#1908),
            # and the graph's own stale sources — which catches files that
            # became excluded without ever being manifest-listed (#1909).
            _prune_sources: list[str] = list(deleted_files)
            for _src in list(excluded_files) + graph_stale_sources:
                if _src not in _prune_sources:
                    _prune_sources.append(_src)
            G = _build_merge(
                [merged],
                graph_data=existing_graph_data,
                prune_sources=_prune_sources or None,
                dedup=True,
                dedup_llm_backend=dedup_backend,
                root=target,
            )
        else:
            G = _build([merged], dedup=True, dedup_llm_backend=dedup_backend, root=target)
        stages.mark("build")
        if G.number_of_nodes() == 0:
            print(
                "[purpory extract] graph is empty — extraction produced no nodes. "
                "Possible causes: all files skipped, binary-only corpus, or LLM "
                "returned no edges.",
                file=sys.stderr,
            )
            sys.exit(1)

        communities = _cluster(
            G, resolution=cli_resolution, exclude_hubs_percentile=cli_exclude_hubs
        )
        stages.mark("cluster")
        cohesion = _score_all(G, communities)
        try:
            gods = _god_nodes(G)
        except Exception:
            gods = []
        try:
            surprises = _surprising(G, communities)
        except Exception:
            surprises = []
        stages.mark("analyze")

        existing_node_count = len((existing_graph_data or {}).get("nodes", []))
        if (
            _extraction_incomplete
            and not cli_allow_partial
            and G.number_of_nodes() < existing_node_count
        ):
            print(
                "[purpory extract] error: extraction was incomplete (an AST/semantic "
                "pass failed) and the resulting graph is smaller than the existing "
                f"SQLite graph ({G.number_of_nodes()} < {existing_node_count} nodes). "
                "Refusing to overwrite a complete graph with a "
                "partial one. Re-run after fixing the failures, or pass --allow-partial "
                "to overwrite anyway.",
                file=sys.stderr,
            )
            sys.exit(1)
        stages.mark("store")
        if merged.get("output_tokens", 0) > 0:
            (purpory_out / ".purpory_semantic_marker").write_text(
                json.dumps({"output_tokens": merged["output_tokens"]}), encoding="utf-8"
            )
        analysis = {
            "communities": {str(k): v for k, v in communities.items()},
            "cohesion": {str(k): v for k, v in cohesion.items()},
            "gods": gods,
            "surprises": surprises,
            "tokens": {
                "input": merged["input_tokens"],
                "output": merged["output_tokens"],
            },
        }
        from purpory.export import graph_data as _graph_data
        from purpory.supervise.structural import store_structural_graph

        snapshot = {**_graph_data(G, communities), "analysis": analysis}
        store_structural_graph(snapshot, root=target)
        try:
            _save_manifest(
                _manifest_files,
                manifest_path=str(manifest_path),
                kind="both",
                root=target,
                scan_corpus=_scan_corpus,
                clear_semantic=_cleared_semantic,
            )
        except Exception as exc:
            print(f"[purpory extract] warning: could not write manifest: {exc}", file=sys.stderr)

        cost = _estimate_cost(backend, merged["input_tokens"], merged["output_tokens"])
        print(
            "[purpory extract] stored SQLite graph: "
            f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges, "
            f"{len(communities)} communities"
        )
        if incremental_mode:
            _excl_note = f", {len(excluded_files)} excluded" if excluded_files else ""
            print(
                f"[purpory extract] incremental summary: "
                f"{sem_cache_hits + unchanged_total} files cached/unchanged, "
                f"{len(code_files) + sem_cache_misses} re-extracted, "
                f"{len(deleted_files)} deleted{_excl_note}"
            )
        elif sem_cache_hits:
            print(
                f"[purpory extract] semantic cache: {sem_cache_hits} cached, {sem_cache_misses} re-extracted"
            )
        if merged["input_tokens"] or merged["output_tokens"]:
            print(
                f"[purpory extract] tokens: "
                f"{merged['input_tokens']:,} in / "
                f"{merged['output_tokens']:,} out, "
                f"est. cost (~{backend}): ${cost:.4f}"
            )
        stages.total()

    else:
        print(f"error: unknown command '{cmd}'", file=sys.stderr)
        print("Run 'purpory --help' for usage.", file=sys.stderr)
        sys.exit(1)
