"""Shared Ollama endpoint configuration."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


def ollama_urls() -> tuple[str, str]:
    configured = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip()
    parsed = urlsplit(configured)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("managed Ollama must use a loopback HTTP URL")
    if parsed.query or parsed.fragment or (parsed.path.rstrip("/") not in {"", "/v1"}):
        raise ValueError("OLLAMA_BASE_URL must end at the host or /v1")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    authority = f"{host}:{parsed.port}" if parsed.port else str(host)
    root = f"http://{authority}"
    return root, root + "/v1"
