from __future__ import annotations
import os
import sys
from typing import Any
from purpory.llm.providers.base import BaseLLMProvider
from purpory.llm.helpers import (
    _extraction_system,
    _bedrock_content,
    _bedrock_inference_config,
    _parse_llm_json,
    _response_is_hollow,
    _backend_pkg_hint,
)

class BedrockProvider(BaseLLMProvider):
    """Adapter for AWS Bedrock Converse API provider."""

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
            import boto3
            import botocore.exceptions
        except ImportError as exc:
            raise ImportError(
                "AWS Bedrock extraction requires boto3. Run: pip install purpory[bedrock]"
            ) from exc

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        profile = os.environ.get("AWS_PROFILE")
        session = boto3.Session(profile_name=profile, region_name=region)
        client = session.client("bedrock-runtime")

        try:
            resp = client.converse(
                modelId=model,
                system=[{"text": _extraction_system(deep=deep_mode)}],
                messages=[{"role": "user", "content": _bedrock_content(user_message, images or [])}],
                inferenceConfig=_bedrock_inference_config(max_tokens, model),
            )
        except botocore.exceptions.ClientError as exc:
            code = exc.response["Error"]["Code"]
            msg = exc.response["Error"]["Message"]
            raise RuntimeError(f"Bedrock API error ({code}): {msg}") from exc

        text = resp.get("output", {}).get("message", {}).get("content", [{}])[0].get("text", "{}")
        result = _parse_llm_json(text)
        usage = resp.get("usage", {})
        result["input_tokens"] = usage.get("inputTokens", 0)
        result["output_tokens"] = usage.get("outputTokens", 0)
        result["model"] = model
        result["finish_reason"] = "length" if resp.get("stopReason") == "max_tokens" else "stop"

        if _response_is_hollow(text, result) and result["finish_reason"] != "length":
            print(
                "[purpory] bedrock returned a hollow response; treating as "
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
            import boto3
        except ImportError as exc:
            raise ImportError(_backend_pkg_hint("boto3", "bedrock")) from exc

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        profile = os.environ.get("AWS_PROFILE")
        session = boto3.Session(profile_name=profile, region_name=region)
        client = session.client("bedrock-runtime")

        resp = client.converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig=_bedrock_inference_config(max_tokens, model),
        )

        bu = resp.get("usage") or {}
        if bu and usage_out is not None:
            usage_out["input"] = usage_out.get("input", 0) + int(bu.get("inputTokens", 0) or 0)
            usage_out["output"] = usage_out.get("output", 0) + int(bu.get("outputTokens", 0) or 0)

        return resp.get("output", {}).get("message", {}).get("content", [{}])[0].get("text", "")
