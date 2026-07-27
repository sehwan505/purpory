from __future__ import annotations

import json
from pathlib import Path

from purpory.supervise.library import ContextService


def _write_graph(root: Path) -> None:
    output = root / "purpory-out"
    output.mkdir()
    (output / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "service",
                        "label": "Service",
                        "type": "class",
                        "source_file": "src/service.py",
                    }
                ],
                "links": [],
            }
        ),
        encoding="utf-8",
    )


def test_nested_working_directory_resolves_to_repository_identity(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    nested = root / "src" / "feature"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()

    service = ContextService(db_path=tmp_path / "context.db", root=nested)

    assert service.root == root.resolve()
    assert service.project_id == str(root.resolve())


def test_environment_override_is_the_single_default_project_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PURPORY_PROJECT_ID", "team/project")
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path)

    service.prepare("self-contained request", session_id="session-a")

    assert service.project_id == "team/project"
    assert service.context_decisions()[0]["project"] == "team/project"


def test_explicit_prepare_project_scopes_graph_and_audit_together(tmp_path: Path) -> None:
    _write_graph(tmp_path)
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path)

    service.prepare(
        "Where is Service?",
        session_id="session-a",
        project="alternate/project",
    )

    assert service.context_decisions()[0]["project"] == "alternate/project"
    assert service.repository.graph_snapshot(project="alternate/project")["nodeCount"] == 1
