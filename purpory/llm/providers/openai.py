from __future__ import annotations
import os
import sys
from typing import Any
from purpory.llm.providers.base import BaseLLMProvider
from purpory.llm.helpers import (
    _extraction_system,
    _openai_content,
    _resolve_max_retries,
    _resolve_api_timeout,
    _thinking_disabled_via_env,
    _parse_llm_json,
    _response_is_hollow,
    _validate_ollama_base_url,
    _CHARS_PER_TOKEN,
    _backend_pkg_hint,
    _resolve_temperature,
    _resolve_max_tokens,
)

class OpenAICompatProvider(BaseLLMProvider):
    """Adapter for OpenAI-compatible API providers."""

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
        try:
            from openai import OpenAI
        except ImportError as exc:
            backend = cfg.get("name", "") if cfg else ""
            extra = backend if backend in ("kimi", "gemini", "openai", "ollama") else "openai"
            raise ImportError(_backend_pkg_hint("openai", extra)) from exc

        base_url = cfg.get("base_url", "") if cfg else ""
        backend = cfg.get("name", "") if cfg else ""
        reasoning_effort = cfg.get("reasoning_effort") if cfg else None
        extra_body = cfg.get("extra_body") if cfg else None

        _retries = _resolve_max_retries()
        if backend == "ollama" and not os.environ.get("PURPORY_MAX_RETRIES", "").strip():
            _retries = 0

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=_resolve_api_timeout(),
            max_retries=_retries,
        )

        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": _extraction_system(deep=deep_mode)},
                {"role": "user", "content": _openai_content(user_message, images or [])},
            ],
            "max_completion_tokens": max_tokens,
            "stream": False,
        }

        if temperature is not None:
            kwargs["temperature"] = temperature
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        elif "moonshot" in base_url:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        elif _thinking_disabled_via_env():
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        if backend == "ollama" and extra_body is None:
            num_ctx_raw = os.environ.get("PURPORY_OLLAMA_NUM_CTX", "").strip()
            estimated_input = len(user_message) // _CHARS_PER_TOKEN + 400
            auto_num_ctx = min(estimated_input + max_tokens + 2000, 131072)
            auto_num_ctx = max(auto_num_ctx, 8192)
            if num_ctx_raw:
                try:
                    num_ctx = int(num_ctx_raw)
                except ValueError:
                    print(
                        f"[purpory] PURPORY_OLLAMA_NUM_CTX={num_ctx_raw!r} is not a valid integer; "
                        f"using auto-derived value ({auto_num_ctx}).",
                        file=sys.stderr,
                    )
                    num_ctx = auto_num_ctx
                else:
                    if num_ctx < estimated_input:
                        print(
                            f"[purpory] warning: PURPORY_OLLAMA_NUM_CTX={num_ctx} is smaller than "
                            f"the estimated chunk input (~{estimated_input} tokens). Ollama will "
                            f"silently truncate the prompt and return empty responses. "
                            f"Try --token-budget {max(1024, num_ctx // 3)} or increase NUM_CTX.",
                            file=sys.stderr,
                        )
            else:
                num_ctx = auto_num_ctx
            keep_alive = os.environ.get("PURPORY_OLLAMA_KEEP_ALIVE", "30m")
            kwargs["extra_body"] = {"options": {"num_ctx": num_ctx}, "keep_alive": keep_alive}

        resp = client.chat.completions.create(**kwargs)
        if not resp.choices or resp.choices[0].message is None:
            raise ValueError("LLM returned empty or filtered response")
        raw_content = resp.choices[0].message.content
        result = _parse_llm_json(raw_content or "{}")
        result["input_tokens"] = resp.usage.prompt_tokens if resp.usage else 0
        result["output_tokens"] = resp.usage.completion_tokens if resp.usage else 0
        result["model"] = model
        result["finish_reason"] = resp.choices[0].finish_reason

        if _response_is_hollow(raw_content, result) and result["finish_reason"] != "length":
            print(
                f"[purpory] {backend or 'backend'} returned a hollow response "
                f"(content={'empty' if not (raw_content or '').strip() else 'no nodes/edges'}, "
                f"output_tokens={result['output_tokens']}); "
                "treating as truncation so adaptive retry can bisect the chunk.",
                file=sys.stderr,
            )
            result["finish_reason"] = "length"

        output_tokens = result["output_tokens"]
        if output_tokens < 50 and backend == "ollama":
            print(
                "[purpory] warning: ollama returned very few tokens — likely causes: "
                "(1) VRAM pressure: check `nvidia-smi` and reduce chunk size with "
                "--token-budget (e.g. --token-budget 4096) or set "
                "PURPORY_OLLAMA_NUM_CTX to a smaller value; "
                "(2) model too small for JSON instruction following — "
                "try a larger model with --model (e.g. --model qwen2.5-coder:14b).",
                file=sys.stderr,
            )
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
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(_backend_pkg_hint("openai", "openai")) from exc

        base_url = cfg.get("base_url", "") if cfg else ""
        reasoning_effort = cfg.get("reasoning_effort") if cfg else None
        extra_body = cfg.get("extra_body") if cfg else None

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=_resolve_api_timeout(),
            max_retries=_resolve_max_retries(),
        )

        kwargs: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": max_tokens,
            "stream": False,
        }

        if temperature is not None:
            kwargs["temperature"] = temperature
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        elif "moonshot" in base_url:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        elif _thinking_disabled_via_env():
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        resp = client.chat.completions.create(**kwargs)
        if not resp.choices or resp.choices[0].message is None:
            raise ValueError("LLM returned empty or filtered response")

        ou = getattr(resp, "usage", None)
        if ou is not None and usage_out is not None:
            usage_out["input"] = usage_out.get("input", 0) + int(getattr(ou, "prompt_tokens", 0) or 0)
            usage_out["output"] = usage_out.get("output", 0) + int(getattr(ou, "completion_tokens", 0) or 0)

        return resp.choices[0].message.content or ""
