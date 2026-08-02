from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from purpory.supervise.embeddings import EmbeddingService
from purpory.supervise.cli import dispatch_product_command
from purpory.supervise.provisioning import ContextProvisioningService
from purpory.supervise.repository import ContextGraphRepository


class _Provider:
    name = "test"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str], *, model: str, dimensions: int) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(index) for index in range(dimensions)] for _ in texts]


def _memory(repository: ContextGraphRepository, key: str, value: str) -> str:
    repository.set_topic(key, value=value, project="project")
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT id FROM context_nodes WHERE namespace = 'memory' AND project = ? AND stable_key = ?",
            ("project", key),
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
