from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from purpory.supervise.gate.contract import GateProposal, GateRequest, ProviderResult
from purpory.ollama import ollama_urls
from purpory.supervise.gate.runtime import GateModelManager
from purpory.supervise.model_cli import dispatch_model


def test_status_reads_gate_model_from_ollama_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._models",
        lambda timeout_seconds=0.25: [
            {"name": "qwen3.5:0.8b", "digest": "sha256:gate"},
            {"name": "qwen3-embedding:0.6b", "digest": "sha256:embedding"},
        ],
    )

    status = GateModelManager().status()

    assert status["installed"] is True
    assert status["ready"] is True
    assert status["runtime"] == "ollama"
    assert status["revision"] == "sha256:gate"
    assert status["endpoint"] == "http://localhost:11434/v1"


def test_install_pulls_only_when_missing_or_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    inventory: list[dict[str, str]] = []
    requests: list[dict[str, object]] = []

    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._models",
        lambda timeout_seconds=0.25: list(inventory),
    )

    def request(method: str, path: str, *, body=None, timeout_seconds: float):
        requests.append({"method": method, "path": path, "body": body})
        inventory.append({"name": str(body["model"]), "digest": "sha256:new"})
        return {"status": "success"}

    monkeypatch.setattr("purpory.supervise.gate.runtime._request_json", request)
    manager = GateModelManager()

    assert manager.install()["action"] == "installed"
    assert manager.install()["action"] == "kept"
    assert manager.install(force=True)["action"] == "installed"
    assert requests == [
        {"method": "POST", "path": "/api/pull", "body": {"model": "qwen3.5:0.8b", "stream": False}},
        {"method": "POST", "path": "/api/pull", "body": {"model": "qwen3.5:0.8b", "stream": False}},
    ]


def test_provider_reuses_ollama_endpoint_and_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._models",
        lambda timeout_seconds=0.25: [{"name": "qwen3.5:0.8b", "digest": "sha256:gate"}],
    )

    provider = GateModelManager().provider()

    assert provider is not None
    assert provider.endpoint == "http://localhost:11434/v1/chat/completions"
    assert provider.model == "qwen3.5:0.8b"
    assert provider.model_revision == "sha256:gate"
    assert provider.tokenizer_path is None


def test_start_does_not_own_or_spawn_shared_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = GateModelManager()
    monkeypatch.setattr(
        manager,
        "status",
        lambda **kwargs: {"running": True, "installed": True, "ready": True},
    )

    assert manager.start()["action"] == "kept"
    assert manager.stop()["action"] == "external-runtime"


def test_ollama_url_must_be_local_and_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1234/v1")
    assert ollama_urls() == ("http://127.0.0.1:1234", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://example.com/v1")
    with pytest.raises(ValueError, match="loopback"):
        ollama_urls()


def test_model_cli_status_is_machine_readable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._models",
        lambda timeout_seconds=0.25: [],
    )

    dispatch_model(["status", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result["installed"] is False
    assert result["running"] is True


def test_model_cli_installs_gate_and_embedding_roles(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installed: list[str] = []

    def install(self, *, model_id: str, revision: str | None, force: bool):
        installed.append(model_id)
        return {"action": "installed", "model": model_id}

    monkeypatch.setattr("purpory.supervise.gate.runtime.GateModelManager.install", install)

    dispatch_model(["install", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert installed == ["qwen3.5:0.8b", "qwen3-embedding:0.6b"]
    assert result["action"] == "installed"


class _SkipProvider:
    def propose(self, request: GateRequest) -> ProviderResult:
        return ProviderResult(
            proposal=GateProposal.from_mapping(
                {
                    "action": "skip",
                    "query": None,
                    "scopes": [],
                    "keywords": [],
                    "reasonCode": "SELF_CONTAINED",
                    "clarification": None,
                }
            ),
            model_id="qwen3.5:0.8b",
            model_revision="test",
            latency_ms=3,
        )


def test_prepare_auto_discovers_managed_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PURPORY_GATE_URL", raising=False)
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime.GateModelManager.provider",
        lambda self, start_if_needed, start_timeout_seconds: _SkipProvider(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["purpory", "prepare", "hello", "--db", str(tmp_path / "context.db"), "--json"],
    )

    from purpory.__main__ import main

    main()

    result = json.loads(capsys.readouterr().out)
    assert result["model"]["id"] == "qwen3.5:0.8b"
    assert result["fallback"] is None
