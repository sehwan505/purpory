from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from purpory.supervise.embeddings import EmbeddingService
from purpory.supervise.cli import dispatch_product_command
from purpory.supervise.gate.contract import GateProposal, GateRequest, ProviderResult
from purpory.supervise.library import ContextService
from purpory.supervise.provisioning import ContextProvisioningService
from purpory.supervise.repository import ContextGraphRepository


class _Provider:
    name = "test"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str], *, model: str, dimensions: int) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(index) for index in range(dimensions)] for _ in texts]


class _SearchGateProvider:
    def propose(self, request: GateRequest) -> ProviderResult:
        return ProviderResult(
            proposal=GateProposal.from_mapping(
                {
                    "action": "search",
                    "query": request.message,
                    "scopes": ["human"],
                    "keywords": [],
                    "reasonCode": "PROJECT_CONTEXT_REQUIRED",
                    "clarification": None,
                }
            ),
            model_id="test/gate",
            model_revision="test",
            latency_ms=0,
        )


def _memory(
    repository: ContextGraphRepository,
    key: str,
    value: str,
    *,
    project: str = "project",
) -> str:
    repository.set_topic(key, value=value, project=project)
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT id FROM context_nodes WHERE namespace = 'memory' AND project = ? AND stable_key = ?",
            (project, key),
        ).fetchone()
    return str(row["id"])


def test_only_expand_seeds_and_successful_deliveries_become_targets(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    node_id = _memory(repository, "knowledge.alpha", "alpha retrieval evidence")
    provisioner = ContextProvisioningService(
        repository=repository,
        root=tmp_path,
        graph_project="project",
        project="project",
    )

    assert provisioner.search("alpha", session_id="s")["candidates"]
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM embedding_targets").fetchone()[0] == 0

    provisioner.expand([node_id], depth=0)
    with repository.connect() as connection:
        target = connection.execute(
            "SELECT priority, reason FROM embedding_targets WHERE node_id = ?", (node_id,)
        ).fetchone()
    assert dict(target) == {"priority": 60, "reason": "expanded"}

    provisioner.deliver([node_id], session_id="s")
    with repository.connect() as connection:
        target = connection.execute(
            "SELECT priority, reason FROM embedding_targets WHERE node_id = ?", (node_id,)
        ).fetchone()
    assert dict(target) == {"priority": 100, "reason": "delivered"}


def test_embedding_batch_refreshes_stale_content_and_keeps_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PURPORY_EMBEDDING_DIMENSIONS", "3")
    repository = ContextGraphRepository(tmp_path / "context.db")
    node_id = _memory(repository, "knowledge.alpha", "first value")
    repository.record_embedding_targets([node_id], reason="delivered")
    provider = _Provider()
    service = EmbeddingService(repository, provider=provider)

    assert service.run()["processed"] == 1
    assert service.run()["processed"] == 0
    with repository.connect() as connection:
        vector_bytes = connection.execute(
            "SELECT LENGTH(vector) FROM node_embeddings WHERE profile_id = ? AND node_id = ?",
            (service.profile_id, node_id),
        ).fetchone()[0]
    assert vector_bytes == 12

    repository.set_topic("knowledge.alpha", value="changed value", project="project")
    assert service.run()["processed"] == 1

    monkeypatch.setenv("PURPORY_EMBEDDING_MODEL", "another-embedding-model")
    replacement = EmbeddingService(repository, provider=provider)
    assert replacement.profile_id != service.profile_id
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM embedding_profiles").fetchone()[0] == 2


def test_vector_budget_evicts_vectors_but_preserves_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PURPORY_EMBEDDING_DIMENSIONS", "3")
    monkeypatch.setenv("PURPORY_EMBEDDING_MAX_BYTES", "1")
    repository = ContextGraphRepository(tmp_path / "context.db")
    node_id = _memory(repository, "knowledge.alpha", "alpha")
    repository.record_embedding_targets([node_id], reason="delivered")

    result = EmbeddingService(repository, provider=_Provider()).run()

    assert result["evicted"] == 1
    assert result["targets"] == 1
    assert result["embedded"] == 0


def test_embedding_status_cli_never_calls_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "purpory.supervise.embeddings.OllamaEmbeddingProvider.embed",
        lambda *args, **kwargs: pytest.fail("status must not invoke Ollama"),
    )

    dispatch_product_command(
        "embed",
        ["--db", str(tmp_path / "context.db"), "--root", str(tmp_path), "--status", "--json"],
    )

    result = json.loads(capsys.readouterr().out)
    assert result["targets"] == 0
    assert result["model"] == "qwen3-embedding:0.6b"


def test_search_fuses_lexical_and_semantic_results_and_keeps_fts_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PURPORY_EMBEDDING_DIMENSIONS", "2")
    repository = ContextGraphRepository(tmp_path / "context.db")
    lexical_id = _memory(repository, "knowledge.lexical", "billing exact keyword")
    semantic_id = _memory(
        repository,
        "knowledge.semantic",
        "payments authorization rationale",
    )
    hidden_id = _memory(
        repository,
        "knowledge.hidden",
        "payments authorization from another project",
        project="other-project",
    )
    repository.record_embedding_targets([lexical_id, semantic_id, hidden_id], reason="delivered")

    class Provider:
        name = "ollama"

        def embed(self, texts: Sequence[str], *, model: str, dimensions: int) -> list[list[float]]:
            assert dimensions == 2
            return [
                [0.0, 1.0] if text == "billing" or "payments authorization" in text else [1.0, 0.1]
                for text in texts
            ]

    provider = Provider()
    EmbeddingService(repository, provider=provider).run()
    monkeypatch.setattr(
        "purpory.supervise.embeddings.OllamaEmbeddingProvider.embed",
        lambda self, texts, *, model, dimensions: provider.embed(
            texts, model=model, dimensions=dimensions
        ),
    )
    provisioner = ContextProvisioningService(
        repository=repository,
        root=tmp_path,
        graph_project="project",
        project="project",
    )

    result = provisioner.search("billing", session_id="s", scopes=["human"], connect=False)

    assert [candidate["nodeId"] for candidate in result["candidates"]] == [
        lexical_id,
        semantic_id,
    ]
    assert hidden_id not in {candidate["nodeId"] for candidate in result["candidates"]}
    lexical, semantic = result["candidates"]
    assert lexical["retrievalRanks"] == {"lexical": 1, "semantic": 2}
    assert lexical["score"] > semantic["score"]
    assert semantic["matchedTerms"] == []
    assert semantic["retrievalRanks"] == {"semantic": 1}
    assert result["fusion"] == {
        "method": "rrf",
        "k": 60,
        "sources": {"lexical": 1, "semantic": 2, "activePath": 0},
        "semanticFailed": False,
    }

    monkeypatch.setattr(
        "purpory.supervise.embeddings.OllamaEmbeddingProvider.embed",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    fallback = provisioner.search("billing", session_id="fallback", scopes=["human"], connect=False)
    assert [candidate["nodeId"] for candidate in fallback["candidates"]] == [lexical_id]
    assert fallback["fusion"]["semanticFailed"] is True


def test_delivered_memory_becomes_retrievable_by_semantic_paraphrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PURPORY_EMBEDDING_DIMENSIONS", "2")
    service = ContextService(
        db_path=tmp_path / "context.db",
        root=tmp_path,
        gate_provider=_SearchGateProvider(),
    )
    service.set_topic(
        "knowledge.cuj-semantic",
        value="All production payments require two-person authorization before release.",
    )

    first = service.prepare("production payments authorization", session_id="lexical")
    assert [item["key"] for item in first["delivery"]] == ["knowledge.cuj-semantic"]

    provider = _Provider()
    provider.name = "ollama"
    assert EmbeddingService(service.repository, provider=provider).run()["processed"] == 1
    monkeypatch.setattr(
        "purpory.supervise.embeddings.OllamaEmbeddingProvider.embed",
        lambda self, texts, *, model, dimensions: provider.embed(
            texts, model=model, dimensions=dimensions
        ),
    )

    second = service.prepare(
        "Which safeguard governs disbursement of corporate funds?",
        session_id="semantic",
    )

    candidate = second["context"]["search"]["candidates"][0]
    assert [item["key"] for item in second["delivery"]] == ["knowledge.cuj-semantic"]
    assert candidate["matchedTerms"] == []
    assert candidate["retrievalRanks"] == {"semantic": 1}
