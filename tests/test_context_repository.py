from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from purpory.supervise.bridge import candidate_topics, seed_from_graph
from purpory.supervise.repository import ContextGraphRepository
from purpory.supervise.recall import cue, lessons
from purpory.supervise.resolve import graph_slice, resolve_topic


def _graph(path: Path, nodes: list[dict], links: list[dict]) -> Path:
    path.write_text(json.dumps({"nodes": nodes, "links": links}), encoding="utf-8")
    return path


def test_topic_requires_exactly_one_content_source(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    with pytest.raises(ValueError, match="exactly one"):
        repository.set_topic("decision.database")
    with pytest.raises(ValueError, match="exactly one"):
        repository.set_topic("decision.database", value="x", source="@repo/x")


def test_human_topic_is_never_overwritten_by_graph_seed(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.set_topic("auth.tokens", value="Human decision", kind="decision")
    action = repository.set_topic(
        "auth.tokens",
        source="@repo/src/auth.py",
        kind="code-area",
        origin="graph-seed",
        seed_node_id="auth",
        seed_graph="graph.json",
    )
    assert action == "kept"
    topic = repository.get_topic("auth.tokens")
    assert topic is not None
    assert topic["value"] == "Human decision"


def test_human_edit_promotes_a_seed(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.set_topic(
        "auth.tokens",
        source="@repo/src/auth.py",
        kind="code-area",
        origin="graph-seed",
        seed_node_id="auth",
        seed_graph="graph.json",
    )
    repository.set_topic("auth.tokens", value="Curated", kind="decision")
    topic = repository.get_topic("auth.tokens")
    assert topic is not None
    assert topic["origin"] == "human"
    assert topic["seed_node_id"] is None


def test_candidate_topics_are_deterministic(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path / "graph.json",
        [
            {"id": "b", "label": "Beta", "community": 2, "source_file": "src/b.py"},
            {"id": "a", "label": "Alpha", "community": 2, "source_file": "src/a.py"},
        ],
        [{"source": "b", "target": "a"}],
    )
    first = candidate_topics(graph, per_community=2)
    second = candidate_topics(graph, per_community=2)
    assert first == second
    assert [item["key"] for item in first["candidates"]] == [
        "community-2.alpha",
        "community-2.beta",
    ]


def test_duplicate_seed_labels_receive_stable_unique_keys(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path / "graph.json",
        [
            {"id": "a", "label": "Handler", "community": 1, "source_file": "a.py"},
            {"id": "b", "label": "Handler", "community": 1, "source_file": "b.py"},
        ],
        [],
    )
    keys = [item["key"] for item in candidate_topics(graph, per_community=2)["candidates"]]
    assert len(keys) == len(set(keys)) == 2
    assert keys[0] == "community-1.handler"
    assert keys[1].startswith("community-1.handler-")


def test_reseed_does_not_prune_a_live_non_candidate_node(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path / "graph.json",
        [
            {"id": "a", "label": "Alpha", "community": 1, "source_file": "a.py"},
            {"id": "b", "label": "Beta", "community": 1, "source_file": "b.py"},
        ],
        [{"source": "a", "target": "b"}],
    )
    repository = ContextGraphRepository(tmp_path / "context.db")
    seed_from_graph(repository, graph, per_community=2)
    result = seed_from_graph(repository, graph, per_community=1)
    assert result["pruned"] == []
    assert repository.get_topic("community-1.beta") is not None


def test_reseed_prunes_only_a_missing_graph_seed(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path / "graph.json",
        [{"id": "a", "label": "Alpha", "community": 1, "source_file": "a.py"}],
        [],
    )
    repository = ContextGraphRepository(tmp_path / "context.db")
    seed_from_graph(repository, graph)
    _graph(
        graph,
        [{"id": "b", "label": "Beta", "community": 1, "source_file": "b.py"}],
        [],
    )
    result = seed_from_graph(repository, graph)
    assert result["pruned"] == ["community-1.alpha"]
    assert repository.get_topic("community-1.beta") is not None


def test_prefix_matching_respects_topic_segments(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.set_topic("auth.token", value="token")
    repository.set_topic("authz.policy", value="policy")
    assert [topic["key"] for topic in repository.list_topics(["auth"])] == ["auth.token"]


def test_reconcile_preview_then_apply_is_atomic_and_audited(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    changes = [
        {
            "key": "intent.product.simplicity",
            "kind": "decision",
            "value": "Keep the product simple and internally consistent.",
            "expectedHash": None,
        },
        {
            "key": "knowledge.product.priority",
            "kind": "note",
            "value": "User intent is more important than exhaustive session history.",
            "expectedHash": None,
        },
    ]

    preview = repository.reconcile_topics(changes, project="demo")
    assert preview["applied"] is False
    assert [item["action"] for item in preview["changes"]] == ["created", "created"]
    assert repository.list_topics(project="demo") == []

    result = repository.reconcile_topics(
        changes, project="demo", apply=True, session_id="session-1"
    )
    assert result["applied"] is True
    assert [item["action"] for item in result["changes"]] == ["created", "created"]
    assert [topic["key"] for topic in repository.list_topics(project="demo")] == [
        "intent.product.simplicity",
        "knowledge.product.priority",
    ]
    with repository.connect() as connection:
        event = connection.execute(
            "SELECT session_id, payload_json FROM context_events "
            "WHERE event_type = 'memory.reconciled'"
        ).fetchone()
    assert event["session_id"] == "session-1"
    assert len(json.loads(event["payload_json"])["changes"]) == 2


def test_reconcile_conflict_rolls_back_the_whole_batch(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.reconcile_topics(
        [
            {
                "key": "intent.a",
                "kind": "decision",
                "value": "Original A",
                "expectedHash": None,
            },
            {
                "key": "intent.b",
                "kind": "decision",
                "value": "Original B",
                "expectedHash": None,
            },
        ],
        project="demo",
        apply=True,
    )
    topics = {topic["key"]: topic for topic in repository.list_topics(project="demo")}

    result = repository.reconcile_topics(
        [
            {
                "key": "intent.a",
                "kind": "decision",
                "value": "Changed A",
                "expectedHash": topics["intent.a"]["hash"],
            },
            {
                "key": "intent.b",
                "kind": "decision",
                "value": "Changed B",
                "expectedHash": "stale",
            },
        ],
        project="demo",
        apply=True,
    )

    assert result["applied"] is False
    assert [item["action"] for item in result["changes"]] == ["updated", "conflict"]
    intent_a = repository.get_topic("intent.a", project="demo")
    intent_b = repository.get_topic("intent.b", project="demo")
    assert intent_a is not None and intent_a["value"] == "Original A"
    assert intent_b is not None and intent_b["value"] == "Original B"


def test_project_memory_overrides_global_memory_only_in_that_project(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.set_topic("intent.direction", value="Global", kind="decision")
    repository.set_topic(
        "intent.direction", value="Project", kind="decision", project="demo"
    )

    global_topic = repository.get_topic("intent.direction")
    project_topic = repository.get_topic("intent.direction", project="demo")
    assert global_topic is not None and global_topic["value"] == "Global"
    assert project_topic is not None and project_topic["value"] == "Project"
    visible = repository.list_retrieval_nodes(project="demo")
    memories = [node for node in visible if node["namespace"] == "memory"]
    assert len(memories) == 1
    assert memories[0]["value"] == "Project"


def test_pointer_resolution_is_live_and_sandboxed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "decision.md"
    target.write_text("first", encoding="utf-8")
    topic = {"value": None, "source": "@repo/decision.md", "kind": "doc-ref"}
    assert resolve_topic(topic, root=root)["value"] == "first"
    target.write_text("second", encoding="utf-8")
    assert resolve_topic(topic, root=root)["value"] == "second"
    escaped = resolve_topic(
        {"value": None, "source": "@repo/../secret", "kind": "doc-ref"}, root=root
    )
    assert escaped["mode"] == "unresolved"
    assert "escapes" in escaped["error"]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlink_cannot_escape_pointer_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(secret)
    result = resolve_topic(
        {"value": None, "source": "@repo/link.txt", "kind": "doc-ref"}, root=root
    )
    assert result["mode"] == "unresolved"


def test_graph_slice_uses_segment_scope_and_explicit_caps(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path / "graph.json",
        [
            {"id": "a", "label": "Auth", "source_file": "src/auth/main.py"},
            {"id": "b", "label": "Token", "source_file": "src/auth/token.py"},
            {"id": "c", "label": "Wrong", "source_file": "src/authz/policy.py"},
        ],
        [
            {"source": "a", "target": "b", "confidence": "EXTRACTED", "weight": 2},
            {"source": "a", "target": "c", "confidence": "INFERRED", "weight": 5},
        ],
    )
    result = graph_slice(graph, "src/auth", god_node_limit=1, edge_limit=1)
    assert result is not None
    assert result["edges"] == [
        {
            "source": "a",
            "target": "b",
            "relation": None,
            "confidence": "EXTRACTED",
            "weight": 2,
        }
    ]
    assert result["truncated"]["godNodes"] == 1


def test_lessons_promote_cross_session_corroboration(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.set_topic("decision.database", value="PostgreSQL")
    repository.record_delivery("a", "decision.database", "value", delivered_at=100)
    repository.record_delivery("b", "decision.database", "value", delivered_at=100)
    result = lessons(repository, now=100)
    assert [item["key"] for item in result["preferred"]] == ["decision.database"]
    assert result["tentative"] == []


def test_cue_marks_only_evidence_backed_unfamiliar_areas(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    project = "demo"
    old = tmp_path / "repo" / "src" / "auth"
    cold = tmp_path / "repo" / "src" / "billing"
    new = tmp_path / "repo" / "src" / "reporting"
    old.mkdir(parents=True)
    cold.mkdir(parents=True)
    new.mkdir(parents=True)
    assert cue(repository, [cold], session_id="first", project=project)["unfamiliar"] is False
    repository.record_touch("past", old, project=project)
    assert cue(repository, [new], session_id="current", project=project)["unfamiliar"] is True


def test_request_resolution_requires_an_existing_topic(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    request_id = repository.create_request("session", "Need deployment policy")
    with pytest.raises(KeyError):
        repository.resolve_request(request_id, "decision.deploy")
    repository.set_topic("decision.deploy", value="Blue-green")
    assert repository.resolve_request(request_id, "decision.deploy") is True
    assert repository.list_requests("resolved")[0]["resolvedKey"] == "decision.deploy"


def test_code_memory_and_seed_links_share_one_context_graph(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path / "graph.json",
        [
            {"id": "auth", "label": "Auth", "community": 1, "source_file": "src/auth.py"},
            {"id": "token", "label": "Token", "community": 1, "source_file": "src/token.py"},
        ],
        [{"source": "auth", "target": "token", "relation": "calls"}],
    )
    repository = ContextGraphRepository(tmp_path / "context.db")

    seed_from_graph(repository, graph, project="demo", per_community=2)
    repository.set_topic("decision.auth", value="Use short-lived access tokens", kind="decision")

    with repository.connect() as connection:
        object_type = connection.execute(
            "SELECT type FROM sqlite_master WHERE name = 'topics'"
        ).fetchone()["type"]
        namespaces = dict(
            connection.execute(
                "SELECT namespace, COUNT(*) AS count FROM context_nodes GROUP BY namespace"
            ).fetchall()
        )
        edge_origins = dict(
            connection.execute(
                "SELECT origin, COUNT(*) AS count FROM context_edges GROUP BY origin"
            ).fetchall()
        )

    assert object_type == "view"
    assert namespaces == {"code": 2, "memory": 3}
    assert edge_origins == {"graph-seed": 2, "structural": 1}
    assert [node["id"] for node in repository.graph_payload(project="demo")["nodes"]] == [
        "auth",
        "token",
    ]

    bounded = repository.graph_payload(
        project="demo",
        node_limit=1,
        edge_limit=1,
    )
    assert bounded["totalNodes"] == 2
    assert bounded["totalLinks"] == 1
    assert len(bounded["nodes"]) == 1
    assert bounded["links"] == []
    assert bounded["truncated"] is True


def test_delivery_history_is_append_only_in_context_events(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.set_topic("decision.database", value="PostgreSQL", kind="decision")

    repository.record_delivery("session", "decision.database", "first", project="demo")
    repository.record_delivery("session", "decision.database", "second", project="demo")

    with repository.connect() as connection:
        latest = connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        events = connection.execute(
            "SELECT payload_json FROM context_events WHERE event_type = 'context.delivered' ORDER BY id"
        ).fetchall()
        received = connection.execute(
            "SELECT COUNT(*) FROM context_edges WHERE relation = 'received'"
        ).fetchone()[0]

    assert latest == 1
    assert [json.loads(row["payload_json"])["rendered"] for row in events] == [
        "first",
        "second",
    ]
    assert received == 1
