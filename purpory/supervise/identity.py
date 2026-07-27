"""Canonical project identity resolution for the context plane."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_project_root(root: str | Path) -> Path:
    """Return the nearest repository root, or the resolved input directory."""
    resolved = Path(root).expanduser().resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return resolved


def resolve_project_id(root: str | Path, explicit: str | None = None) -> str:
    """Return the configured project ID or the canonical repository root."""
    configured = (explicit or os.environ.get("PURPORY_PROJECT_ID", "")).strip()
    return configured or str(resolve_project_root(root))
