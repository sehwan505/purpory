"""Project-scoped access to the canonical structural graph."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from purpory.supervise.identity import resolve_project_id, resolve_project_root
from purpory.supervise.repository import ContextGraphRepository


def project_state_directory(root: str | Path) -> Path:
    project_root = resolve_project_root(root)
    project_id = resolve_project_id(project_root)
    configured = os.environ.get("PURPORY_STATE_DIR", "").strip()
    base = Path(configured).expanduser() if configured else ContextGraphRepository().path.parent
    return base / "projects" / hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16]


def load_structural_graph(root: str | Path) -> dict[str, Any] | None:
    project_root = resolve_project_root(root)
    repository = ContextGraphRepository()
    return repository.structural_graph(project=resolve_project_id(project_root))


def store_structural_graph(
    graph: dict[str, Any],
    *,
    root: str | Path,
) -> dict[str, Any]:
    project_root = resolve_project_root(root)
    repository = ContextGraphRepository()
    return repository.replace_structural_graph(
        graph,
        project=resolve_project_id(project_root),
    )
