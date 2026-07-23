"""Qwen 3.5 gate adapter for schema-capable OpenAI-compatible runtimes."""

from __future__ import annotations

import json
import os
import time
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from typing import Any
from urllib.parse import urlsplit

from purpory.supervise.gate.contract import (
    GATE_RESPONSE_SCHEMA,
    PROMPT_VERSION,
    GateProposal,
    GateRequest,
    ProviderResult,
)
from purpory.supervise.gate.provider import GateProviderError

DEFAULT_MODEL = "Qwen/Qwen3.5-0.8B"
DEFAULT_TIMEOUT_SECONDS = 2.0
MAX_RESPONSE_BYTES = 65_536

SYSTEM_PROMPT = """You are Purpory's memory gate. Decide only whether the current request:
- skip: can be handled from the current conversation without durable user/project memory;
- search: should search durable human decisions, code context, or prior session history;
- ask: lacks required user input that memory search cannot supply.

Never answer the request. Never invent topic keys. Preserve useful Korean terms and add concise
English code-vocabulary translations to keywords when a multilingual request needs code search.
Keywords are non-authoritative search hints, never node identifiers.
Use search whenever the request refers to prior choices, user preferences, project-specific facts,
code outside the supplied conversation, or earlier sessions. Return exactly the required schema.
Prompt contract: %s.""" % PROMPT_VERSION


def _is_loopback(hostname: str | None) -> bool:
    return hostname in {"127.0.0.1", "localhost", "::1"}


class QwenGateProvider:
    """Call a warm, local Qwen server without importing an LLM runtime."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str = DEFAULT_MODEL,
        model_revision: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        allow_remote: bool = False,
    ) -> None:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            endpoint = normalized
        elif normalized.endswith("/v1"):
            endpoint = normalized + "/chat/completions"
        else:
            endpoint = normalized + "/v1/chat/completions"
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("gate URL must be an absolute HTTP(S) URL")
        if not allow_remote and not _is_loopback(parsed.hostname):
            raise ValueError(
                "remote gate URLs are disabled; set PURPORY_GATE_ALLOW_REMOTE=1 explicitly"
            )
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("gate timeout must be between 0 and 60 seconds")
        self.endpoint = endpoint
        self._parsed_endpoint = parsed
        self.model = model.strip() or DEFAULT_MODEL
        self.model_revision = model_revision.strip() if model_revision else None
        self.api_key = api_key
        self.timeout_seconds = float(timeout_seconds)

    @classmethod
    def from_environment(cls) -> "QwenGateProvider":
        base_url = os.environ.get("PURPORY_GATE_URL", "").strip()
        if not base_url:
            raise GateProviderError("PURPORY_GATE_URL is not configured")
        timeout_raw = os.environ.get("PURPORY_GATE_TIMEOUT", "2")
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise GateProviderError("PURPORY_GATE_TIMEOUT must be a number") from exc
        return cls(
            base_url=base_url,
            model=os.environ.get("PURPORY_GATE_MODEL", DEFAULT_MODEL),
            model_revision=os.environ.get("PURPORY_GATE_MODEL_REVISION"),
            api_key=os.environ.get("PURPORY_GATE_API_KEY"),
            timeout_seconds=timeout,
            allow_remote=os.environ.get("PURPORY_GATE_ALLOW_REMOTE") == "1",
        )

    def propose(self, request: GateRequest) -> ProviderResult:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        request.model_payload(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 96,
            "response_format": {
                "type": "json_schema",
                "json_schema": GATE_RESPONSE_SCHEMA,
            },
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        encoded_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        target = self._parsed_endpoint.path or "/"
        if self._parsed_endpoint.query:
            target += "?" + self._parsed_endpoint.query
        started = time.monotonic()
        connection = self._connection()
        try:
            connection.request("POST", target, body=encoded_body, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if response.status >= 400:
                raise GateProviderError(f"gate server returned HTTP {response.status}")
        except (TimeoutError, HTTPException, OSError) as exc:
            raise GateProviderError(f"gate server unavailable: {exc}") from exc
        finally:
            connection.close()
        latency_ms = max(0, round((time.monotonic() - started) * 1_000))
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GateProviderError("gate response exceeds size limit")
        try:
            payload = json.loads(raw)
            content = _message_content(payload)
            proposal_payload = json.loads(content) if isinstance(content, str) else content
            if not isinstance(proposal_payload, dict):
                raise ValueError("gate content must be a JSON object")
            proposal = GateProposal.from_mapping(proposal_payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GateProviderError(f"invalid constrained gate response: {exc}") from exc
        return ProviderResult(
            proposal=proposal,
            model_id=self.model,
            model_revision=self.model_revision,
            latency_ms=latency_ms,
        )

    def _connection(self) -> HTTPConnection:
        hostname = self._parsed_endpoint.hostname
        if hostname is None:
            raise GateProviderError("gate URL has no hostname")
        if self._parsed_endpoint.scheme == "https":
            return HTTPSConnection(
                hostname,
                port=self._parsed_endpoint.port,
                timeout=self.timeout_seconds,
            )
        return HTTPConnection(
            hostname,
            port=self._parsed_endpoint.port,
            timeout=self.timeout_seconds,
        )


def _message_content(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise ValueError("gate response must be an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("gate response contains no choices")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ValueError("gate response contains no message")
    content = choice["message"].get("content")
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        ]
        content = "".join(str(text) for text in texts)
    if not isinstance(content, (str, dict)):
        raise ValueError("gate response content is missing")
    return content
