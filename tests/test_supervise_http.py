from __future__ import annotations

import json
import subprocess
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from purpory.supervise.library import ContextService
from purpory.supervise.serve.server import ContextHTTPServer


@pytest.fixture
def context_server(tmp_path: Path):
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path)
    service.set_topic("decision.database", value="PostgreSQL", kind="decision")
    server = ContextHTTPServer(
        ("127.0.0.1", 0),
        service,
        "read-secret",
        "write-secret",
        "agent-secret",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(server: ContextHTTPServer, method: str, path: str, **kwargs):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    connection.request(method, path, **kwargs)
    response = connection.getresponse()
    body = json.loads(response.read())
    connection.close()
    return response.status, body


def test_read_api_accepts_query_token(context_server: ContextHTTPServer) -> None:
    status, body = _request(context_server, "GET", "/api/view?t=read-secret")
    assert status == 200
    assert body["topics"][0]["key"] == "decision.database"


def test_project_api_creates_namespace_and_attaches_git_resource(
    context_server: ContextHTTPServer,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "-C", str(repository), "init", "-b", "main"],
        check=True,
        capture_output=True,
        text=True,
    )
    headers = {
        "Content-Type": "application/json",
        "X-Purpory-Token": "write-secret",
    }

    status, project = _request(
        context_server,
        "POST",
        "/api/projects",
        body=json.dumps({"name": "Agent work", "description": "Shared work context"}),
        headers=headers,
    )
    assert status == 201

    status, attached = _request(
        context_server,
        "POST",
        f"/api/projects/{project['id']}/resources/git",
        body=json.dumps({"path": str(repository), "alias": "Main repository"}),
        headers=headers,
    )
    assert status == 200
    assert attached["resources"][0]["alias"] == "Main repository"
    assert attached["resources"][0]["views"][0]["locator"] == str(repository.resolve())

    status, remote = _request(
        context_server,
        "POST",
        f"/api/projects/{project['id']}/resources/git",
        body=json.dumps({"path": "https://github.com/acme/remote.git"}),
        headers=headers,
    )
    assert status == 200
    remote_resource = next(
        item for item in remote["resources"] if item["externalIdentity"] == "github.com/acme/remote"
    )
    assert remote_resource["views"] == []

    status, projects = _request(
        context_server,
        "GET",
        "/api/projects?t=read-secret",
    )
    assert status == 200
    assert [item["name"] for item in projects] == ["Agent work"]


def test_model_status_api_reports_managed_runtime_state(
    context_server: ContextHTTPServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime.GateModelManager.status",
        lambda _self, **_kwargs: {"installed": False, "endpoint": None},
    )

    status, body = _request(context_server, "GET", "/api/model/status?t=read-secret")

    assert status == 200
    assert body["installed"] is False
    assert body["providerSource"] == "none"


def test_model_install_api_uses_existing_model_manager(
    context_server: ContextHTTPServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        context_server.service,
        "install_model",
        lambda model: {"action": "installed", "model": model, "installed": True},
    )

    status, body = _request(
        context_server,
        "POST",
        "/api/model/install",
        body=json.dumps({"model": "qwen3.5:0.8b"}),
        headers={
            "Content-Type": "application/json",
            "X-Purpory-Token": "write-secret",
        },
    )

    assert status == 200
    assert body == {
        "action": "installed",
        "model": "qwen3.5:0.8b",
        "installed": True,
    }


def test_model_select_api_passes_external_reconcile_provider(
    context_server: ContextHTTPServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: dict[str, str] = {}

    def select(model: str, *, role: str, provider: str) -> dict[str, str]:
        selected.update(model=model, role=role, provider=provider)
        return selected

    monkeypatch.setattr(context_server.service, "select_model", select)

    status, body = _request(
        context_server,
        "POST",
        "/api/model/select",
        body=json.dumps(
            {"model": "gpt-4.1-mini", "role": "reconcile", "provider": "openai"}
        ),
        headers={
            "Content-Type": "application/json",
            "X-Purpory-Token": "write-secret",
        },
    )

    assert status == 200
    assert body == {
        "model": "gpt-4.1-mini",
        "role": "reconcile",
        "provider": "openai",
    }


def test_graph_api_imports_snapshot_and_bounds_response(
    context_server: ContextHTTPServer,
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
    context_server.service.sync_graph()

    status, body = _request(
        context_server,
        "GET",
        "/api/graph?t=read-secret&limit=1&edgeLimit=1",
    )

    assert status == 200
    assert body["totalNodes"] == 2
    assert body["totalLinks"] == 1
    assert len(body["nodes"]) == 1
    assert body["truncated"] is True


def test_read_api_rejects_bad_token(context_server: ContextHTTPServer) -> None:
    status, body = _request(context_server, "GET", "/api/view?t=wrong")
    assert status == 401
    assert "token" in body["error"]


def test_mutation_rejects_query_token_before_body(context_server: ContextHTTPServer) -> None:
    status, body = _request(context_server, "POST", "/api/topics?t=read-secret")
    assert status == 403
    assert "query tokens" in body["error"]


def test_mutation_requires_write_header(context_server: ContextHTTPServer) -> None:
    payload = json.dumps({"key": "decision.deploy", "value": "Blue-green", "kind": "decision"})
    status, _ = _request(
        context_server,
        "POST",
        "/api/topics",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    assert status == 401
    status, body = _request(
        context_server,
        "POST",
        "/api/topics",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "X-Purpory-Token": "write-secret",
        },
    )
    assert status == 201
    assert body["action"] == "created"


def test_mutation_rejects_cross_origin(context_server: ContextHTTPServer) -> None:
    payload = json.dumps({"key": "decision.deploy", "value": "Blue-green"})
    status, body = _request(
        context_server,
        "POST",
        "/api/topics",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "X-Purpory-Token": "write-secret",
            "Origin": "https://attacker.example",
        },
    )
    assert status == 403
    assert "cross-origin" in body["error"]


def test_agent_token_can_prepare_context_but_cannot_curate_topics(
    context_server: ContextHTTPServer,
) -> None:
    payload = json.dumps(
        {
            "message": "decision.database",
            "sessionId": "agent-session",
            "tokenBudget": 512,
        }
    )
    status, body = _request(
        context_server,
        "POST",
        "/api/context/prepare",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "X-Purpory-Agent-Token": "agent-secret",
        },
    )
    assert status == 200
    assert body["action"] == "retrieve"

    status, _ = _request(
        context_server,
        "POST",
        "/api/topics",
        body=json.dumps({"key": "decision.bad", "value": "bad"}),
        headers={
            "Content-Type": "application/json",
            "X-Purpory-Agent-Token": "agent-secret",
        },
    )
    assert status == 401


def test_agent_token_can_raise_but_not_decide_global_memory_request(
    context_server: ContextHTTPServer,
) -> None:
    agent_headers = {
        "Content-Type": "application/json",
        "X-Purpory-Agent-Token": "agent-secret",
    }
    status, proposed = _request(
        context_server,
        "POST",
        "/api/global-memory/requests",
        body=json.dumps(
            {
                "key": "intent.editor",
                "kind": "decision",
                "value": "Use Neovim",
                "rationale": "Reusable editor preference",
            }
        ),
        headers=agent_headers,
    )
    assert status == 201
    assert proposed["status"] == "pending"

    status, _ = _request(
        context_server,
        "POST",
        f"/api/global-memory/requests/{proposed['id']}/approve",
        body="{}",
        headers=agent_headers,
    )
    assert status == 401


def test_agent_token_can_raise_but_not_resolve_evidence_review(
    context_server: ContextHTTPServer,
) -> None:
    agent_headers = {
        "Content-Type": "application/json",
        "X-Purpory-Agent-Token": "agent-secret",
    }
    status, review = _request(
        context_server,
        "POST",
        "/api/memory/reviews",
        body=json.dumps(
            {
                "key": "decision.database",
                "sourceType": "code",
                "sourceId": "src/database.py",
                "evidence": "sqlite configuration",
                "reason": "Code conflicts with the PostgreSQL decision.",
            }
        ),
        headers=agent_headers,
    )
    assert status == 201

    status, _ = _request(
        context_server,
        "POST",
        f"/api/memory/reviews/{review['id']}/resolve",
        body=json.dumps({"outcome": "keep"}),
        headers=agent_headers,
    )
    assert status == 401


def test_agent_token_cannot_enable_preparation_input_retention(
    context_server: ContextHTTPServer,
) -> None:
    status, body = _request(
        context_server,
        "POST",
        "/api/context/prepare",
        body=json.dumps({"message": "private", "sessionId": "agent-session", "retainInput": True}),
        headers={
            "Content-Type": "application/json",
            "X-Purpory-Agent-Token": "agent-secret",
        },
    )
    assert status == 403
    assert "human mutation token" in body["error"]


def test_removed_agent_primitives_are_not_exposed(
    context_server: ContextHTTPServer,
) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-Purpory-Token": "write-secret",
    }
    for path in (
        "/api/gate",
        "/api/pull",
        "/api/request",
        "/api/context/push",
        "/api/context/catalog",
        "/api/context/search",
        "/api/context/expand",
        "/api/context/path",
        "/api/context/deliver",
    ):
        status, _ = _request(
            context_server,
            "POST",
            path,
            body="{}",
            headers=headers,
        )
        assert status == 404, path


def test_context_decisions_and_feedback_api(context_server: ContextHTTPServer) -> None:
    payload = json.dumps({"message": "hello", "sessionId": "agent-session"})
    _, decision = _request(
        context_server,
        "POST",
        "/api/context/prepare",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "X-Purpory-Agent-Token": "agent-secret",
        },
    )
    status, decisions = _request(
        context_server,
        "GET",
        "/api/context/decisions?t=read-secret&limit=10",
    )
    assert status == 200
    assert decisions[0]["id"] == decision["decisionId"]

    status, feedback = _request(
        context_server,
        "POST",
        f"/api/context/decisions/{decision['decisionId']}/feedback",
        body=json.dumps({"verdict": "correct"}),
        headers={
            "Content-Type": "application/json",
            "X-Purpory-Token": "write-secret",
        },
    )
    assert status == 200
    assert feedback["verdict"] == "correct"


def test_global_memory_approval_api_preserves_edits(
    context_server: ContextHTTPServer,
) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-Purpory-Token": "write-secret",
    }
    status, proposed = _request(
        context_server,
        "POST",
        "/api/global-memory/requests",
        body=json.dumps(
            {
                "key": "intent.editor",
                "kind": "decision",
                "value": "Use Vim",
                "rationale": "Reusable editor preference",
            }
        ),
        headers=headers,
    )
    assert status == 201
    status, edited = _request(
        context_server,
        "POST",
        f"/api/global-memory/requests/{proposed['id']}/edit",
        body=json.dumps(
            {
                "key": "intent.editor",
                "kind": "decision",
                "value": "Use Neovim",
                "rationale": "Corrected reusable editor preference",
            }
        ),
        headers=headers,
    )
    assert status == 200
    assert edited["initialProposal"]["value"] == "Use Vim"
    status, approved = _request(
        context_server,
        "POST",
        f"/api/global-memory/requests/{proposed['id']}/approve",
        body="{}",
        headers=headers,
    )
    assert status == 200
    assert approved["status"] == "approved"
    assert approved["finalProposal"]["value"] == "Use Neovim"


def test_needs_review_and_memory_report_api(
    context_server: ContextHTTPServer,
) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-Purpory-Token": "write-secret",
    }
    status, review = _request(
        context_server,
        "POST",
        "/api/memory/reviews",
        body=json.dumps(
            {
                "key": "decision.database",
                "sourceType": "code",
                "sourceId": "src/database.py",
                "evidence": "sqlite configuration",
                "reason": "Code conflicts with the PostgreSQL decision.",
            }
        ),
        headers=headers,
    )
    assert status == 201
    status, reviews = _request(
        context_server,
        "GET",
        "/api/memory/reviews?t=read-secret&status=open",
    )
    assert status == 200
    assert reviews[0]["id"] == review["id"]
    status, resolved = _request(
        context_server,
        "POST",
        f"/api/memory/reviews/{review['id']}/resolve",
        body=json.dumps({"outcome": "keep"}),
        headers=headers,
    )
    assert status == 200
    assert resolved["outcome"] == "keep"
    status, report = _request(
        context_server,
        "GET",
        "/api/memory/report?t=read-secret",
    )
    assert status == 200
    assert report


def test_static_dashboard_is_packaged(context_server: ContextHTTPServer) -> None:
    connection = HTTPConnection("127.0.0.1", context_server.server_port, timeout=2)
    connection.request("GET", "/")
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    connection.close()
    assert response.status == 200
    assert "Purpory" in body
    assert "Content-Security-Policy" in response.headers
