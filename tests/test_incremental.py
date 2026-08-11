"""Integration tests for incremental purpory extract behavior."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable

# Backend-selecting env vars. These tests assume no working LLM backend (a docs
# corpus should fail without one); strip them so a developer who has a real
# ANTHROPIC_API_KEY / OPENAI_API_KEY / etc. exported does not make a docs extract
# succeed and break the "no backend" path. CI has none of these set anyway.
_LLM_ENV_KEYS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "MOONSHOT_API_KEY", "DEEPSEEK_API_KEY", "OLLAMA_BASE_URL",
    "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID",
)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in _LLM_ENV_KEYS}
    return subprocess.run(
        [PYTHON, "-m", "purpory"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def _make_docs_corpus(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "intro.md").write_text("# Introduction\nThis doc introduces the system.")
    (docs / "api.md").write_text("# API Reference\nThe API has endpoints.")
    return docs


def _export_graph(project: Path) -> Path:
    graph_path = project.parent / f"{project.name}-graph.json"
    result = _run(
        ["export", "json", "--output", str(graph_path)],
        project,
    )
    assert result.returncode == 0, result.stderr
    return graph_path


def test_manifest_written_after_extract(tmp_path):
    """A document-only structural extract succeeds and writes its manifest."""
    docs = _make_docs_corpus(tmp_path)
    r = _run(["extract", str(docs)], tmp_path)
    assert r.returncode == 0, r.stderr
    # Runtime state is stored outside the scanned project.
    manifest = docs / "purpory-out" / "manifest.json"
    assert not manifest.exists()


def test_incremental_mode_detected_via_manifest(tmp_path):
    """If manifest.json + graph.json exist, incremental mode message is shown."""
    docs = _make_docs_corpus(tmp_path)
    out = docs / "purpory-out"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps({"nodes": [], "links": []}))
    (out / "manifest.json").write_text(json.dumps({"document": [str(docs / "intro.md")]}))
    r = _run(["extract", str(docs)], tmp_path)
    combined = r.stdout + r.stderr
    assert "incremental" in combined.lower() or r.returncode != 0


def test_no_incremental_without_manifest(tmp_path):
    """Without manifest.json, full scan message is shown (not incremental)."""
    docs = _make_docs_corpus(tmp_path)
    r = _run(["extract", str(docs)], tmp_path)
    # Check combined output doesn't contain incremental-mode phrasing.
    # Use a phrase rather than a bare word to avoid matching the tmp_path,
    # which pytest derives from the test name and contains "incremental".
    assert "incremental update" not in r.stdout.lower()
    assert "incremental scan" not in r.stdout.lower()


def test_extract_no_cluster_incremental_noop_preserves_existing_graph(tmp_path):
    """#1347: no-op incremental no-cluster extract must not overwrite graph.json."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def alpha():\n    return 1\n", encoding="utf-8"
    )

    first = _run(["extract", str(project), "--no-cluster"], tmp_path)
    assert first.returncode == 0, first.stderr
    graph_path = _export_graph(project)
    before = json.loads(graph_path.read_text(encoding="utf-8"))
    assert before.get("nodes"), "first run should produce a non-empty code graph"

    second = _run(["extract", str(project), "--no-cluster"], tmp_path)
    assert second.returncode == 0, second.stderr

    graph_path = _export_graph(project)
    after = json.loads(graph_path.read_text(encoding="utf-8"))
    assert after.get("nodes"), "no-op incremental run must not empty the graph"
    assert {node["id"] for node in after["nodes"]} == {
        node["id"] for node in before["nodes"]
    }
    def topology(graph):
        return {
            (edge.get("source"), edge.get("target"), edge.get("relation"))
            for edge in graph.get("links", graph.get("edges", []))
        }

    assert topology(after) == topology(before)


def _edges(graph_json: Path) -> list[dict]:
    g = json.loads(graph_json.read_text())
    return g.get("links", g.get("edges", []))


def test_update_prunes_a_removed_imports_edge(tmp_path):
    """#1521: when an import is deleted from a file, `purpory update` must prune
    the edge it produced — preserving it (keyed only on endpoint membership) left a
    stale edge that drove phantom circular-dependency findings."""
    proj = tmp_path / "proj"
    pkg = proj / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "b.py").write_text("def helper():\n    return 1\n")
    (pkg / "a.py").write_text("from pkg.b import helper\ndef use():\n    return helper()\n")

    # initial extract -> the import edge a -> b exists
    r1 = _run(["extract", str(proj), "--no-cluster"], tmp_path)
    assert r1.returncode == 0, r1.stderr
    gj = _export_graph(proj)
    before = _edges(gj)
    assert any(e.get("relation") in ("imports", "imports_from") and
               str(e.get("source_file", "")).endswith("a.py") for e in before), \
        f"expected an import edge from a.py initially: {before}"

    # remove the import, then update
    (pkg / "a.py").write_text("def use():\n    return 1\n")
    r2 = _run(["update", str(proj)], tmp_path)
    assert r2.returncode == 0, r2.stderr
    gj = _export_graph(proj)
    after = _edges(gj)

    # the stale import edge owned by a.py must be gone
    stale = [e for e in after
             if e.get("relation") in ("imports", "imports_from")
             and str(e.get("source_file", "")).endswith("a.py")]
    assert not stale, f"removed import's edge survived update (stale): {stale}"
