from __future__ import annotations
import json
import os
import sys
from typing import Any
from purpory.llm.providers.base import BaseLLMProvider
from purpory.llm.helpers import (
    _extraction_system,
    _with_image_notes,
    _resolve_api_timeout,
    _parse_llm_json,
    _response_is_hollow,
    _no_window_kwargs,
)

def _claude_cli_envelope(stdout: str) -> dict:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude -p produced unparseable JSON envelope: {exc}; "
            f"first 500 chars of stdout: {stdout[:500]!r}"
        ) from exc
    if isinstance(envelope, list):
        result_events = [
            e for e in envelope
            if isinstance(e, dict) and e.get("type") == "result"
        ]
        if result_events:
            return result_events[-1]
        if envelope and isinstance(envelope[-1], dict):
            return envelope[-1]
        raise RuntimeError(
            "claude -p returned a JSON array with no result object; "
            f"first 500 chars of stdout: {stdout[:500]!r}"
        )
    return envelope

class ClaudeCLIProvider(BaseLLMProvider):
    """Adapter for local Claude Code CLI tool."""

    def call_direct(
        self,
        api_key: str | None,
        model: str,
        user_message: str,
        max_tokens: int,
        temperature: float | None,
        deep_mode: bool = False,
        images: list[Any] | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import platform
        import shutil
        import subprocess

        claude_cmd = "claude"
        if platform.system() == "Windows":
            cmd_path = shutil.which("claude.cmd")
            if cmd_path:
                claude_cmd = cmd_path
            elif shutil.which("claude") is None:
                raise RuntimeError(
                    "Claude Code CLI not found on $PATH. Install from "
                    "https://claude.ai/code and run `claude` once to authenticate."
                )
        elif shutil.which("claude") is None:
            raise RuntimeError(
                "Claude Code CLI not found on $PATH. Install from "
                "https://claude.ai/code and run `claude` once to authenticate."
            )

        add_dir_args: list[str] = []
        if images:
            user_message = _with_image_notes(user_message, images, with_paths=True)
            seen_dirs: set[str] = set()
            for r in images:
                d = str(r.path.parent)
                if d not in seen_dirs:
                    seen_dirs.add(d)
                    add_dir_args.extend(["--add-dir", d])

        combined_message = (
            _extraction_system(deep=deep_mode)
            + "\n\n---\n"
            + "Now extract the knowledge graph from the following source file(s) "
            + "and output ONLY the JSON object described above. No prose, no "
            + "preamble, no markdown fences.\n\n"
            + user_message
        )
        cli_args = [
            claude_cmd, "-p",
            "--output-format", "json",
            "--no-session-persistence",
            *add_dir_args,
        ]

        cli_model = os.environ.get("PURPORY_CLAUDE_CLI_MODEL", "").strip()
        if cli_model:
            cli_args.extend(["--model", cli_model])

        proc = subprocess.run(
            cli_args,
            input=combined_message,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_resolve_api_timeout(),
            check=False,
            **_no_window_kwargs(),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:500]}"
            )

        envelope = _claude_cli_envelope(proc.stdout)

        raw_content = envelope.get("result", "")
        result = _parse_llm_json(raw_content or "{}")
        usage = envelope.get("usage") or {}
        result["input_tokens"] = (
            int(usage.get("input_tokens", 0) or 0)
            + int(usage.get("cache_read_input_tokens", 0) or 0)
            + int(usage.get("cache_creation_input_tokens", 0) or 0)
        )
        result["output_tokens"] = int(usage.get("output_tokens", 0) or 0)
        model_usage = envelope.get("modelUsage") or {}
        result["model"] = next(iter(model_usage), "claude-code-plan")
        stop_reason = envelope.get("stop_reason", "")
        result["finish_reason"] = "length" if stop_reason == "max_tokens" else "stop"

        if _response_is_hollow(raw_content, result) and result["finish_reason"] != "length":
            print(
                "[purpory] claude-cli returned a hollow response; treating as "
                "truncation so adaptive retry can bisect the chunk.",
                file=sys.stderr,
            )
            result["finish_reason"] = "length"
        return result

    def call_raw(
        self,
        api_key: str | None,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float | None = None,
        cfg: dict[str, Any] | None = None,
        usage_out: dict[str, int] | None = None,
    ) -> str:
        import platform
        import shutil
        import subprocess

        claude_cmd = "claude"
        if platform.system() == "Windows":
            cmd_path = shutil.which("claude.cmd")
            if cmd_path:
                claude_cmd = cmd_path
            elif shutil.which("claude") is None:
                raise RuntimeError("Claude Code CLI not found on $PATH")
        elif shutil.which("claude") is None:
            raise RuntimeError("Claude Code CLI not found on $PATH")

        cli_args = [claude_cmd, "-p", "--output-format", "json", "--no-session-persistence"]

        # In call_raw, the parameter 'model' might be overridden by config default,
        # but if we passed model specifically, we honor it.
        cli_args.extend(["--model", model])

        proc = subprocess.run(
            cli_args,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_resolve_api_timeout(),
            check=False,
            **_no_window_kwargs(),
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:500]}")

        envelope = _claude_cli_envelope(proc.stdout)
        cli_usage = envelope.get("usage") or {}

        if cli_usage and usage_out is not None:
            inp = (
                (cli_usage.get("input_tokens", 0) or 0)
                + (cli_usage.get("cache_read_input_tokens", 0) or 0)
                + (cli_usage.get("cache_creation_input_tokens", 0) or 0)
            )
            out = cli_usage.get("output_tokens", 0) or 0
            usage_out["input"] = usage_out.get("input", 0) + int(inp)
            usage_out["output"] = usage_out.get("output", 0) + int(out)

        return envelope.get("result", "")
