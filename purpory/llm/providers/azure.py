from __future__ import annotations
import os
import sys
from typing import Any
from purpory.llm.providers.base import BaseLLMProvider
from purpory.llm.helpers import (
    _extraction_system,
    _resolve_api_timeout,
    _resolve_max_retries,
    _parse_llm_json,
    _response_is_hollow,
    _resolve_temperature,
)

def _azure_client(api_key: str, endpoint: str):
    """Construct an AzureOpenAI client with env-driven api_version and timeout."""
    try:
        from openai import AzureOpenAI
    except ImportError as exc:
        raise ImportError(
            "Azure OpenAI requires the openai package. Run: pip install openai"
        ) from exc
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip()
    timeout_raw = os.environ.get("PURPORY_API_TIMEOUT", "").strip()
    timeout_s: float = 600.0
    if timeout_raw:
        try:
            v = float(timeout_raw)
            if v > 0:
                timeout_s = v
        except ValueError:
            pass
    return AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
        timeout=timeout_s,
        max_retries=_resolve_max_retries(),
    )

class AzureProvider(BaseLLMProvider):
    """Adapter for Azure OpenAI Service provider."""

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
        endpoint = (cfg.get("endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")).strip() if cfg else os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        if not endpoint:
            raise ValueError(
                "Azure OpenAI backend requires AZURE_OPENAI_ENDPOINT to be set "
                "(e.g. https://my-resource.openai.azure.com/)."
            )

        client = _azure_client(api_key or "", endpoint)
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": _extraction_system(deep=deep_mode)},
                {"role": "user", "content": user_message},
            ],
            "max_completion_tokens": max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        resp = client.chat.completions.create(**kwargs)
        if not resp.choices or resp.choices[0].message is None:
            raise ValueError("Azure OpenAI returned empty or filtered response")
        raw_content = resp.choices[0].message.content
        result = _parse_llm_json(raw_content or "{}")
        result["input_tokens"] = resp.usage.prompt_tokens if resp.usage else 0
        result["output_tokens"] = resp.usage.completion_tokens if resp.usage else 0
        result["model"] = model
        result["finish_reason"] = resp.choices[0].finish_reason

        if _response_is_hollow(raw_content, result) and result["finish_reason"] != "length":
            print(
                "[purpory] azure returned a hollow response; treating as "
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
        endpoint = (cfg.get("endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")).strip() if cfg else os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        if not endpoint:
            raise ValueError(
                "Azure OpenAI backend requires AZURE_OPENAI_ENDPOINT to be set."
            )
        client = _azure_client(api_key or "", endpoint)
        azure_kwargs: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": max_tokens,
        }
        if temperature is not None:
            azure_kwargs["temperature"] = temperature

        resp = client.chat.completions.create(**azure_kwargs)
        if not resp.choices or resp.choices[0].message is None:
            raise ValueError("Azure OpenAI returned empty or filtered response")

        au = getattr(resp, "usage", None)
        if au is not None and usage_out is not None:
            usage_out["input"] = usage_out.get("input", 0) + int(getattr(au, "prompt_tokens", 0) or 0)
            usage_out["output"] = usage_out.get("output", 0) + int(getattr(au, "completion_tokens", 0) or 0)

        return resp.choices[0].message.content or ""
