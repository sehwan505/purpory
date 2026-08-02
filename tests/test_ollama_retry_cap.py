"""#1686 - a wedged local Ollama request must not multiply --api-timeout by the
SDK's 6 transient-error retries into a ~20min block. Ollama defaults to 0 SDK
retries so the timeout is the effective wall-clock bound; an explicit
PURPORY_MAX_RETRIES still wins.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

try:
    import openai
except ImportError:
    openai = None

pytestmark = pytest.mark.skipif(openai is None, reason="openai library is not installed")

from purpory.llm.providers import get_provider


def _call_openai(base_url: str, api_key: str, model: str, message: str, backend: str) -> None:
    get_provider(backend).call_direct(
        api_key,
        model,
        message,
        8192,
        None,
        cfg={"name": backend, "base_url": base_url},
    )


def _capture_client_kwargs(monkeypatch):
    captured: dict = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        client = MagicMock()
        resp = MagicMock()
        resp.choices[0].message.content = '{"nodes": [], "edges": [], "hyperedges": []}'
        resp.choices[0].finish_reason = "stop"
        resp.usage.prompt_tokens = 1
        resp.usage.completion_tokens = 1
        client.chat.completions.create.return_value = resp
        return client

    monkeypatch.setattr("openai.OpenAI", _factory)
    return captured


def test_ollama_defaults_to_zero_sdk_retries(monkeypatch):
    monkeypatch.delenv("PURPORY_MAX_RETRIES", raising=False)
    captured = _capture_client_kwargs(monkeypatch)
    _call_openai("http://localhost:11434/v1", "ollama", "m", "def f(): pass", "ollama")
    assert captured.get("max_retries") == 0


def test_ollama_honors_explicit_max_retries(monkeypatch):
    monkeypatch.setenv("PURPORY_MAX_RETRIES", "3")
    captured = _capture_client_kwargs(monkeypatch)
    _call_openai("http://localhost:11434/v1", "ollama", "m", "def f(): pass", "ollama")
    assert captured.get("max_retries") == 3


def test_cloud_backend_keeps_default_retries(monkeypatch):
    monkeypatch.delenv("PURPORY_MAX_RETRIES", raising=False)
    captured = _capture_client_kwargs(monkeypatch)
    _call_openai("https://api.moonshot.cn/v1", "sk-x", "m", "def f(): pass", "kimi")
    assert captured.get("max_retries") == 6  # default retained for rate-limited clouds


def test_api_timeout_is_passed_to_client(monkeypatch):
    monkeypatch.setenv("PURPORY_API_TIMEOUT", "180")
    captured = _capture_client_kwargs(monkeypatch)
    _call_openai("http://localhost:11434/v1", "ollama", "m", "def f(): pass", "ollama")
    assert captured.get("timeout") == 180.0
