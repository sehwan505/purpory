from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from purpory.supervise.identity import resolve_project_id
from purpory.supervise.library import ContextService
from purpory.supervise.repository import ContextGraphRepository
from purpory.supervise.resources import discover_git_resource


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Purpory Test")
    _git(repository, "config", "user.email", "purpory@example.invalid")
    (repository / "README.md").write_text("main\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "initial")
    worktree = tmp_path / "feature-worktree"
    _git(repository, "worktree", "add", "-b", "feature", str(worktree))
    return repository, worktree


def _write_graph(root: Path, node_id: str) -> None:
    source = root / "src"
    source.mkdir()
    (source / "handler.py").write_text(f"# {node_id}\n", encoding="utf-8")
    output = root / "purpory-out"
    output.mkdir()
    (output / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": node_id,
                        "label": node_id,
                        "type": "function",
                        "source_file": "src/handler.py",
                    }
                ],
                "links": [],
            }
        ),
        encoding="utf-8",
    )


def test_project_namespace_accepts_provider_neutral_resources(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    project = repository.create_project_namespace(
        "Research",
        description="Evidence and decisions for a market study.",
    )
    material = tmp_path / "material"
    material.mkdir()

    updated = repository.attach_resource(
        project["id"],
        provider="filesystem",
        resource_kind="document-collection",
        external_identity="research-material-v1",
        label="Research material",
        views=[{"locator": str(material), "revision": "2026-07"}],
    )

    assert updated["kind"] == "project"
    assert updated["resources"][0]["provider"] == "filesystem"
    assert updated["resources"][0]["kind"] == "document-collection"
    binding = repository.resolve_resource_view(material / "notes")
    assert binding is not None
    assert binding["namespaceId"] == project["id"]
    selection = repository.project_resource_selection(project["id"])
    nodes = repository.get_context_nodes(selection["nodeIds"])
    assert {node["namespace"] for node in nodes} == {"context", "resource"}
    edges = repository.adjacent_context_edges(selection["nodeIds"])
    assert {edge["relation"] for edge in edges} == {"contains", "has-view"}


def test_same_resource_can_participate_in_multiple_projects(tmp_path: Path) -> None:
    repository = ContextGraphRepository(tmp_path / "context.db")
    first = repository.create_project_namespace("Launch")
    second = repository.create_project_namespace("Compliance")

    for project in (first, second):
        repository.attach_resource(
            project["id"],
            provider="filesystem",
            resource_kind="document-collection",
            external_identity="shared-policies",
            label="Shared policies",
        )

    assert repository.get_project_namespace(first["id"])["resources"][0]["id"] == (
        repository.get_project_namespace(second["id"])["resources"][0]["id"]
    )


def test_v3_resource_binding_migrates_to_many_to_many(tmp_path: Path) -> None:
    database = tmp_path / "context.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE context_namespaces (
                id TEXT PRIMARY KEY, namespace_kind TEXT NOT NULL, name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', parent_id TEXT,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE context_resources (
                id TEXT PRIMARY KEY, provider TEXT NOT NULL, resource_kind TEXT NOT NULL,
                external_identity TEXT NOT NULL, label TEXT NOT NULL,
                properties_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, UNIQUE(provider, external_identity)
            );
            CREATE TABLE context_namespace_resources (
                namespace_id TEXT NOT NULL, resource_id TEXT NOT NULL UNIQUE,
                alias TEXT, created_at INTEGER NOT NULL,
                PRIMARY KEY(namespace_id, resource_id)
            );
            INSERT INTO context_namespaces VALUES(
                'project_old', 'project', 'Old', '', NULL, 1, 1
            );
            INSERT INTO context_resources VALUES(
                'resource_old', 'filesystem', 'document-collection',
                'shared', 'Shared', '{}', 1, 1
            );
            INSERT INTO context_namespace_resources VALUES(
                'project_old', 'resource_old', NULL, 1
            );
            """
        )
    repository = ContextGraphRepository(database)
    second = repository.create_project_namespace("Second")

    repository.attach_resource(
        second["id"],
        provider="filesystem",
        resource_kind="document-collection",
        external_identity="shared",
        label="Shared",
    )

    assert repository.get_project_namespace(second["id"])["resources"][0]["id"] == (
        "resource_old"
    )


def test_git_provider_discovers_repository_and_all_worktree_views(tmp_path: Path) -> None:
    repository, worktree = _repository_with_worktree(tmp_path)

    discovered = discover_git_resource(repository)

    assert discovered["provider"] == "git"
    assert discovered["resourceKind"] == "repository"
    assert {view["locator"] for view in discovered["views"]} == {
        str(repository.resolve()),
        str(worktree.resolve()),
    }
    assert {view["properties"]["branch"] for view in discovered["views"]} == {
        "main",
        "feature",
    }


def test_git_remote_is_a_resource_before_a_local_view_exists(tmp_path: Path) -> None:
    database = tmp_path / "context.db"
    service = ContextService(db_path=database, root=tmp_path)
    project = service.create_project("Remote resource")

    remote = service.attach_git_resource(
        project["id"],
        "https://github.com/acme/shared.git",
    )

    assert remote["resources"][0]["externalIdentity"] == "github.com/acme/shared"
    assert remote["resources"][0]["views"] == []

    checkout = tmp_path / "shared"
    checkout.mkdir()
    _git(checkout, "init", "-b", "main")
    _git(checkout, "remote", "add", "origin", "git@github.com:acme/shared.git")
    attached = service.attach_git_resource(project["id"], checkout)

    assert len(attached["resources"]) == 1
    assert attached["resources"][0]["views"][0]["locator"] == str(checkout.resolve())


def test_project_memory_is_shared_while_worktree_graphs_stay_separate(
    tmp_path: Path,
) -> None:
    repository, worktree = _repository_with_worktree(tmp_path)
    _write_graph(repository, "main_handler")
    _write_graph(worktree, "feature_handler")
    database = tmp_path / "context.db"

    main_service = ContextService(db_path=database, root=repository)
    project = main_service.create_project("Product")
    attached = main_service.attach_git_resource(
        project["id"],
        repository,
        alias="Primary repository",
    )
    refreshed = main_service.attach_git_resource(project["id"], repository)
    assert refreshed["resources"][0]["alias"] == "Primary repository"
    view_ids = {
        Path(view["locator"]).resolve(): view["id"]
        for view in attached["resources"][0]["views"]
    }

    main_service.set_topic(
        "decision.delivery",
        value="Prefer the smallest reversible change.",
        kind="decision",
    )
    main_service.set_topic(
        "area.handler",
        source="@repo/src/handler.py",
        kind="code-area",
    )
    main_service.sync_graph()
    worktree_service = ContextService(db_path=database, root=worktree)
    worktree_service.sync_graph()

    main_selection = main_service.repository.project_resource_selection(
        project["id"],
        active_view_id=view_ids[repository.resolve()],
    )
    assert any(
        edge["origin"] == "derived"
        and edge["source"]["stableKey"] == view_ids[repository.resolve()]
        and edge["target"]["stableKey"] == "main_handler"
        for edge in main_service.repository.adjacent_context_edges(
            main_selection["nodeIds"]
        )
    )

    assert main_service.project_id == project["id"]
    assert worktree_service.project_id == project["id"]
    assert worktree_service.topic("decision.delivery")["value"] == (
        "Prefer the smallest reversible change."
    )
    assert {
        node["id"]
        for node in main_service.topic("area.handler")["graph"]["godNodes"]
    } == {"main_handler"}
    assert {
        node["id"]
        for node in worktree_service.topic("area.handler")["graph"]["godNodes"]
    } == {"feature_handler"}
    assert view_ids[repository.resolve()] != view_ids[worktree.resolve()]
    main_graph = resolve_project_id(repository)
    feature_graph = resolve_project_id(worktree)
    assert main_graph != feature_graph
    assert main_service.repository.graph_snapshot(project=main_graph)["nodeCount"] == 1
    assert main_service.repository.graph_snapshot(project=feature_graph)["nodeCount"] == 1
    assert {
        node["stableKey"]
        for node in main_service.repository.list_retrieval_nodes(
            project=main_graph,
            memory_project=project["id"],
        )
        if node["namespace"] == "code"
    } == {"main_handler"}
    assert {
        node["stableKey"]
        for node in main_service.repository.list_retrieval_nodes(
            project=feature_graph,
            memory_project=project["id"],
        )
        if node["namespace"] == "code"
    } == {"feature_handler"}


def test_retrieval_combines_selected_views_from_multiple_git_resources(
    tmp_path: Path,
) -> None:
    primary, _ = _repository_with_worktree(tmp_path)
    secondary = tmp_path / "secondary"
    secondary.mkdir()
    _git(secondary, "init", "-b", "main")
    _write_graph(primary, "primary_handler")
    _write_graph(secondary, "secondary_handler")
    service = ContextService(db_path=tmp_path / "context.db", root=primary)
    project = service.create_project("Multi-resource")
    service.attach_git_resource(project["id"], primary)
    service.attach_git_resource(project["id"], secondary, alias="Secondary service")
    service.sync_graph()

    code = service._provisioner().search(
        "secondary_handler",
        session_id="multi-resource",
        scopes=("code",),
    )
    resources = service._provisioner().search(
        "Secondary service",
        session_id="multi-resource",
        scopes=("resource",),
    )

    assert any(item["label"] == "secondary_handler" for item in code["candidates"])
    assert any(item["namespace"] == "resource" for item in resources["candidates"])
    assert len(service.view()["graphProjects"]) == 2


def test_git_resource_is_discovered_and_reused_across_clones(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for checkout in (first, second):
        checkout.mkdir()
        _git(checkout, "init", "-b", "main")
        _git(checkout, "remote", "add", "origin", "git@github.com:example/product.git")
    database = tmp_path / "context.db"

    first_service = ContextService(db_path=database, root=first)
    first_service.set_topic(
        "decision.shared",
        value="Keep project memory isolated but available across clones.",
        kind="decision",
    )
    second_service = ContextService(db_path=database, root=second)

    assert second_service.project_id == first_service.project_id
    assert second_service.topic("decision.shared")["value"].startswith("Keep project memory")
    assert second_service.view()["resourceBinding"]["locator"] == str(second.resolve())
