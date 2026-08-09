"""CLI adapter for ``purpory model``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from purpory.supervise.embeddings import DEFAULT_MODEL as DEFAULT_EMBEDDING_MODEL
from purpory.supervise.gate.qwen import (
    DEFAULT_MODEL,
    DEFAULT_RECONCILE_MODEL,
    RECOMMENDED_GATE_MODELS,
    RECOMMENDED_RECONCILE_MODELS,
)
from purpory.supervise.gate.runtime import GateModelManager


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="purpory model",
        description="Manage the local gate and reconcile models in the shared Ollama runtime.",
        epilog="Global option accepted anywhere: --json",
    )
    subparsers = parser.add_subparsers(dest="verb", required=True)

    install = subparsers.add_parser("install", help="pull models into Ollama")
    install.add_argument(
        "--role",
        choices=("all", "gate", "reconcile", "embedding"),
        default="all",
        help="role to install (default: all)",
    )
    install.add_argument("--model", help="specific model tag to install")
    install.add_argument("--revision")
    install.add_argument("--force", action="store_true")

    subparsers.add_parser("list", help="list installed and recommended models")

    start = subparsers.add_parser("start", help="verify the shared Ollama runtime")
    start.add_argument("--port", type=int, default=0)
    start.add_argument("--wait", type=float, default=300.0)

    stop = subparsers.add_parser("stop", help="report shared runtime ownership")
    stop.add_argument("--wait", type=float, default=10.0)
    stop.add_argument("--force", action="store_true")

    subparsers.add_parser("status", help="show installation and runtime health")

    logs = subparsers.add_parser("logs", help="show recent model server logs")
    logs.add_argument("--lines", type=int, default=100)
    return parser


def _emit(value: Any, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _extract_json(arguments: Sequence[str]) -> tuple[list[str], bool]:
    json_output = False
    cleaned: list[str] = []
    for argument in arguments:
        if argument == "--json":
            json_output = True
        else:
            cleaned.append(argument)
    return cleaned, json_output


def dispatch_model(arguments: Sequence[str] | None = None) -> None:
    raw = list(sys.argv[2:] if arguments is None else arguments)
    cleaned, json_output = _extract_json(raw)
    options = _parser().parse_args(cleaned)
    manager = GateModelManager()
    try:
        if options.verb == "install":
            if options.model and options.role == "all":
                raise ValueError("--model requires specific --role (gate, reconcile, or embedding)")
            role_models = {
                "gate": DEFAULT_MODEL,
                "reconcile": DEFAULT_RECONCILE_MODEL,
                "embedding": DEFAULT_EMBEDDING_MODEL,
            }
            roles = tuple(role_models) if options.role == "all" else (options.role,)
            models = {
                role: manager.install(
                    model_id=options.model or role_models[role],
                    revision=options.revision,
                    force=options.force,
                )
                for role in roles
            }
            result = {
                "action": (
                    "installed"
                    if any(item["action"] == "installed" for item in models.values())
                    else "kept"
                ),
                "runtime": "ollama",
                "models": models,
            }
        elif options.verb == "list":
            installed = manager.list_installed_models()
            result = {
                "installed": installed,
                "gatePresets": RECOMMENDED_GATE_MODELS,
                "reconcilePresets": RECOMMENDED_RECONCILE_MODELS,
                "defaultGate": DEFAULT_MODEL,
                "defaultReconcile": DEFAULT_RECONCILE_MODEL,
            }
        elif options.verb == "start":
            result = manager.start(port=options.port, wait_seconds=options.wait)
        elif options.verb == "stop":
            result = manager.stop(wait_seconds=options.wait, force=options.force)
        elif options.verb == "status":
            result = manager.status()
            result["models"] = {
                "gate": result.copy(),
                "reconcile": manager.status(model=DEFAULT_RECONCILE_MODEL),
                "embedding": manager.status(model=DEFAULT_EMBEDDING_MODEL),
            }
        elif options.verb == "logs":
            result = manager.logs(lines=options.lines)
        else:
            raise ValueError(f"unsupported model command: {options.verb}")
        _emit(result, json_output=json_output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def main() -> None:
    dispatch_model(sys.argv[1:])


if __name__ == "__main__":
    main()
