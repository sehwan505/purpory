"""Product CLI adapters for Purpory's context plane."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from purpory.supervise.library import ContextService

USER_MEMORY_CATEGORIES = {
    "intent": "decision",
    "knowledge": "note",
    "reference": "doc-ref",
}


def _remember_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="purpory remember",
        description="Store durable human knowledge at a stable logical address.",
    )
    parser.add_argument("key", nargs="?")
    content = parser.add_mutually_exclusive_group()
    content.add_argument("--value", help="inline context text")
    content.add_argument("--source", help="live @root pointer or external reference")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--list", action="store_true", help="list visible project memories")
    operation.add_argument("--batch", metavar="FILE", help="preview a JSON batch; use - for stdin")
    parser.add_argument("--apply", action="store_true", help="apply --batch atomically")
    parser.add_argument("--prefix", help="limit --list to one logical key prefix")
    parser.add_argument("--session", help="session id recorded with an applied batch")
    parser.add_argument(
        "--global-request",
        action="store_true",
        help="propose a global memory write for explicit human approval",
    )
    parser.add_argument("--rationale", help="why a proposed global memory should be available")
    classification = parser.add_mutually_exclusive_group()
    classification.add_argument(
        "--category",
        choices=tuple(USER_MEMORY_CATEGORIES),
        help="user-facing memory category (default: knowledge)",
    )
    classification.add_argument(
        "--kind",
        choices=("note", "code-area", "doc-ref", "decision", "seeded"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--project", help="stable project namespace (default: infer from cwd)")
    parser.add_argument("--db")
    parser.add_argument("--json", action="store_true")
    return parser


def _read_remember_batch(path: str) -> list[dict[str, Any]]:
    limit = 1_048_576
    if path == "-":
        raw = sys.stdin.read(limit + 1)
    else:
        batch_path = Path(path).expanduser()
        if batch_path.stat().st_size > limit:
            raise ValueError("remember batch cannot exceed 1 MiB")
        raw = batch_path.read_text(encoding="utf-8")
    if len(raw.encode("utf-8")) > limit:
        raise ValueError("remember batch cannot exceed 1 MiB")
    parsed = json.loads(raw)
    changes = parsed.get("changes") if isinstance(parsed, dict) else parsed
    if not isinstance(changes, list):
        raise ValueError("remember batch must be a JSON list or an object with changes")
    if not all(isinstance(change, dict) for change in changes):
        raise ValueError("each remember batch change must be an object")
    return changes


def _prepare_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="purpory prepare",
        description="Prepare situation-aware context for one agent request.",
    )
    parser.add_argument("message")
    parser.add_argument("--path", action="append", default=[], dest="paths")
    parser.add_argument("--root", default=".")
    parser.add_argument("--cwd")
    parser.add_argument("--session")
    parser.add_argument("--project")
    parser.add_argument("--budget", type=int, default=2_000)
    retention = parser.add_mutually_exclusive_group()
    retention.add_argument(
        "--retain-input",
        action="store_true",
        dest="retain_input",
        help="retain the local request text in the decision audit (default)",
    )
    retention.add_argument(
        "--no-retain-input",
        action="store_false",
        dest="retain_input",
        help="store only a request hash in the decision audit",
    )
    parser.set_defaults(retain_input=True)
    parser.add_argument("--gate-url")
    parser.add_argument("--gate-model")
    parser.add_argument("--gate-revision")
    parser.add_argument("--gate-timeout", type=float, default=2.0)
    parser.add_argument("--allow-remote-gate", action="store_true")
    parser.add_argument("--no-model-start", action="store_true")
    parser.add_argument("--model-start-timeout", type=float, default=300.0)
    parser.add_argument("--db")
    parser.add_argument("--json", action="store_true")
    return parser


def _dashboard_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="purpory dashboard",
        description="Start the local context supervision dashboard.",
    )
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--root", default=".")
    parser.add_argument("--graph")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-model-start", action="store_true")
    parser.add_argument("--model-start-timeout", type=float, default=300.0)
    parser.add_argument("--db")
    return parser


def _embed_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="purpory embed",
        description="Materialize queued embeddings for bulk imports or backfills.",
    )
    parser.add_argument("--limit", type=int, default=32)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--root", default=".", help=argparse.SUPPRESS)
    parser.add_argument("--db")
    parser.add_argument("--json", action="store_true")
    return parser


def _emit(value: Any, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _managed_gate_provider(options: argparse.Namespace, *, explicit: bool) -> Any:
    if explicit and options.gate_url:
        from purpory.supervise.gate.qwen import DEFAULT_MODEL, QwenGateProvider

        return QwenGateProvider(
            base_url=options.gate_url,
            model=options.gate_model or DEFAULT_MODEL,
            model_revision=options.gate_revision,
            timeout_seconds=options.gate_timeout,
            allow_remote=options.allow_remote_gate,
        )
    if os.environ.get("PURPORY_GATE_URL", "").strip():
        return None

    from purpory.supervise.gate.provider import UnavailableGateProvider
    from purpory.supervise.gate.runtime import GateModelManager

    try:
        return GateModelManager().provider(
            start_if_needed=not options.no_model_start,
            start_timeout_seconds=options.model_start_timeout,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return UnavailableGateProvider(f"managed gate startup failed: {exc}")


def dispatch_product_command(command: str, arguments: Sequence[str] | None = None) -> None:
    raw = list(sys.argv[2:] if arguments is None else arguments)
    parsers = {
        "remember": _remember_parser,
        "prepare": _prepare_parser,
        "dashboard": _dashboard_parser,
        "embed": _embed_parser,
    }
    try:
        parser_factory = parsers.get(command)
        if parser_factory is None:
            raise ValueError(f"unsupported product command: {command}")
        options = parser_factory().parse_args(raw)
        if command == "embed":
            from purpory.supervise.embeddings import EmbeddingService
            from purpory.supervise.repository import ContextGraphRepository

            embeddings = EmbeddingService(ContextGraphRepository(options.db))
            result = embeddings.status() if options.status else embeddings.run(limit=options.limit)
            _emit(result, json_output=options.json)
            return

        root = Path(options.root).expanduser().resolve()

        if command == "remember":
            service = ContextService(
                db_path=options.db,
                root=root,
                project_id=options.project,
            )
            if options.list:
                if (
                    options.key
                    or options.value is not None
                    or options.source is not None
                    or options.global_request
                    or options.rationale
                ):
                    raise ValueError("--list cannot be combined with a key, --value, or --source")
                if options.apply:
                    raise ValueError("--apply requires --batch")
                result = service.list_topics(prefix=options.prefix)
                _emit(result, json_output=options.json)
                return
            if options.batch:
                if (
                    options.key
                    or options.value is not None
                    or options.source is not None
                    or options.global_request
                    or options.rationale
                ):
                    raise ValueError("--batch cannot be combined with a key, --value, or --source")
                if options.prefix:
                    raise ValueError("--prefix requires --list")
                result = service.reconcile_topics(
                    _read_remember_batch(options.batch),
                    apply=options.apply,
                    session_id=options.session,
                )
                _emit(result, json_output=options.json)
                return
            if options.apply:
                raise ValueError("--apply requires --batch")
            if options.prefix:
                raise ValueError("--prefix requires --list")
            if not options.key:
                raise ValueError("remember requires a key, --list, or --batch")
            if (options.value is None) == (options.source is None):
                raise ValueError("exactly one of --value or --source is required")
            kind = options.kind or USER_MEMORY_CATEGORIES[options.category or "knowledge"]
            if options.global_request:
                if not options.rationale:
                    raise ValueError("--global-request requires --rationale")
                result = service.propose_global_memory(
                    options.key,
                    value=options.value,
                    source=options.source,
                    kind=kind,
                    rationale=options.rationale,
                )
            else:
                if options.rationale:
                    raise ValueError("--rationale requires --global-request")
                result = service.set_topic(
                    options.key,
                    value=options.value,
                    source=options.source,
                    kind=kind,
                )
            _emit(result, json_output=options.json)
            return

        gate_provider = _managed_gate_provider(options, explicit=command == "prepare")
        service = ContextService(
            db_path=options.db,
            root=root,
            graph_path=getattr(options, "graph", None),
            gate_provider=gate_provider,
        )

        if command == "prepare":
            result = service.prepare(
                options.message,
                session_id=options.session,
                project=options.project,
                working_directory=options.cwd or root,
                active_paths=options.paths,
                token_budget=options.budget,
                retain_input=options.retain_input,
            )
            if options.json:
                _emit(result, json_output=True)
            elif result["action"] == "retrieve":
                from purpory.supervise.gate.service import render_awareness

                awareness = render_awareness(result.get("awareness") or [])
                rendered = result["context"]["rendered"].rstrip()
                _emit(
                    "\n\n".join(part for part in (rendered, awareness) if part),
                    json_output=False,
                )
            elif result["action"] == "ask":
                _emit(result["clarification"], json_output=False)
            return

        from purpory.supervise.serve import serve_dashboard

        serve_dashboard(
            service,
            port=options.port,
            open_browser=not options.no_browser,
        )
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m purpory.supervise {remember|prepare|dashboard|embed}")
    dispatch_product_command(sys.argv[1], sys.argv[2:])


if __name__ == "__main__":
    main()
