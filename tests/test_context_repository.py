from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from purpory.supervise.bridge import candidate_topics, seed_from_graph
from purpory.supervise.repository import ContextGraphRepository, value_hash
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


def test_awareness_metrics_count_follow_up_after_exposure(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.set_topic("knowledge.hidden", value="A constraint", project="demo")
    topic = repository.get_topic("knowledge.hidden", project="demo")
    assert topic is not None

    hint = {"nodeId": topic["id"], "key": topic["key"], "reason": "graph-lead"}
    repository.record_awareness_exposures(
        "session-1", [hint], project="demo", shown_at=100
    )
    assert repository.awareness_metrics(project="demo") == {
        "exposures": 1,
        "followUps": 0,
    }
    assert repository.awareness_metrics(project="other") == {
        "exposures": 0,
        "followUps": 0,
    }
    repository.record_awareness_exposures(
        "session-1", [hint], project="other", shown_at=100
    )
    assert repository.awareness_metrics(project="other") == {
        "exposures": 1,
        "followUps": 0,
    }

    repository.record_node_delivery(
        "session-1",
        topic["id"],
        topic["key"],
        "A constraint",
        project="demo",
        delivered_at=101,
    )
    assert repository.awareness_metrics(project="demo") == {
        "exposures": 1,
        "followUps": 1,
    }
    assert repository.awareness_metrics(project="other") == {
        "exposures": 1,
        "followUps": 0,
    }


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


def test_reconcile_keeps_current_and_two_previous_versions(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    key = "intent.product.direction"
    expected_hash = None
    for number in range(1, 5):
        result = repository.reconcile_topics(
            [
                {
                    "key": key,
                    "kind": "decision",
                    "value": f"Direction {number}",
                    "expectedHash": expected_hash,
                }
            ],
            project="demo",
            apply=True,
        )
        assert result["changes"][0]["action"] in {"created", "updated"}
        topic = repository.get_topic(key, project="demo")
        assert topic is not None
        expected_hash = topic["hash"]

    versions = repository.list_memory_versions(key, project="demo")

    assert [item["version"] for item in versions] == [4, 3, 2]
    assert [item["value"] for item in versions] == [
        "Direction 4",
        "Direction 3",
        "Direction 2",
    ]
    assert [item["superseded"] for item in versions] == [False, True, True]


def test_reconcile_preview_and_unchanged_apply_do_not_create_versions(
    tmp_path: Path,
) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    key = "intent.product.direction"
    created = repository.reconcile_topics(
        [
            {
                "key": key,
                "kind": "decision",
                "value": "Stable direction",
                "expectedHash": None,
            }
        ],
        project="demo",
        apply=True,
    )
    expected_hash = created["changes"][0]["proposedHash"]
    unchanged = {
        "key": key,
        "kind": "decision",
        "value": "Stable direction",
        "expectedHash": expected_hash,
    }

    repository.reconcile_topics([unchanged], project="demo")
    applied = repository.reconcile_topics([unchanged], project="demo", apply=True)

    assert applied["changes"][0]["action"] == "unchanged"
    assert len(repository.list_memory_versions(key, project="demo")) == 1


def test_needs_review_is_content_addressed_and_does_not_reopen(
    tmp_path: Path,
) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.reconcile_topics(
        [
            {
                "key": "intent.database",
                "kind": "decision",
                "value": "Use PostgreSQL",
                "expectedHash": None,
            }
        ],
        project="demo",
        apply=True,
    )
    first_hash = value_hash("database evidence v1")
    first = repository.create_needs_review(
        "intent.database",
        project="demo",
        source_type="code",
        source_id="src/database.py",
        content_hash=first_hash,
        reason="Code now configures SQLite.",
    )
    resolved = repository.resolve_needs_review(first["id"], outcome="keep")
    repeated = repository.create_needs_review(
        "intent.database",
        project="demo",
        source_type="code",
        source_id="src/database.py",
        content_hash=first_hash,
        reason="Code now configures SQLite.",
    )
    changed = repository.create_needs_review(
        "intent.database",
        project="demo",
        source_type="code",
        source_id="src/database.py",
        content_hash=value_hash("database evidence v2"),
        reason="Code changed again.",
    )

    assert resolved is not None and resolved["status"] == "resolved"
    assert repeated["id"] == first["id"]
    assert repeated["created"] is False
    assert repeated["status"] == "resolved"
    assert changed["created"] is True
    assert changed["status"] == "open"


def test_memory_usage_preserves_raw_selection_and_expansion_counts(
    tmp_path: Path,
) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.set_topic("intent.database", value="Use PostgreSQL", kind="decision")
    node = repository.list_retrieval_nodes(project="demo")[0]

    repository.record_memory_usage(node["id"], event="selected", occurred_at=10)
    repository.record_memory_usage(node["id"], event="selected", occurred_at=20)
    repository.record_memory_usage(node["id"], event="expanded", occurred_at=30)

    usage = repository.memory_usage([node["id"]])[node["id"]]
    assert usage == {
        "selectedCount": 2,
        "expandedCount": 1,
        "lastSelectedAt": 20,
        "lastExpandedAt": 30,
    }


def test_global_memory_requires_request_decision_and_retains_edits(
    tmp_path: Path,
) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    request = repository.create_global_memory_request(
        "intent.editor",
        value="Use Vim",
        source=None,
        kind="decision",
        rationale="Reusable preference",
        requested_from_project="demo",
    )
    assert repository.get_topic("intent.editor") is None
    edited = repository.update_global_memory_request(
        request["id"],
        key="intent.editor",
        value="Use Neovim",
        source=None,
        kind="decision",
        rationale="The user corrected the reusable preference",
    )
    assert edited is not None

    approved = repository.decide_global_memory_request(request["id"], decision="approve")

    assert approved is not None and approved["status"] == "approved"
    assert approved["initialProposal"]["value"] == "Use Vim"
    assert approved["proposal"]["value"] == "Use Neovim"
    assert approved["finalProposal"]["value"] == "Use Neovim"
    topic = repository.get_topic("intent.editor")
    assert topic is not None and topic["value"] == "Use Neovim"
    assert len(repository.list_memory_versions("intent.editor", project="")) == 1


def test_identical_pending_global_memory_request_is_deduplicated(
    tmp_path: Path,
) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    arguments = {
        "value": "Use Neovim",
        "source": None,
        "kind": "decision",
        "rationale": "Reusable preference",
        "requested_from_project": "demo",
    }

    first = repository.create_global_memory_request("intent.editor", **arguments)
    repeated = repository.create_global_memory_request("intent.editor", **arguments)

    assert first["created"] is True
    assert repeated["created"] is False
    assert repeated["id"] == first["id"]
    assert len(repository.list_global_memory_requests("pending")) == 1


def test_stale_global_memory_approval_requires_a_fresh_human_edit(
    tmp_path: Path,
) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    first = repository.create_global_memory_request(
        "intent.editor",
        value="Use Vim",
        source=None,
        kind="decision",
        rationale="First reusable preference",
        requested_from_project="demo",
    )
    stale = repository.create_global_memory_request(
        "intent.editor",
        value="Use Emacs",
        source=None,
        kind="decision",
        rationale="Concurrent reusable preference",
        requested_from_project="demo",
    )
    repository.decide_global_memory_request(first["id"], decision="approve")

    with pytest.raises(ValueError, match="changed after this request"):
        repository.decide_global_memory_request(stale["id"], decision="approve")

    edited = repository.update_global_memory_request(
        stale["id"],
        key="intent.editor",
        value="Use Neovim",
        source=None,
        kind="decision",
        rationale="Reviewed after the competing approval",
    )
    assert edited is not None
    approved = repository.decide_global_memory_request(stale["id"], decision="approve")
    assert approved is not None and approved["status"] == "approved"
    topic = repository.get_topic("intent.editor")
    assert topic is not None and topic["value"] == "Use Neovim"


def test_rejected_global_memory_request_does_not_write_memory(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    request = repository.create_global_memory_request(
        "intent.shell",
        value="Use fish",
        source=None,
        kind="decision",
        rationale="Potentially reusable preference",
        requested_from_project="demo",
    )

    rejected = repository.decide_global_memory_request(request["id"], decision="reject")

    assert rejected is not None and rejected["status"] == "rejected"
    assert repository.get_topic("intent.shell") is None


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


def test_request_resolution_accepts_project_local_reconciled_memory(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    project = "demo"
    key = "intent.product.goal"
    applied = repository.reconcile_topics(
        [
            {
                "key": key,
                "value": "Preserve the user's durable intent.",
                "kind": "decision",
                "expectedHash": None,
            }
        ],
        project=project,
        apply=True,
        session_id="reconcile-session",
    )
    assert applied["changes"][0]["action"] == "created"
    request_id = repository.create_request(
        "session",
        "Need the product goal",
        project=project,
    )

    assert repository.resolve_request(request_id, key) is True

    with repository.connect() as connection:
        target = connection.execute(
            """
            SELECT node.project
            FROM context_edges edge
            JOIN context_nodes node ON node.id = edge.target_id
            WHERE edge.relation = 'resolved-as'
            """
        ).fetchone()
    assert target is not None
    assert target["project"] == project


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


def test_structural_graph_roundtrips_complete_snapshot(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    graph = {
        "directed": True,
        "built_at_commit": "abc123",
        "nodes": [
            {"id": "auth", "label": "Auth", "type": "class"},
            {"id": "token", "label": "Token", "type": "class"},
        ],
        "links": [{"source": "auth", "target": "token", "relation": "calls"}],
        "hyperedges": [{"id": "auth-flow", "nodes": ["auth", "token"]}],
    }

    result = repository.replace_structural_graph(graph, project="demo")
    snapshot = repository.graph_snapshot(project="demo")

    assert result["hyperedges"] == 1
    assert repository.structural_graph(project="demo") == graph
    assert snapshot is not None
    assert snapshot == {
        "project": "demo",
        "sourcePath": "",
        "contentHash": result["contentHash"],
        "builtAtCommit": "abc123",
        "nodeCount": 2,
        "edgeCount": 1,
        "hyperedgeCount": 1,
        "importedAt": snapshot["importedAt"],
    }


def test_structural_graph_edge_order_is_stable_across_replacements(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    graph = {
        "nodes": [{"id": str(index)} for index in range(9)],
        "links": [
            {
                "source": str(index),
                "target": str((index * 3 + 2) % 9),
                "relation": chr(97 + index),
            }
            for index in range(9)
        ],
    }

    repository.replace_structural_graph(graph, project="demo")
    first = repository.structural_graph(project="demo")
    assert first is not None
    repository.replace_structural_graph(first, project="demo")

    assert repository.structural_graph(project="demo") == first


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


def test_delivery_history_is_isolated_by_project(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    for project in ("product", "release"):
        repository.set_topic(
            "decision.scope",
            value=f"{project} context",
            kind="decision",
            project=project,
        )
        topic = repository.get_topic("decision.scope", project=project)
        assert topic is not None
        repository.record_node_delivery(
            "same-session",
            topic["id"],
            "memory:decision.scope",
            topic["value"],
            project=project,
            session_context={"projectId": project},
        )

    sessions = repository.session_view(session_id="same-session")
    assert {session["project"] for session in sessions} == {"product", "release"}
    assert repository.session_topic_keys(
        "same-session", project="product"
    ) == ["memory:decision.scope"]


def test_session_view_recovers_human_label_after_delivered_node_is_removed(
    tmp_path: Path,
) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    repository.set_topic("reference.dashboard", value="Dashboard", project="demo")
    topic = repository.get_topic("reference.dashboard", project="demo")
    assert topic is not None
    repository.record_node_delivery(
        "session",
        topic["id"],
        "material.49a62f2f278a85507109",
        "## Session dashboard\n\nReadable context content.",
        project="demo",
    )
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT id, payload_json FROM context_events WHERE event_type = 'context.delivered'"
        ).fetchone()
        payload = json.loads(row["payload_json"])
        for field in ("label", "namespace", "kind", "origin", "source"):
            payload.pop(field, None)
        connection.execute(
            "UPDATE context_events SET payload_json = ? WHERE id = ?",
            (json.dumps(payload), row["id"]),
        )
        connection.execute("DELETE FROM context_nodes WHERE id = ?", (topic["id"],))
        connection.commit()

    item = repository.session_view(session_id="session")[0]["items"][0]
    assert item["label"] == "Session dashboard"
    assert item["preview"] == "## Session dashboard\n\nReadable context content."


def test_v6_delivery_history_migrates_to_project_scope(tmp_path: Path) -> None:
    database = tmp_path / "context.db"
    repository = ContextGraphRepository(database)
    repository.set_topic("decision.scope", value="Scoped")
    with repository.connect() as connection:
        connection.executescript(
            """
            DROP TABLE deliveries;
            CREATE TABLE deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                project TEXT,
                key TEXT NOT NULL,
                value_hash TEXT NOT NULL,
                delivered_at INTEGER NOT NULL,
                UNIQUE(session_id, key)
            );
            INSERT INTO deliveries(session_id, project, key, value_hash, delivered_at)
            VALUES ('session', NULL, 'decision.scope', 'old', 1);
            """
        )
        connection.commit()

    migrated = ContextGraphRepository(database)
    migrated.record_delivery(
        "session", "decision.scope", "product", project="product"
    )
    migrated.record_delivery(
        "session", "decision.scope", "release", project="release"
    )

    assert {session["project"] for session in migrated.session_view()} == {
        "",
        "product",
        "release",
    }
