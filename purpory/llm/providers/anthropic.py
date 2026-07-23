from __future__ import annotations
import sys
from typing import Any
from purpory.llm.providers.base import BaseLLMProvider
from purpory.llm.helpers import (
    _extraction_system,
    _anthropic_content,
    _resolve_api_timeout,
    _resolve_max_retries,
    _parse_llm_json,
    _response_is_hollow,
    _backend_pkg_hint,
)

class AnthropicProvider(BaseLLMProvider):
    """Adapter for Anthropic Claude provider."""

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
            import anthropic
        except ImportError as exc:
            raise ImportError(_backend_pkg_hint("anthropic", "anthropic")) from exc

        base_url = cfg.get("base_url", "https://api.anthropic.com") if cfg else "https://api.anthropic.com"
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=_resolve_api_timeout(),
            max_retries=_resolve_max_retries(),
        )

        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_extraction_system(deep=deep_mode),
            messages=[{"role": "user", "content": _anthropic_content(user_message, images or [])}],
        )

        raw_content = resp.content[0].text if resp.content else None
        result = _parse_llm_json(raw_content or "{}")
        result["input_tokens"] = resp.usage.input_tokens if resp.usage else 0
        result["output_tokens"] = resp.usage.output_tokens if resp.usage else 0
        result["model"] = model
        result["finish_reason"] = "length" if resp.stop_reason == "max_tokens" else "stop"

        if _response_is_hollow(raw_content, result) and result["finish_reason"] != "length":
            print(
                "[purpory] claude returned a hollow response; treating as "
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
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(_backend_pkg_hint("anthropic", "anthropic")) from exc

        base_url = cfg.get("base_url", "https://api.anthropic.com") if cfg else "https://api.anthropic.com"
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=_resolve_api_timeout(),
            max_retries=_resolve_max_retries(),
        )

        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        u = getattr(resp, "usage", None)
        if u is not None and usage_out is not None:
            usage_out["input"] = usage_out.get("input", 0) + int(getattr(u, "input_tokens", 0) or 0)
            usage_out["output"] = usage_out.get("output", 0) + int(getattr(u, "output_tokens", 0) or 0)

        return resp.content[0].text if resp.content else ""
