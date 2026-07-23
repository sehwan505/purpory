from __future__ import annotations

import json
from pathlib import Path

from purpory.supervise.library import ContextService


def _service_with_graph(tmp_path: Path) -> ContextService:
    output = tmp_path / "purpory-out"
    output.mkdir()
    (output / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "auth-service",
                        "label": "AuthService",
                        "type": "class",
                        "source_file": "src/auth/service.py",
                        "community": 1,
                    },
                    {
                        "id": "token-repository",
                        "label": "TokenRepository",
                        "type": "class",
                        "source_file": "src/auth/repository.py",
                        "community": 1,
                    },
                    {
                        "id": "database-pool",
                        "label": "DatabasePool",
                        "type": "class",
                        "source_file": "src/database/pool.py",
                        "community": 2,
                    },
                ],
                "links": [
                    {
                        "source": "auth-service",
                        "target": "token-repository",
                        "relation": "calls",
                        "confidence": "EXTRACTED",
                    },
                    {
                        "source": "token-repository",
                        "target": "database-pool",
                        "relation": "uses",
                        "confidence": "EXTRACTED",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path)
    service.set_topic(
        "decision.auth.ttl",
        value="Access tokens expire after thirty minutes",
        kind="decision",
    )
    return service


def test_catalog_is_compact_and_never_copies_memory_values(tmp_path: Path) -> None:
    service = _service_with_graph(tmp_path)

    catalog = service.catalog(session_id="agent-a")

    assert catalog["counts"]["human"] == 1
    assert catalog["counts"]["code"] == 3
    assert catalog["topicNamespaces"] == [{"name": "decision", "count": 1}]
    assert catalog["graphSnapshot"]["nodeCount"] == 3
    assert "Access tokens" not in json.dumps(catalog)


def test_search_connects_distinct_concepts_through_the_context_graph(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)

    result = service.search(
        "auth database",
        session_id="agent-a",
        scopes=["code"],
        keywords=["auth", "database"],
    )

    labels = [candidate["label"] for candidate in result["candidates"]]
    assert "AuthService" in labels
    assert "DatabasePool" in labels
    assert result["connections"][0]["found"] is True
    connection_labels = [node["label"] for node in result["connections"][0]["nodes"]]
    assert connection_labels in (
        ["AuthService", "TokenRepository", "DatabasePool"],
        ["DatabasePool", "TokenRepository", "AuthService"],
    )


def test_search_uses_only_terms_present_in_the_context_vocabulary(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)

    result = service.search(
        "인증과 데이터베이스 연결",
        session_id="agent-a",
        scopes=["code"],
        keywords=["auth", "database"],
        connect=False,
    )

    assert result["terms"] == ["auth", "database"]
    assert result["ignoredTerms"] == ["인증과", "데이터베이스", "연결"]
    assert {candidate["label"] for candidate in result["candidates"]} >= {
        "AuthService",
        "DatabasePool",
    }


def test_session_scope_can_recall_previously_delivered_memory(tmp_path: Path) -> None:
    service = _service_with_graph(tmp_path)
    memory_search = service.search(
        "token expiry",
        session_id="agent-a",
        scopes=["human"],
        keywords=["token", "expire"],
        connect=False,
    )
    memory_id = memory_search["candidates"][0]["nodeId"]
    service.deliver([memory_id], session_id="agent-a", token_budget=512)

    result = service.search(
        "token",
        session_id="agent-a",
        scopes=["session"],
        connect=False,
    )

    assert result["candidates"][0]["nodeId"] == memory_id
    assert "session-recall" in result["candidates"][0]["signals"]


def test_candidate_pool_reserves_space_for_memory_and_active_paths(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)
    service.set_topic(
        "decision.shared-token",
        value="shared token policy",
        kind="decision",
    )
    service.catalog(session_id="agent-a")

    candidates = service.repository.search_retrieval_nodes(
        project=service.project_id,
        terms=["token"],
        active_paths=["src/database"],
        include_memory=True,
        include_code=True,
        limit=4,
    )

    assert len(candidates) <= 4
    assert any(node["namespace"] == "memory" for node in candidates)
    assert any(node["source"] == "src/database/pool.py" for node in candidates)


def test_search_result_covers_distinct_terms_before_filling_by_score(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)

    result = service.search(
        "auth token database",
        session_id="agent-a",
        scopes=["human", "code"],
        limit=3,
        connect=False,
    )

    assert {term for item in result["candidates"] for term in item["matchedTerms"]} == {
        "auth",
        "token",
        "database",
    }
    assert {item["namespace"] for item in result["candidates"]} == {
        "memory",
        "code",
    }


def test_expand_is_relation_filtered_and_bounded(tmp_path: Path) -> None:
    service = _service_with_graph(tmp_path)
    search = service.search("AuthService", session_id="agent-a", scopes=["code"], connect=False)
    auth_id = next(
        candidate["nodeId"]
        for candidate in search["candidates"]
        if candidate["label"] == "AuthService"
    )

    expanded = service.expand([auth_id], depth=1, relations=["calls"], node_limit=10)

    assert {node["label"] for node in expanded["nodes"]} == {
        "AuthService",
        "TokenRepository",
    }
    assert [edge["relation"] for edge in expanded["edges"]] == ["calls"]
    assert expanded["truncated"] is False


def test_deliver_records_exact_context_and_deduplicates_per_session(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)
    search = service.search("AuthService", session_id="agent-a", scopes=["code"], connect=False)
    auth_id = search["candidates"][0]["nodeId"]

    first = service.deliver([auth_id], session_id="agent-a", token_budget=512)
    second = service.deliver([auth_id], session_id="agent-a", token_budget=512)

    assert first["delivery"][0]["mode"] == "context-graph"
    assert "AuthService" in first["rendered"]
    assert first["valueHash"]
    assert second["delivery"] == []
    assert second["omitted"] == [
        {"key": first["delivery"][0]["key"], "reason": "already-delivered"}
    ]
    assert (
        service.repository.session_view()[0]["items"][0]["valueHash"]
        == first["delivery"][0]["valueHash"]
    )


def test_prepare_includes_ready_context_without_public_primitives(
    tmp_path: Path,
) -> None:
    from purpory.supervise.gate.contract import GateProposal, GateRequest, ProviderResult

    class Provider:
        def propose(self, request: GateRequest) -> ProviderResult:
            assert request.model_payload()["contextCatalog"]["counts"]["code"] == 3
            return ProviderResult(
                proposal=GateProposal.from_mapping(
                    {
                        "action": "search",
                        "query": "auth database",
                        "scopes": ["code"],
                        "keywords": ["auth", "database"],
                        "reasonCode": "CODE_CONTEXT_REQUIRED",
                        "clarification": None,
                    }
                ),
                model_id="stub/qwen",
                model_revision="test",
                latency_ms=1,
            )

    service = _service_with_graph(tmp_path)
    service.gate_provider = Provider()

    result = service.prepare(
        "인증 흐름이 DB와 어떻게 연결돼?",
        session_id="agent-a",
        token_budget=1_000,
    )

    assert result["action"] == "retrieve"
    assert result["context"]["manifest"]["counts"]["code"] == 3
    assert result["context"]["search"]["connections"][0]["found"] is True
    assert result["context"]["rendered"]
    assert "continuation" not in result["context"]


def test_view_synchronizes_structural_graph_before_reporting_diagnostics(
    tmp_path: Path,
) -> None:
    output = tmp_path / "purpory-out"
    output.mkdir()
    (output / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "auth", "label": "Auth", "source_file": "src/auth.py"},
                    {"id": "token", "label": "Token", "source_file": "src/token.py"},
                ],
                "links": [{"source": "auth", "target": "token", "relation": "calls"}],
            }
        ),
        encoding="utf-8",
    )
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path)

    view = service.view()

    assert view["diagnostics"]["counts"]["nodes"] == 2
    assert view["diagnostics"]["counts"]["edges"] == 1
