"""CLI adapter for ``purpory model``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from purpory.supervise.gate.qwen import DEFAULT_MODEL
from purpory.supervise.gate.runtime import GateModelManager


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="purpory model",
        description="Manage the local gate model and its warm inference process.",
        epilog="Global option accepted anywhere: --json",
    )
    subparsers = parser.add_subparsers(dest="verb", required=True)

    install = subparsers.add_parser("install", help="download and pin the gate model")
    install.add_argument("--model", default=DEFAULT_MODEL)
    install.add_argument("--revision")
    install.add_argument("--force", action="store_true")

    start = subparsers.add_parser("start", help="start and warm the local model server")
    start.add_argument("--port", type=int, default=0)
    start.add_argument("--wait", type=float, default=300.0)

    stop = subparsers.add_parser("stop", help="stop the managed model server")
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
            result = manager.install(
                model_id=options.model,
                revision=options.revision,
                force=options.force,
            )
        elif options.verb == "start":
            result = manager.start(port=options.port, wait_seconds=options.wait)
        elif options.verb == "stop":
            result = manager.stop(wait_seconds=options.wait, force=options.force)
        elif options.verb == "status":
            result = manager.status()
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
