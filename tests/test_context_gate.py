from __future__ import annotations

import json
from pathlib import Path

import pytest

from purpory.supervise.gate.contract import (
    MAX_MESSAGE_CHARS,
    GateProposal,
    GateRequest,
    ProviderResult,
)
from purpory.supervise.gate.provider import GateProviderError
from purpory.supervise.gate.qwen import (
    DEFAULT_MAX_INPUT_TOKENS,
    QwenGateProvider,
)
from purpory.supervise.library import ContextService


class StubGateProvider:
    def __init__(self, proposal: GateProposal) -> None:
        self.proposal = proposal

    def propose(self, request: GateRequest) -> ProviderResult:
        return ProviderResult(
            proposal=self.proposal,
            model_id="stub/qwen",
            model_revision="test-revision",
            latency_ms=7,
        )


class FailingGateProvider:
    def propose(self, request: GateRequest) -> ProviderResult:
        raise GateProviderError("synthetic timeout")


def _proposal(
    action: str,
    *,
    query: str | None = None,
    scopes: list[str] | None = None,
    keywords: list[str] | None = None,
    reason: str = "PROJECT_CONTEXT_REQUIRED",
    clarification: str | None = None,
) -> GateProposal:
    return GateProposal.from_mapping(
        {
            "action": action,
            "query": query,
            "scopes": scopes or [],
            "keywords": keywords or [],
            "reasonCode": reason,
            "clarification": clarification,
        }
    )


def test_gate_contract_rejects_freeform_fields() -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        GateProposal.from_mapping(
            {
                "action": "skip",
                "query": None,
                "scopes": [],
                "keywords": [],
                "reasonCode": "SELF_CONTAINED",
                "clarification": None,
                "answer": "hallucinated",
            }
        )


def test_search_with_evidence_retrieves_and_records_exact_delivery(tmp_path: Path) -> None:
    provider = StubGateProvider(
        _proposal(
            "search",
            query="database decision",
            scopes=["human"],
            keywords=["PostgreSQL"],
        )
    )
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path, gate_provider=provider)
    service.set_topic(
        "decision.database", value="PostgreSQL is the source of truth", kind="decision"
    )

    result = service.prepare("Which database did we choose?", session_id="session-a")

    assert result["action"] == "retrieve"
    assert result["model"]["id"] == "stub/qwen"
    assert [item["key"] for item in result["delivery"]] == ["decision.database"]
    assert (
        service.repository.session_view()[0]["items"][0]["valueHash"]
        == result["delivery"][0]["valueHash"]
    )


def test_gate_retrieves_structural_nodes_without_seed_topics(tmp_path: Path) -> None:
    output = tmp_path / "purpory-out"
    output.mkdir()
    (output / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "token-service",
                        "label": "TokenService",
                        "type": "class",
                        "source_file": "src/auth/token.py",
                    },
                    {
                        "id": "auth-controller",
                        "label": "AuthController",
                        "type": "class",
                        "source_file": "src/auth/controller.py",
                    },
                ],
                "links": [
                    {
                        "source": "auth-controller",
                        "target": "token-service",
                        "relation": "calls",
                        "confidence": "EXTRACTED",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    provider = StubGateProvider(
        _proposal("search", query="TokenService", scopes=["code"], keywords=["TokenService"])
    )
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path, gate_provider=provider)
    service.sync_graph()

    result = service.prepare("Where is TokenService?", session_id="session-a")

    assert result["action"] == "retrieve"
    assert result["delivery"][0]["mode"] == "context-graph"
    assert result["delivery"][0]["origin"] == "structural"
    assert "TokenService" in result["delivery"][0]["rendered"]
    assert service.repository.list_topics() == []


def test_search_without_evidence_asks_and_deduplicates_gap(tmp_path: Path) -> None:
    provider = StubGateProvider(
        _proposal("search", query="missing deployment policy", scopes=["human"])
    )
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path, gate_provider=provider)

    first = service.prepare("What is our deployment policy?", session_id="session-a")
    second = service.prepare("What is our deployment policy?", session_id="session-a")

    assert first["action"] == "ask"
    assert second["requestId"] == first["requestId"]
    assert len(service.requests(status="open")) == 1


def test_direct_ask_records_a_deduplicated_gap(tmp_path: Path) -> None:
    provider = StubGateProvider(
        _proposal(
            "ask",
            reason="AMBIGUOUS_REQUEST",
            clarification="Which deployment environment do you mean?",
        )
    )
    service = ContextService(
        db_path=tmp_path / "context.db",
        root=tmp_path,
        gate_provider=provider,
    )

    first = service.prepare("Deploy it", session_id="session-a")
    second = service.prepare("Deploy it", session_id="session-a")

    assert first["action"] == "ask"
    assert first["requestId"] is not None
    assert second["requestId"] == first["requestId"]
    assert len(service.requests(status="open")) == 1


def test_direct_ask_searches_memory_before_interrupting_user(tmp_path: Path) -> None:
    provider = StubGateProvider(
        _proposal(
            "ask",
            reason="AMBIGUOUS_REQUEST",
            clarification="What goal do you mean?",
        )
    )
    service = ContextService(
        db_path=tmp_path / "context.db",
        root=tmp_path,
        gate_provider=provider,
    )
    service.set_topic(
        "intent.product.ultimate",
        value="Purpory should eventually understand and replace the user's work",
        kind="decision",
    )

    result = service.prepare("What is Purpory's ultimate product goal?", session_id="session-a")

    assert result["proposal"]["action"] == "ask"
    assert result["action"] == "retrieve"
    assert [item["key"] for item in result["delivery"]] == ["intent.product.ultimate"]
    assert result["requestId"] is None
    assert result["clarification"] is None


def test_scope_alone_never_selects_an_unrelated_human_topic(tmp_path: Path) -> None:
    provider = StubGateProvider(_proposal("search", query="deployment policy", scopes=["human"]))
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path, gate_provider=provider)
    service.set_topic(
        "decision.database", value="PostgreSQL transaction isolation", kind="decision"
    )

    result = service.prepare("How do we deploy?", session_id="session-a")

    assert result["action"] == "ask"
    assert result["delivery"] == []


def test_skip_never_searches_or_injects(tmp_path: Path) -> None:
    provider = StubGateProvider(_proposal("skip", reason="SELF_CONTAINED"))
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path, gate_provider=provider)
    service.set_topic("decision.database", value="PostgreSQL", kind="decision")

    result = service.prepare("Summarize this sentence", session_id="session-a")

    assert result["action"] == "skip"
    assert result["delivery"] == []
    assert service.repository.session_view() == []


def test_provider_failure_uses_audited_conservative_search(tmp_path: Path) -> None:
    service = ContextService(
        db_path=tmp_path / "context.db",
        root=tmp_path,
        gate_provider=FailingGateProvider(),
    )
    service.set_topic("decision.database", value="database PostgreSQL", kind="decision")

    result = service.prepare("database", session_id="session-a")

    assert result["action"] == "retrieve"
    assert result["proposal"]["reasonCode"] == "GATE_UNAVAILABLE"
    assert "synthetic timeout" in result["fallback"]


def test_provider_failure_without_evidence_skips_after_audited_search(tmp_path: Path) -> None:
    service = ContextService(
        db_path=tmp_path / "context.db",
        root=tmp_path,
        gate_provider=FailingGateProvider(),
    )

    result = service.prepare("implement the requested change", session_id="session-a")

    assert result["action"] == "skip"
    assert result["proposal"]["reasonCode"] == "GATE_UNAVAILABLE"
    assert service.requests(status="open") == []


def test_oversized_prompt_bypasses_gate_without_losing_original(tmp_path: Path) -> None:
    calls = 0

    class CapturingProvider:
        def input_limit_reason(self, request: GateRequest) -> str:
            return (
                f"gate prompt requires {DEFAULT_MAX_INPUT_TOKENS + 1} tokens, "
                f"exceeding operating limit {DEFAULT_MAX_INPUT_TOKENS}"
            )

        def propose(self, request: GateRequest) -> ProviderResult:
            nonlocal calls
            calls += 1
            raise AssertionError("oversized prompt must not invoke the gate model")

    service = ContextService(
        db_path=tmp_path / "context.db",
        root=tmp_path,
        gate_provider=CapturingProvider(),
    )
    message = "important beginning\n" + ("context " * 10_000) + "\nimportant ending"

    result = service.prepare(
        message,
        session_id="session-a",
        retain_input=True,
    )

    assert result["action"] == "skip"
    assert result["proposal"]["reasonCode"] == "GATE_UNAVAILABLE"
    assert "model invocation skipped" in result["fallback"]
    assert calls == 0
    assert len(message) <= MAX_MESSAGE_CHARS
    assert service.context_decisions()[0]["inputText"] == message


def test_unchanged_context_is_not_injected_twice_into_one_session(tmp_path: Path) -> None:
    provider = StubGateProvider(_proposal("search", query="database", scopes=["human"]))
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path, gate_provider=provider)
    service.set_topic("decision.database", value="database PostgreSQL", kind="decision")

    first = service.prepare("database", session_id="session-a")
    second = service.prepare("database", session_id="session-a")

    assert first["action"] == "retrieve"
    assert second["action"] == "skip"
    assert second["delivery"] == []
    assert second["omitted"] == [{"key": "decision.database", "reason": "already-delivered"}]


def test_gate_delivery_respects_explicit_budget(tmp_path: Path) -> None:
    provider = StubGateProvider(_proposal("search", query="architecture", scopes=["human"]))
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path, gate_provider=provider)
    service.set_topic("architecture.large", value="architecture " * 2_000)

    result = service.prepare("architecture", session_id="session-a", token_budget=128)

    assert result["action"] == "retrieve"
    assert result["delivery"][0]["truncated"] is True
    assert result["delivery"][0]["estimatedTokens"] <= 128
    assert "truncated by Purpory" in result["delivery"][0]["rendered"]


def test_gate_feedback_is_auditable_without_retaining_input_by_default(tmp_path: Path) -> None:
    provider = StubGateProvider(_proposal("skip", reason="SELF_CONTAINED"))
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path, gate_provider=provider)
    result = service.prepare("private request", session_id="session-a")
    service.context_feedback(
        result["decisionId"],
        verdict="incorrect",
        expected_action="retrieve",
        note="needed prior preference",
    )

    decision = service.context_decisions()[0]
    assert decision["inputText"] is None
    assert decision["inputHash"] != ""
    assert decision["feedback"]["expectedAction"] == "retrieve"


def test_qwen_provider_rejects_remote_endpoint_by_default() -> None:
    with pytest.raises(ValueError, match="remote gate URLs"):
        QwenGateProvider(base_url="https://models.example/v1")


def test_qwen_provider_expands_strict_model_classification(monkeypatch) -> None:
    captured: dict = {}

    class Response:
        status = 200

        def read(self, maximum: int) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "SEARCH\n"
                            }
                        }
                    ]
                }
            ).encode()

    class Connection:
        def request(self, method, target, body, headers):
            captured["calls"] = captured.get("calls", 0) + 1
            captured["method"] = method
            captured["target"] = target
            captured["body"] = json.loads(body)

        def getresponse(self):
            return Response()

        def close(self):
            captured["closed"] = True

    provider = QwenGateProvider(base_url="http://127.0.0.1:8080/v1")
    monkeypatch.setattr(provider, "_connection", lambda: Connection())
    request = GateRequest.create(
        message="전에 정한 인증 정책",
        session_id="session-a",
        project="demo",
        working_directory="/tmp/demo",
    )

    result = provider.propose(request)

    assert result.proposal.action == "search"
    assert result.proposal.query == "전에 정한 인증 정책"
    assert result.proposal.scopes == ("code", "human", "resource", "session")
    assert result.proposal.reason_code == "CONTEXT_SEARCH_REQUIRED"
    assert "response_format" not in captured["body"]
    assert "temperature" not in captured["body"]
    assert captured["body"]["max_tokens"] == 8
    assert captured["body"]["messages"][1]["content"] == "전에 정한 인증 정책"
    system_prompt = captured["body"]["messages"][0]["content"]
    assert "exactly one word" in system_prompt
    assert "SKIP, SEARCH, or ASK" in system_prompt
    assert captured["closed"] is True

    class SizedIds:
        def __len__(self) -> int:
            return 20

    class FakeTokenizer:
        def encode(self, rendered: str, *, add_special_tokens: bool):
            return type("Encoding", (), {"ids": SizedIds()})()

    provider.tokenizer_path = Path("/unused/tokenizer.json")
    provider.max_input_tokens = 19
    provider._tokenizer = FakeTokenizer()
    long_request = GateRequest.create(
        message="important beginning\n" + ("context " * 10_000) + "\nimportant ending",
        session_id="session-a",
        project="demo",
        working_directory="/tmp/demo",
    )
    with pytest.raises(GateProviderError, match="exceeding operating limit 19"):
        provider.propose(long_request)

    assert captured["calls"] == 1


def test_qwen_provider_rejects_non_classifier_response(monkeypatch) -> None:
    class Response:
        status = 200

        def read(self, maximum: int) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "I would search."}}]}
            ).encode()

    class Connection:
        def request(self, method, target, body, headers):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    provider = QwenGateProvider(base_url="http://127.0.0.1:8080/v1")
    monkeypatch.setattr(provider, "_connection", lambda: Connection())
    request = GateRequest.create(
        message="전에 정한 인증 정책",
        session_id="session-a",
        project="demo",
        working_directory="/tmp/demo",
    )

    with pytest.raises(GateProviderError, match="invalid gate classifier response"):
        provider.propose(request)
