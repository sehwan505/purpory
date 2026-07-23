"""Vendored code-graph CLI engine used by Purpory."""

from __future__ import annotations
import errno
import os
import sys

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("purpory")
except Exception:
    __version__ = "unknown"

# Output directory — override with PURPORY_OUT env var for worktrees or shared-output setups.
# Accepts a relative name ("purpory-out-feature") or an absolute path ("/shared/purpory-out").
# Defined once in purpory.paths so the security/callflow path guards honour the
# same override (#1423).
from purpory.paths import PURPORY_OUT as _PURPORY_OUT

# Official prompt-level integrations.
from purpory.install import (  # noqa: E402,F401
    _AGENTS_MD_MARKER,
    _CLAUDE_MD_MARKER,
    _always_on,
    _replace_or_append_section,
    claude_install,
    claude_uninstall,
    codex_install,
    codex_uninstall,
    dispatch_install_cli,
)
from purpory.cli import dispatch_command  # noqa: E402


_ALWAYS_ON_ALIASES = {
    "_CLAUDE_MD_SECTION": "claude-md",
    "_AGENTS_MD_SECTION": "agents-md",
}


def __getattr__(name: str) -> str:
    # PEP 562 keeps the two packaged instruction sections lazy at import time.
    base = _ALWAYS_ON_ALIASES.get(name)
    if base is not None:
        return _always_on(base)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _silence_broken_pipe() -> None:
    """Handle a downstream reader that closed the pipe early. Redirect stdout to
    devnull so the interpreter's shutdown flush does not raise a second time, then
    exit 0 — the reader (head, `Select-Object -First N`, `sed q`) has what it needs."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except Exception:
        pass
    sys.exit(0)


def main() -> None:
    """Console entry point. Wraps the CLI so that when a downstream consumer closes
    stdout early, purpory treats it as success instead of crashing with an
    unhandled write-to-closed-pipe error and exit 255 — which made CI wrappers and
    agent harnesses read a successful query as a command failure (#1807)."""
    try:
        _run_cli()
        # Flush explicitly, inside the guard. Piped stdout is block-buffered, so a
        # small fully-buffered output would otherwise only flush at interpreter
        # shutdown — outside this try — where a reader that closed the pipe surfaces
        # as a noisy "Exception ignored on flushing sys.stdout" and a nonzero exit.
        sys.stdout.flush()
    except BrokenPipeError:
        _silence_broken_pipe()
    except OSError as exc:
        # Windows surfaces a write to a closed pipe as OSError(EINVAL) rather than
        # BrokenPipeError; EPIPE is the POSIX form when it slips past the above.
        if getattr(exc, "errno", None) in (errno.EPIPE, errno.EINVAL):
            _silence_broken_pipe()
        else:
            raise


def _run_cli() -> None:
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if len(sys.argv) >= 2 and sys.argv[1] in ("-v", "--version", "version"):
        print(f"purpory {__version__}")
        return

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "-?"):
        print("Usage: purpory <command>")
        print()
        print("Commands:")
        print("  claude install          configure Claude Code UserPromptSubmit preflight")
        print("  claude uninstall        remove Claude Code Purpory integration")
        print("  codex install           configure Codex UserPromptSubmit preflight")
        print("  codex uninstall         remove Codex Purpory integration")
        print('  path "A" "B"            shortest path between two nodes in graph.json')
        print("    --graph <path>          path to graph.json (default purpory-out/graph.json)")
        print('  explain "X"             plain-language explanation of a node and its neighbors')
        print("    --graph <path>          path to graph.json (default purpory-out/graph.json)")
        print("  diagnose multigraph    report same-endpoint edge collapse risk in graph.json")
        print("    --graph <path>          path to graph/extraction JSON")
        print("                            (default purpory-out/graph.json)")
        print("    --json                  emit machine-readable JSON")
        print("    --max-examples N        max same-endpoint examples to print (default 5)")
        print("    --directed              force directed post-build simulation")
        print("    --undirected            force undirected post-build simulation")
        print("                            (default follows JSON directed flag;")
        print("                             raw extraction with no flag defaults directed)")
        print("    --extract-path PATH     extractor source for suppression scan")
        print(
            "  clone <github-url>      clone a GitHub repo locally and print its path for /purpory"
        )
        print(
            "  merge-driver <base> <current> <other>  git merge driver: union-merge two graph.json files (set up via hook install)"
        )
        print(
            "  merge-graphs <g1> <g2>  merge two or more graph.json files into one cross-repo graph"
        )
        print("    --out <path>            output path (default: purpory-out/merged-graph.json)")
        print("    --branch <branch>       checkout a specific branch (default: repo default)")
        print(
            "    --out <dir>             clone to a custom directory (default: ~/.purpory/repos/<owner>/<repo>)"
        )
        print("  add <url>               fetch a URL and save it to ./raw, then update the graph")
        print('    --author "Name"         tag the author of the content')
        print('    --contributor "Name"    tag who added it to the corpus')
        print("    --dir <path>            target directory (default: ./raw)")
        print("  watch <path>            watch a folder and rebuild the graph on code changes")
        print(
            "  update <path>           re-extract code files and update the graph (no LLM needed)"
        )
        print(
            "    --force                 overwrite graph.json even if the rebuild has fewer nodes"
        )
        print(
            "                            (also: PURPORY_FORCE=1 env var; use after refactors that delete code)"
        )
        print("    --no-cluster            skip clustering, write raw extraction only")
        print(
            "  cluster-only <path>     rerun clustering on an existing graph.json and regenerate report"
        )
        print(
            "    --no-viz                skip graph.html generation (useful for >5000 node graphs / CI)"
        )
        print(
            "    --graph <path>          path to graph.json (default <path>/purpory-out/graph.json)"
        )
        print(
            "    --no-label              keep 'Community N' placeholders (skip LLM community naming)"
        )
        print(
            "    --backend=<name>        backend to use for community naming (default: auto-detect)"
        )
        print("    --model=<name>          model to use for community naming")
        print(
            "    --max-concurrency=N     parallel community-labeling LLM calls (default 4; forced to 1 for ollama/claude-cli)"
        )
        print("    --batch-size=N          communities per labeling LLM call (default 100)")
        print(
            "  label <path>            (re)name communities with the configured LLM backend, regenerate report"
        )
        print(
            "    --missing-only         keep existing labels and only name missing/placeholder communities"
        )
        print("    --backend=<name>        backend to use (default: auto-detect from API keys)")
        print("    --model=<name>          model to use for community naming")
        print(
            "    --max-concurrency=N     parallel labeling LLM calls (default 4; forced to 1 for ollama/claude-cli)"
        )
        print("    --batch-size=N          communities per labeling LLM call (default 100)")
        print('  query "<question>"       BFS traversal of graph.json for a question')
        print("    --dfs                   use depth-first instead of breadth-first")
        print("    --context C             explicit edge-context filter (repeatable)")
        print("    --budget N              cap output at N tokens (default 2000)")
        print("    --graph <path>          path to graph.json (default purpory-out/graph.json)")
        print('  affected "X"             reverse traversal to find nodes impacted by X')
        print("    --relation R            edge relation to traverse in reverse (repeatable)")
        print("    --depth N               reverse traversal depth (default 2)")
        print("    --graph <path>          path to graph.json (default purpory-out/graph.json)")
        print("  remember <key>         store durable human context")
        print("    --value TEXT           store inline knowledge")
        print("    --source POINTER       store a live @root or external reference")
        print("    --list [--prefix KEY]  list visible project memories")
        print("    --batch FILE [--apply] preview or atomically apply memory changes")
        print('  prepare "<request>"    prepare situation-aware context for an agent')
        print("    --path PATH            add an active filesystem path (repeatable)")
        print("    --session ID           preserve delivery memory across requests")
        print("    --budget N             cap delivered context tokens (default 2000)")
        print("    --no-retain-input      store only a request hash in the local audit")
        print("  dashboard              launch the local supervision UI")
        print("    --port N               bind a loopback port (default: automatic)")
        print("  model <verb>            manage the local Qwen gate runtime")
        print("    install|start|stop     download, warm, or stop the managed model")
        print("    status|logs            inspect runtime health and recent logs")
        print(
            "  save-result             save a Q&A result to purpory-out/memory/ for graph feedback loop"
        )
        print("    --question Q            the question asked")
        print("    --answer A              the answer to save")
        print("    --type T                query type: query|path_query|explain (default: query)")
        print("    --nodes N1 N2 ...       source node labels cited in the answer")
        print("    --outcome O             work-memory signal: useful|dead_end|corrected")
        print(
            "    --correction TEXT       what the right answer was (pairs with --outcome corrected)"
        )
        print("    --memory-dir DIR        memory directory (default: purpory-out/memory)")
        print(
            "  reflect                 aggregate purpory-out/memory/ outcomes into a deterministic lessons doc"
        )
        print("    --memory-dir DIR        memory directory (default: purpory-out/memory)")
        print(
            "    --out FILE              output path (default: purpory-out/reflections/LESSONS.md)"
        )
        print(
            "    --graph PATH            graph.json, for community grouping + dropping stale nodes (optional)"
        )
        print(
            "    --analysis PATH         .purpory_analysis.json (optional, auto-detected next to --graph)"
        )
        print(
            "    --labels PATH           .purpory_labels.json (optional, auto-detected next to --graph)"
        )
        print("    --half-life-days N      signal weight halves every N days (default 30)")
        print("    --min-corroboration N   distinct useful results to prefer a node (default 2)")
        print(
            "  check-update <path>     check needs_update flag and notify if semantic re-extraction is pending (cron-safe)"
        )
        print("  tree                    emit a D3 v7 collapsible-tree HTML for graph.json")
        print("    --graph PATH            path to graph.json (default purpory-out/graph.json)")
        print("    --output HTML           output path (default purpory-out/GRAPH_TREE.html)")
        print("    --root PATH             filesystem root for the hierarchy")
        print("    --max-children N        cap children per node (default 200)")
        print("    --top-k-edges N         per-symbol outbound edges in inspector (default 12)")
        print("    --label NAME            project label in header")
        print(
            "  extract <path>          headless full extraction (AST + semantic LLM) for CI/scripts"
        )
        print(
            "    --backend B             gemini|kimi|claude|openai|deepseek|ollama (default: whichever API key is set)"
        )
        print(
            "                            openai also reaches self-hosted OpenAI-compatible servers (llama.cpp,"
        )
        print(
            "                            vLLM, LM Studio): set OPENAI_BASE_URL (e.g. http://localhost:8080/v1)"
        )
        print("                            and OPENAI_MODEL to the model name your server serves")
        print(
            "                            claude also reaches custom Anthropic-compatible endpoints (LiteLLM"
        )
        print(
            "                            proxy, gateways): set ANTHROPIC_BASE_URL and ANTHROPIC_MODEL"
        )
        print("    --model M               override backend default model")
        print("    --mode deep             aggressive INFERRED-edge semantic extraction")
        print("    --force                 full re-scan and re-dispatch: skip the incremental")
        print(
            "                            manifest gate and semantic cache reads (env: PURPORY_FORCE=1)"
        )
        print("    --max-workers N         AST extraction subprocess count (default: cpu_count)")
        print(
            "    --token-budget N        per-chunk token cap for semantic extraction (default: 60000)"
        )
        print(
            "    --max-concurrency N     parallel semantic chunks in flight (default: 4; set 1 for local LLMs)"
        )
        print(
            "    --api-timeout S         per-request timeout in seconds for the LLM client (default: 600)"
        )
        print("    --out DIR               output dir (default: <path>); writes <DIR>/purpory-out/")
        print(
            "    --google-workspace      export .gdoc/.gsheet/.gslides shortcuts via gws before extraction"
        )
        print("    --no-cluster            skip clustering, write raw extraction only")
        print(
            "    --code-only             index code (local AST, no API key) and skip doc/paper/image files"
        )
        print("    --postgres DSN          extract schema from a live PostgreSQL database")
        print("                            maps tables, views, functions + FK relationships;")
        print("                            column-level detail is not represented in the graph")
        print("    --cargo                 extract crate→crate deps from Cargo.toml")
        print("    --global                also merge the resulting graph into the global graph")
        print("    --as <tag>              repo tag for --global (default: target directory name)")
        print(
            "  global add <graph.json>  add/update a project graph in the global graph (~/.purpory/global-graph.json)"
        )
        print("    --as <tag>               repo tag (default: parent directory name)")
        print("  global remove <tag>      remove a repo's nodes from the global graph")
        print("  global list              list repos in the global graph")
        print("  global path              print path to the global graph file")
        print("  benchmark [graph.json]  measure token reduction vs naive full-corpus approach")
        print("  export callflow-html    emit Mermaid-based architecture/call-flow HTML")
        print(
            "  hook install            install post-commit/post-checkout git hooks (all platforms)"
        )
        print("  hook uninstall          remove git hooks")
        print("  hook status             check if git hooks are installed")
        print()
        print("Run `purpory prepare --help` for context delivery options.")
        return

    cmd = sys.argv[1]

    # Universal help guard: -h/--help/-? anywhere after the command shows help
    # and stops — prevents flags from silently triggering destructive subcommands
    # Exempt free-text commands whose user string may contain these tokens.
    _FREE_TEXT_CMDS = {
        "query",
        "explain",
        "path",
        "save-result",
        "remember",
        "prepare",
        "dashboard",
        "model",
    }
    if cmd not in _FREE_TEXT_CMDS and any(a in {"-h", "--help", "-?"} for a in sys.argv[2:]):
        print(f"Run 'purpory --help' for full usage.")
        return

    if dispatch_install_cli(cmd):
        return
    dispatch_command(cmd)


if __name__ == "__main__":
    main()
