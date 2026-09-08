"""Hermes Agent memory provider backed by the Purpory CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from agent.memory_provider import MemoryProvider, RecallStatus


TOOLS = [
    {
        "name": "purpory_query",
        "description": "Find up to five relevant Purpory nodes without loading their content.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "purpory_explain",
        "description": "Load evidence for selected Purpory node IDs returned by purpory_query.",
        "parameters": {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 5,
                }
            },
            "required": ["nodes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "purpory_path",
        "description": "Show the relationship path between two Purpory nodes.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
            },
            "required": ["source", "target"],
            "additionalProperties": False,
        },
    },
]


def _executable() -> str | None:
    return shutil.which(os.environ.get("PURPORY_EXECUTABLE", "purpory"))


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


class PurporyMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        self._bin = ""
        self._root = ""
        self._hermes_home = ""
        self._session_id = ""
        self._primary = True
        self._recalled = False

    @property
    def name(self) -> str:
        return "purpory"

    def is_available(self) -> bool:
        return _executable() is not None

    def unavailable_reason(self) -> str:
        return "Install the purpory CLI or set PURPORY_EXECUTABLE to its path."

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        executable = _executable()
        if executable is None:
            raise RuntimeError(self.unavailable_reason())
        self._bin = executable
        self._root = str(Path(os.environ.get("PURPORY_ROOT", os.getcwd())).resolve())
        self._hermes_home = str(Path(kwargs["hermes_home"]).resolve())
        self._session_id = session_id
        self._primary = kwargs.get("agent_context", "primary") == "primary"

    def system_prompt_block(self) -> str:
        return (
            "# Purpory Project Memory\n"
            "Purpory prefetch returns navigation hints, not source content. "
            "Use purpory_explain only for relevant node IDs and purpory_path for relationships."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._recalled = False
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": query,
            "session_id": session_id or self._session_id,
            "cwd": self._root,
        }
        output = self._run(["preflight", "hermes"], json.dumps(payload), timeout=7)
        if not output:
            return ""
        response = json.loads(output)
        if response.get("decision") == "block":
            raise RuntimeError(response.get("reason", "Purpory preflight failed"))
        context = response.get("hookSpecificOutput", {}).get("additionalContext", "")
        self._recalled = bool(context.strip())
        return context

    def recall_status(self) -> RecallStatus | None:
        return RecallStatus("Purpory", 0) if self._recalled else None

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return TOOLS

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if tool_name == "purpory_query":
            command = ["query", "--", _text(args.get("question"), "question")]
        elif tool_name == "purpory_explain":
            nodes = args.get("nodes")
            if not isinstance(nodes, list) or not 1 <= len(nodes) <= 5:
                raise ValueError("nodes must contain between 1 and 5 node IDs")
            command = ["explain", "--", *[_text(node, "node") for node in nodes]]
        elif tool_name == "purpory_path":
            command = [
                "path",
                "--",
                _text(args.get("source"), "source"),
                _text(args.get("target"), "target"),
            ]
        else:
            raise ValueError(f"unknown Purpory tool: {tool_name}")
        return json.dumps({"result": self._run(command)}, ensure_ascii=False)

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if not self._primary or not self._session_id:
            return
        records = [
            {"role": message.get("role"), "content": message.get("content")}
            for message in messages
            if message.get("role") in {"user", "assistant"} and message.get("content")
        ]
        if not any(record["role"] == "user" for record in records):
            return
        path = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".jsonl", dir=self._hermes_home, delete=False
            ) as transcript:
                path = transcript.name
                for record in records:
                    transcript.write(json.dumps(record, ensure_ascii=False) + "\n")
            payload = {
                "hook_event_name": "SessionEnd",
                "session_id": self._session_id,
                "cwd": self._root,
                "transcript_path": path,
                "reason": "hermes-session-end",
            }
            self._run(["session-end", "hermes"], json.dumps(payload), timeout=15)
        finally:
            if path:
                Path(path).unlink(missing_ok=True)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        self._session_id = new_session_id
        self._recalled = False

    def _run(self, arguments: list[str], input_text: str | None = None, *, timeout: int = 30) -> str:
        environment = os.environ.copy()
        if self._session_id:
            environment["PURPORY_SESSION"] = f"hermes:{self._session_id}"
        try:
            result = subprocess.run(
                [self._bin, "--root", self._root, *arguments],
                cwd=self._root,
                env=environment,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"purpory command timed out after {timeout}s") from error
        if result.returncode:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            raise RuntimeError(f"purpory command failed: {detail}")
        return result.stdout.strip()


def register(ctx: Any) -> None:
    ctx.register_memory_provider(PurporyMemoryProvider())
