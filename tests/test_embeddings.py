from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from purpory.supervise.embeddings import EmbeddingService, search_embeddings
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


def test_remembered_memories_queue_and_use_signals_only_raise_priority(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    node_id = _memory(repository, "knowledge.alpha", "alpha retrieval evidence")
    provisioner = ContextProvisioningService(
        repository=repository,
        root=tmp_path,
        graph_project="project",
        project="project",
    )

    with repository.connect() as connection:
        target = connection.execute(
            "SELECT priority, reason FROM embedding_targets WHERE node_id = ?", (node_id,)
        ).fetchone()
    assert dict(target) == {"priority": 80, "reason": "remembered"}

    provisioner.expand([node_id], depth=0)
    with repository.connect() as connection:
        target = connection.execute(
            "SELECT priority, reason FROM embedding_targets WHERE node_id = ?", (node_id,)
        ).fetchone()
    assert dict(target) == {"priority": 80, "reason": "remembered"}

    provisioner.deliver([node_id], session_id="s")
    with repository.connect() as connection:
        target = connection.execute(
            "SELECT priority, reason FROM embedding_targets WHERE node_id = ?", (node_id,)
        ).fetchone()
    assert dict(target) == {"priority": 100, "reason": "delivered"}


def test_normal_write_keeps_the_queue_when_embedding_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "purpory.supervise.embeddings.OllamaEmbeddingProvider.embed",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path)

    service.set_topic("knowledge.alpha", value="alpha retrieval evidence")

    status = EmbeddingService(service.repository).status()
    assert status["pending"] == 1
    assert status["embedded"] == 0


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


def test_semantic_search_abstains_on_weak_similarity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PURPORY_EMBEDDING_DIMENSIONS", "2")
    repository = ContextGraphRepository(tmp_path / "context.db")
    node_id = _memory(repository, "knowledge.weak", "weak semantic candidate")
    repository.record_embedding_targets([node_id], reason="delivered")

    class Provider:
        name = "test"

        def embed(
            self, texts: Sequence[str], *, model: str, dimensions: int
        ) -> list[list[float]]:
            return [[1.0, 0.0] if text.startswith("Instruct:") else [0.5, 0.866] for text in texts]

    provider = Provider()
    EmbeddingService(repository, provider=provider).run()

    assert search_embeddings(
        repository,
        "unrelated request",
        memory_project="project",
        code_projects=[],
        resource_node_ids=[],
        include_memory=True,
        include_code=False,
        include_resources=False,
        limit=10,
        provider=provider,
    ) == []


def test_search_uses_semantic_results_without_memory_body_fts_fallback(
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
                [0.0, 1.0]
                if text.endswith("Query: billing") or "payments authorization" in text
                else [1.0, 0.1]
                for text in texts
            ]

    provider = Provider()
    EmbeddingService(repository, provider=provider).run()
    pending_id = _memory(repository, "knowledge.pending", "deployment policy")
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

    assert [candidate["nodeId"] for candidate in result["candidates"]] == [semantic_id]
    assert lexical_id not in {candidate["nodeId"] for candidate in result["candidates"]}
    assert hidden_id not in {candidate["nodeId"] for candidate in result["candidates"]}
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM node_embeddings WHERE node_id = ?", (pending_id,)
        ).fetchone()[0] == 0
    semantic = result["candidates"][0]
    assert semantic["matchedTerms"] == []
    assert semantic["retrievalRanks"] == {"semantic": 1}
    assert result["fusion"] == {
        "method": "rrf",
        "k": 60,
        "sources": {"lexical": 0, "semantic": 1, "activePath": 0},
        "semanticFailed": False,
    }

    monkeypatch.setattr(
        "purpory.supervise.embeddings.OllamaEmbeddingProvider.embed",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    fallback = provisioner.search("billing", session_id="fallback", scopes=["human"], connect=False)
    assert fallback["candidates"] == []
    assert fallback["fusion"]["semanticFailed"] is True


def test_new_memory_is_automatically_retrievable_by_semantic_paraphrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PURPORY_EMBEDDING_DIMENSIONS", "2")
    service = ContextService(
        db_path=tmp_path / "context.db",
        root=tmp_path,
        gate_provider=_SearchGateProvider(),
    )
    provider = _Provider()
    provider.name = "ollama"
    monkeypatch.setattr(
        "purpory.supervise.embeddings.OllamaEmbeddingProvider.embed",
        lambda self, texts, *, model, dimensions: provider.embed(
            texts, model=model, dimensions=dimensions
        ),
    )
    service.set_topic(
        "knowledge.cuj-semantic",
        value="All production payments require two-person authorization before release.",
    )
    assert EmbeddingService(service.repository, provider=provider).status()["pending"] == 0

    result = service.prepare(
        "Which safeguard governs disbursement of corporate funds?",
        session_id="semantic",
    )

    candidate = result["context"]["search"]["candidates"][0]
    assert [item["key"] for item in result["delivery"]] == ["knowledge.cuj-semantic"]
    assert candidate["matchedTerms"] == []
    assert candidate["retrievalRanks"] == {"semantic": 1}
    assert EmbeddingService(service.repository, provider=provider).status()["embedded"] == 1
