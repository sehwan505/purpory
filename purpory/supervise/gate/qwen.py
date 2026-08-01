"""Qwen 3.5 gate adapter for OpenAI-compatible runtimes."""

from __future__ import annotations

import json
import os
import time
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from purpory.supervise.gate.contract import (
    MODEL_ACTIONS,
    PROMPT_VERSION,
    GateProposal,
    GateRequest,
    ProviderResult,
)
from purpory.supervise.gate.provider import GateProviderError

DEFAULT_MODEL = "Qwen/Qwen3.5-0.8B"
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_MAX_INPUT_TOKENS = 20_000
DEFAULT_MAX_CONTEXT_TOKENS = 262_144
MAX_RESPONSE_TOKENS = 8
MAX_RESPONSE_BYTES = 65_536

SYSTEM_PROMPT = """You are Purpory's memory gate classifier. Decide only whether the current request:
- skip: general conversation, direct questions, or requests answered without durable project memory;
- search: should search durable human decisions, registered resources, code context, or prior session history;
- ask: ONLY when a specific modification/action lacks essential target specifications. Purpory will still search memory before asking the user.

Never classify general questions, conversation, or inquiries about purpory as ASK.
Questions about this project's goal, intent, decisions, history, implementation, or current state are SEARCH.

OUTPUT CONTRACT (mandatory):
- Reply with exactly one word and nothing else: SKIP, SEARCH, or ASK.
- Do not return JSON, punctuation, prose, or a Markdown code fence.

Examples:
- "안녕하세요" -> SKIP
- "gate model이 비활성화되었을 때 fallback이 있어?" -> SKIP
- "전에 정한 인증 정책을 찾아줘" -> SEARCH
- "Purpory의 궁극적인 목표가 뭐야?" -> SEARCH
- "현재 코드의 gate parser를 확인해줘" -> SEARCH
- "이걸 배포해줘" when no target is supplied -> ASK

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
        tokenizer_path: str | Path | None = None,
        max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
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
        if max_context_tokens <= MAX_RESPONSE_TOKENS:
            raise ValueError(
                f"gate context limit must exceed {MAX_RESPONSE_TOKENS} tokens"
            )
        if max_input_tokens <= 0 or max_input_tokens + MAX_RESPONSE_TOKENS > max_context_tokens:
            raise ValueError(
                "gate input limit must be positive and fit within the model context limit"
            )
        self.endpoint = endpoint
        self._parsed_endpoint = parsed
        self.model = model.strip() or DEFAULT_MODEL
        self.model_revision = model_revision.strip() if model_revision else None
        self.api_key = api_key
        self.timeout_seconds = float(timeout_seconds)
        self.tokenizer_path = (
            Path(tokenizer_path).expanduser().resolve()
            if tokenizer_path is not None
            else None
        )
        self.max_input_tokens = int(max_input_tokens)
        self.max_context_tokens = int(max_context_tokens)
        self._tokenizer: Any | None = None

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
        limit_reason = self.input_limit_reason(request)
        if limit_reason is not None:
            raise GateProviderError(limit_reason)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    # The classifier needs the request itself, not the full
                    # context catalog. Oversized requests bypass this provider
                    # in the gateway instead of being silently transformed.
                    "content": request.message,
                },
            ],
            # transformers serve 5.14 ignores OpenAI's response_format field,
            # so the local 0.8B model performs one bounded classification. The
            # adapter deterministically expands that classification into the
            # richer internal proposal contract.
            "max_tokens": MAX_RESPONSE_TOKENS,
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
            action = _classified_action(content)
            proposal = _proposal_for_action(action, request)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GateProviderError(f"invalid gate classifier response: {exc}") from exc
        return ProviderResult(
            proposal=proposal,
            model_id=self.model,
            model_revision=self.model_revision,
            latency_ms=latency_ms,
        )

    def input_limit_reason(self, request: GateRequest) -> str | None:
        """Return why this request cannot fit, without altering its message."""
        if self.tokenizer_path is None:
            return None
        prompt_tokens = self._count_prompt_tokens(request.message)
        if prompt_tokens > self.max_input_tokens:
            return (
                f"gate prompt requires {prompt_tokens} tokens, "
                f"exceeding operating limit {self.max_input_tokens}"
            )
        required_tokens = prompt_tokens + MAX_RESPONSE_TOKENS
        if required_tokens <= self.max_context_tokens:
            return None
        return (
            f"gate request requires {required_tokens} tokens "
            f"(prompt {prompt_tokens} + output {MAX_RESPONSE_TOKENS}), "
            f"exceeding model context limit {self.max_context_tokens}"
        )

    def _count_prompt_tokens(self, message: str) -> int:
        path = self.tokenizer_path
        if path is None:
            raise GateProviderError("gate tokenizer is not configured")
        if self._tokenizer is None:
            tokenizer_file = path / "tokenizer.json" if path.is_dir() else path
            if not tokenizer_file.is_file():
                raise GateProviderError(f"gate tokenizer is missing: {tokenizer_file}")
            try:
                tokenizer_type = import_module("tokenizers").Tokenizer
                self._tokenizer = tokenizer_type.from_file(str(tokenizer_file))
            except (AttributeError, ImportError, OSError, ValueError) as exc:
                raise GateProviderError(f"could not load gate tokenizer: {exc}") from exc
        tokenizer = self._tokenizer
        if tokenizer is None:
            raise GateProviderError("gate tokenizer could not be initialized")
        rendered = (
            f"<|im_start|>system\n{SYSTEM_PROMPT.strip()}<|im_end|>\n"
            f"<|im_start|>user\n{message.strip()}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n"
        )
        try:
            return len(tokenizer.encode(rendered, add_special_tokens=False).ids)
        except (AttributeError, TypeError, ValueError) as exc:
            raise GateProviderError(f"could not count gate input tokens: {exc}") from exc

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


def _classified_action(content: Any) -> str:
    if not isinstance(content, str):
        raise ValueError("gate classifier content must be text")
    action = content.strip().lower()
    if action not in MODEL_ACTIONS:
        raise ValueError("expected exactly one of SKIP, SEARCH, or ASK")
    return action


def _proposal_for_action(action: str, request: GateRequest) -> GateProposal:
    if action == "skip":
        payload = {
            "action": "skip",
            "query": None,
            "scopes": [],
            "keywords": [],
            "reasonCode": "SELF_CONTAINED",
            "clarification": None,
        }
    elif action == "search":
        payload = {
            "action": "search",
            "query": request.message,
            "scopes": ["human", "resource", "code", "session"],
            "keywords": [],
            "reasonCode": "CONTEXT_SEARCH_REQUIRED",
            "clarification": None,
        }
    elif action == "ask":
        payload = {
            "action": "ask",
            "query": None,
            "scopes": [],
            "keywords": [],
            "reasonCode": "USER_INPUT_REQUIRED",
            "clarification": (
                "이 요청을 진행하는 데 필요한 대상과 기대 결과를 구체적으로 알려주세요."
            ),
        }
    else:
        raise ValueError(f"unsupported classified gate action: {action}")
    return GateProposal.from_mapping(payload)
