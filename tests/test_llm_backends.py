from __future__ import annotations

from pathlib import Path

import pytest

from purpory import llm


def _clear_backend_env(monkeypatch) -> None:
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "MOONSHOT_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(
    ("key", "backend"),
    (
        ("GEMINI_API_KEY", "gemini"),
        ("ANTHROPIC_API_KEY", "claude"),
        ("OPENAI_API_KEY", "openai"),
        ("DEEPSEEK_API_KEY", "deepseek"),
    ),
)
def test_detects_builtin_provider_from_environment(monkeypatch, key: str, backend: str) -> None:
    _clear_backend_env(monkeypatch)
    monkeypatch.setenv(key, "test-key")

    assert llm.detect_backend() == backend


def test_extract_dispatches_through_provider(tmp_path: Path, monkeypatch) -> None:
    _clear_backend_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    source = tmp_path / "note.md"
    source.write_text("# Architecture\n", encoding="utf-8")
    expected = {"nodes": [], "edges": [], "hyperedges": []}
    received = {}

    class Provider:
        def call_direct(self, **kwargs):
            received.update(kwargs)
            return expected

    monkeypatch.setattr("purpory.llm.providers.get_provider", lambda backend: Provider())

    assert llm.extract_files_direct([source], backend="gemini", root=tmp_path) is expected
    assert received["api_key"] == "google-key"
    assert received["model"] == "gemini-3-flash-preview"
    assert received["max_tokens"] == 16_384
    assert received["cfg"]["name"] == "gemini"


def test_raw_completion_dispatches_through_provider(monkeypatch) -> None:
    _clear_backend_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    received = {}

    class Provider:
        def call_raw(self, **kwargs):
            received.update(kwargs)
            return "answer"

    monkeypatch.setattr("purpory.llm.providers.get_provider", lambda backend: Provider())

    assert llm._call_llm("question", backend="openai") == "answer"
    assert received["prompt"] == "question"
    assert received["cfg"]["name"] == "openai"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown backend"):
        llm.extract_files_direct([], backend="custom-provider")
