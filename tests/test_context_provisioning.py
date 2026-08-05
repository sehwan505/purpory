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
                    {
                        "id": "typing-any",
                        "label": "Any",
                        "type": "code",
                        "community": 1,
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
                    {
                        "source": "auth-service",
                        "target": "typing-any",
                        "relation": "references",
                        "confidence": "EXTRACTED",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path)
    service.sync_graph()
    service.set_topic(
        "decision.auth.ttl",
        value="Access tokens expire after thirty minutes",
        kind="decision",
    )
    return service


def test_catalog_is_compact_and_never_copies_memory_values(tmp_path: Path) -> None:
    service = _service_with_graph(tmp_path)

    catalog = service._provisioner().catalog(session_id="agent-a")

    assert catalog["counts"]["human"] == 1
    assert catalog["counts"]["material"] == 4
    assert catalog["topicNamespaces"] == [{"name": "decision", "count": 1}]
    assert catalog["graphSnapshot"]["nodeCount"] == 4
    assert "Access tokens" not in json.dumps(catalog)


def test_search_connects_distinct_concepts_through_the_context_graph(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)

    result = service._provisioner().search(
        "auth database",
        session_id="agent-a",
        scopes=["code"],
        keywords=["auth", "database"],
    )

    labels = [candidate["label"] for candidate in result["candidates"]]
    assert result["scopes"] == ["material"]
    assert "AuthService" in labels
    assert "DatabasePool" in labels
    assert result["connections"][0]["found"] is True
    assert {node["namespace"] for node in result["connections"][0]["nodes"]} == {"material"}
    connection_labels = [node["label"] for node in result["connections"][0]["nodes"]]
    assert connection_labels in (
        ["AuthService", "TokenRepository", "DatabasePool"],
        ["DatabasePool", "TokenRepository", "AuthService"],
    )


def test_search_uses_only_terms_present_in_the_context_vocabulary(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)

    result = service._provisioner().search(
        "인증과 데이터베이스 연결",
        session_id="agent-a",
        scopes=["material"],
        keywords=["auth", "database"],
        connect=False,
    )

    assert result["terms"] == ["auth", "database"]
    assert result["ignoredTerms"] == ["연결"]
    assert {item["input"] for item in result["expandedTerms"]} >= {
        "인증과",
        "데이터베이스",
    }
    assert {candidate["label"] for candidate in result["candidates"]} >= {
        "AuthService",
        "DatabasePool",
    }


def test_generic_service_term_does_not_select_unrelated_context(tmp_path: Path) -> None:
    service = _service_with_graph(tmp_path)

    result = service._provisioner().search(
        "service",
        session_id="agent-a",
        scopes=["material"],
        connect=False,
    )

    assert result["candidates"] == []


def test_search_returns_grounded_graph_frontier_without_forcing_a_match(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)
    provisioner = service._provisioner()

    found = provisioner.search(
        "AuthService",
        session_id="agent-a",
        scopes=["material"],
        connect=False,
    )
    missed = provisioner.search(
        "missing deployment policy",
        session_id="agent-a",
        scopes=["material"],
        connect=False,
    )

    frontier = found["exploration"]
    assert frontier["status"] == "continue"
    assert frontier["hasMore"] is True
    assert frontier["frontier"][0]["node"]["label"] == "TokenRepository"
    assert {item["node"]["label"] for item in frontier["frontier"]} == {"TokenRepository"}
    assert frontier["frontier"][0]["via"]["relation"] == "calls"
    assert frontier["frontier"][0]["provenance"] == "graph-lead"
    assert missed["candidates"] == []
    assert missed["exploration"]["status"] == "incomplete"
    assert missed["exploration"]["hasMore"] is False
    assert missed["exploration"]["frontier"] == []
    assert missed["exploration"]["gaps"]

    lead_id = frontier["frontier"][0]["node"]["id"]
    provisioner.deliver([lead_id], session_id="agent-a", token_budget=512)
    repeated = provisioner.search(
        "AuthService",
        session_id="agent-a",
        scopes=["material"],
        connect=False,
    )
    assert repeated["exploration"]["frontier"] == []
    assert repeated["exploration"]["hasMore"] is False


def test_context_steps_are_internal_to_prepare(tmp_path: Path) -> None:
    service = _service_with_graph(tmp_path)

    for name in ("catalog", "search", "expand", "context_path", "deliver"):
        assert not hasattr(service, name)


def test_search_expands_korean_developer_terms_without_an_llm(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)

    result = service._provisioner().search(
        "인증과 데이터베이스를 찾아줘",
        session_id="agent-a",
        scopes=["material"],
        connect=False,
    )

    assert {"auth", "authentication", "database"} & set(result["terms"])
    assert {candidate["label"] for candidate in result["candidates"]} >= {
        "AuthService",
        "DatabasePool",
    }
    expansions = {item["input"]: item["terms"] for item in result["expandedTerms"]}
    assert "auth" in expansions["인증과"]
    assert "database" in expansions["데이터베이스를"]


def test_session_scope_can_recall_previously_delivered_memory(tmp_path: Path) -> None:
    service = _service_with_graph(tmp_path)
    memory_search = service._provisioner().search(
        "token expiry",
        session_id="agent-a",
        scopes=["human"],
        keywords=["token", "expire"],
        connect=False,
    )
    memory_id = memory_search["candidates"][0]["nodeId"]
    service._provisioner().deliver([memory_id], session_id="agent-a", token_budget=512)

    result = service._provisioner().search(
        "token",
        session_id="agent-a",
        scopes=["session"],
        connect=False,
    )

    assert result["candidates"][0]["nodeId"] == memory_id
    assert "session-recall" in result["candidates"][0]["signals"]


def test_deliver_records_project_local_reconciled_memory(tmp_path: Path) -> None:
    service = _service_with_graph(tmp_path)
    key = "intent.product.long-term-autonomy"
    value = "Eventually replace the user's work after learning durable intent."
    applied = service.reconcile_topics(
        [
            {
                "key": key,
                "value": value,
                "kind": "decision",
                "expectedHash": None,
            }
        ],
        apply=True,
        session_id="reconcile-session",
    )
    assert applied["changes"][0]["action"] == "created"

    search = service._provisioner().search(
        "long term autonomy durable intent",
        session_id="agent-a",
        scopes=["human"],
        connect=False,
    )
    memory = next(candidate for candidate in search["candidates"] if candidate["key"] == key)

    delivered = service._provisioner().deliver(
        [memory["nodeId"]],
        session_id="agent-a",
        token_budget=512,
    )

    assert delivered["delivery"][0]["nodeId"] == memory["nodeId"]
    assert value in delivered["rendered"]
    with service.repository.connect() as connection:
        received = connection.execute(
            """
            SELECT target.project
            FROM context_edges edge
            JOIN context_nodes target ON target.id = edge.target_id
            WHERE edge.relation = 'received' AND target.id = ?
            """,
            (memory["nodeId"],),
        ).fetchone()
    assert received is not None
    assert received["project"] == service.project_id


def test_candidate_pool_reserves_space_for_memory_and_active_paths(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)
    service.set_topic(
        "decision.shared-token",
        value="shared token policy",
        kind="decision",
    )
    service._provisioner().catalog(session_id="agent-a")

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

    result = service._provisioner().search(
        "auth token database",
        session_id="agent-a",
        scopes=["human", "material"],
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
        "material",
    }


def test_expand_is_relation_filtered_and_bounded(tmp_path: Path) -> None:
    service = _service_with_graph(tmp_path)
    search = service._provisioner().search(
        "AuthService", session_id="agent-a", scopes=["material"], connect=False
    )
    auth_id = next(
        candidate["nodeId"]
        for candidate in search["candidates"]
        if candidate["label"] == "AuthService"
    )

    expanded = service._provisioner().expand([auth_id], depth=1, relations=["calls"], node_limit=10)

    assert {node["label"] for node in expanded["nodes"]} == {
        "AuthService",
        "TokenRepository",
    }
    assert [edge["relation"] for edge in expanded["edges"]] == ["calls"]
    assert expanded["truncated"] is False
    assert expanded["exploration"]["frontier"][0]["node"]["label"] == "DatabasePool"
    assert expanded["exploration"]["frontier"][0]["via"]["relation"] == "uses"


def test_deliver_records_exact_context_and_deduplicates_per_session(
    tmp_path: Path,
) -> None:
    service = _service_with_graph(tmp_path)
    search = service._provisioner().search(
        "AuthService", session_id="agent-a", scopes=["material"], connect=False
    )
    auth_id = search["candidates"][0]["nodeId"]

    first = service._provisioner().deliver([auth_id], session_id="agent-a", token_budget=512)
    second = service._provisioner().deliver([auth_id], session_id="agent-a", token_budget=512)

    assert first["delivery"][0]["mode"] == "context-graph"
    assert "AuthService" in first["rendered"]
    assert first["valueHash"]
    assert second["delivery"] == []
    assert second["omitted"] == [
        {"key": first["delivery"][0]["key"], "reason": "already-delivered"}
    ]
    session = service.repository.session_view()[0]
    assert session["items"][0]["valueHash"] == first["delivery"][0]["valueHash"]
    assert session["items"][0]["label"] == "AuthService"
    assert session["items"][0]["source"] == "src/auth/service.py"
    assert "AuthService" in session["items"][0]["preview"]
    assert session["context"]["graphProject"] == str(tmp_path)


def test_prepare_includes_ready_context_without_public_primitives(
    tmp_path: Path,
) -> None:
    from purpory.supervise.gate.contract import GateProposal, GateRequest, ProviderResult

    class Provider:
        def propose(self, request: GateRequest) -> ProviderResult:
            assert request.model_payload()["contextCatalog"]["counts"]["material"] == 4
            return ProviderResult(
                proposal=GateProposal.from_mapping(
                    {
                        "action": "search",
                        "query": "auth database",
                        "scopes": ["material"],
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
    assert result["context"]["manifest"]["counts"]["material"] == 4
    assert result["context"]["search"]["connections"][0]["found"] is True
    assert result["context"]["rendered"]
    bridge = next(item for item in result["delivery"] if item["signals"] == ["graph-bridge"])
    assert bridge["nodeId"] not in {
        candidate["nodeId"] for candidate in result["context"]["search"]["candidates"]
    }
    assert "TokenRepository" in bridge["rendered"]
    assert bridge["rendered"].startswith(
        "[retrieval=graph-bridge; exploratory graph context]"
    )


def test_view_reports_explicitly_imported_structural_graph(
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
    service.sync_graph()

    view = service.view()

    assert view["diagnostics"]["counts"]["nodes"] == 2
    assert view["diagnostics"]["counts"]["edges"] == 1
