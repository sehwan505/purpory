"""Structural extraction ignores non-code files and never needs an LLM key."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PYTHON = sys.executable
_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
             "ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "DEEPSEEK_API_KEY")


def _mixed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def hello():\n    return 1\n")
    (repo / "README.md").write_text("# Design\n\nHow it works.\n")
    (repo / "NOTES.txt").write_text("Architecture notes and rationale.\n")
    return repo


def _run(repo: Path, *extra: str):
    env = {k: v for k, v in os.environ.items() if k not in _KEY_VARS}
    return subprocess.run(
        [PYTHON, "-m", "purpory", "extract", ".", *extra],
        cwd=repo, capture_output=True, text=True, env=env,
    )


def test_code_only_succeeds_without_key(tmp_path):
    repo = _mixed_repo(tmp_path)
    r = _run(repo, "--code-only")
    assert r.returncode == 0, f"--code-only should succeed with no key: {r.stderr}"
    out = r.stdout + r.stderr
    assert "model-backed extraction was removed" in out
    assert not (repo / "purpory-out").exists()
    graph = repo / "graph.json"
    exported = subprocess.run(
        [PYTHON, "-m", "purpory", "export", "json", "--output", str(graph)],
        cwd=repo,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k not in _KEY_VARS},
    )
    assert exported.returncode == 0, exported.stderr
    import json
    g = json.loads(graph.read_text())
    labels = [n.get("label") for n in g["nodes"]]
    assert any(str(l).startswith("hello") for l in labels), "code was indexed"


def test_mixed_repo_without_key_uses_structural_extraction(tmp_path):
    repo = _mixed_repo(tmp_path)
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    assert "model-backed extraction was removed" in r.stdout
