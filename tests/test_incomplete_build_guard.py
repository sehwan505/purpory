"""Tests for the incomplete-build shrink-guard on `purpory extract`.

A full build writes the graph with `to_json(..., force=True)`, which bypasses the
#479 shrink guard. When this run's extraction was incomplete (an AST pass crashed
or some semantic chunks failed), forcing the write can silently overwrite a good
complete graph with a smaller partial one. The build now drops back to the shrink
guard (force=False) on an incomplete run — unless `--allow-partial` is passed —
and exits non-zero (before writing the manifest) if the guard refuses.
"""
from __future__ import annotations

import pytest

import purpory.__main__ as mainmod


def _make_docs_corpus(tmp_path):
    # Docs-only corpus: no code files, so AST extraction is skipped and the only
    # driver of incompleteness is the (stubbed) semantic chunk run.
    (tmp_path / "README.md").write_text("# Notes\nThe entry point overview.\n")
    (tmp_path / "GUIDE.md").write_text("# Guide\nHow to use the thing.\n")
    return tmp_path


def _arm_extract(monkeypatch, tmp_path, *, chunk_total, chunk_succeeded, extra_argv=()):
    corpus = _make_docs_corpus(tmp_path)
    out_dir = tmp_path / "out"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")

    def _stub_corpus(paths, **kwargs):
        on_chunk = kwargs.get("on_chunk_done")
        if on_chunk:
            for i in range(chunk_succeeded):
                on_chunk(i, chunk_total, {"nodes": [], "edges": [], "hyperedges": []})
        return {
            "nodes": [{"id": "s1", "source_file": str(corpus / "README.md"),
                       "file_type": "document", "label": "Notes"}],
            "edges": [], "hyperedges": [], "input_tokens": 10, "output_tokens": 5,
        }

    monkeypatch.setattr("purpory.llm.extract_corpus_parallel", _stub_corpus)
    monkeypatch.setattr(
        mainmod.sys, "argv",
        ["purpory", "extract", str(corpus), "--backend", "claude",
         "--out", str(out_dir), *extra_argv],
    )
    return corpus, out_dir


def test_partial_extraction_refuses_to_shrink_existing_graph(monkeypatch, tmp_path, capsys):
    corpus, out_dir = _arm_extract(
        monkeypatch,
        tmp_path,
        chunk_total=3,
        chunk_succeeded=1,
        extra_argv=["--force"],
    )
    _seed_existing_graph(corpus, 5)

    with pytest.raises(SystemExit) as exc:
        mainmod.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Refusing to overwrite" in err
    assert len(_load_graph(corpus)["nodes"]) == 5
    # The manifest must not be stamped for a graph we declined to write.
    assert not (out_dir / "purpory-out" / "manifest.json").exists()


def test_partial_extraction_writes_when_not_shrinking(monkeypatch, tmp_path):
    corpus, _ = _arm_extract(monkeypatch, tmp_path, chunk_total=3, chunk_succeeded=1)

    mainmod.main()

    assert len(_load_graph(corpus)["nodes"]) == 1


def test_allow_partial_forces_write_despite_incomplete(monkeypatch, tmp_path):
    corpus, _ = _arm_extract(
        monkeypatch,
        tmp_path,
        chunk_total=3,
        chunk_succeeded=1,
        extra_argv=["--force", "--allow-partial"],
    )
    _seed_existing_graph(corpus, 5)

    mainmod.main()

    assert len(_load_graph(corpus)["nodes"]) == 1


def test_complete_extraction_keeps_force_write(monkeypatch, tmp_path):
    corpus, _ = _arm_extract(
        monkeypatch,
        tmp_path,
        chunk_total=1,
        chunk_succeeded=1,
        extra_argv=["--force"],
    )
    _seed_existing_graph(corpus, 5)

    mainmod.main()

    assert len(_load_graph(corpus)["nodes"]) == 1


def _seed_existing_graph(root, n):
    from purpory.supervise.structural import store_structural_graph

    store_structural_graph(
        {
            "nodes": [
                {
                    "id": f"keep{i}",
                    "label": f"k{i}",
                    "source_file": "README.md",
                    "file_type": "document",
                }
                for i in range(n)
            ],
            "links": [],
        },
        root=root,
    )


def _load_graph(root):
    from purpory.supervise.structural import load_structural_graph

    graph = load_structural_graph(root)
    assert graph is not None
    return graph


def _arm_no_cluster(monkeypatch, tmp_path, *, extra_argv=()):
    corpus = _make_docs_corpus(tmp_path)
    out_dir = tmp_path / "out"
    gout = out_dir / "purpory-out"
    _seed_existing_graph(corpus, 5)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")

    def _stub_corpus(paths, **kwargs):
        on_chunk = kwargs.get("on_chunk_done")
        if on_chunk:
            on_chunk(0, 3, {"nodes": [], "edges": [], "hyperedges": []})  # 1 of 3 -> partial
        return {"nodes": [{"id": "s1", "source_file": str(corpus / "README.md"),
                           "file_type": "document", "label": "Notes"}],
                "edges": [], "hyperedges": [], "input_tokens": 1, "output_tokens": 1}

    monkeypatch.setattr("purpory.llm.extract_corpus_parallel", _stub_corpus)
    monkeypatch.setattr(
        mainmod.sys, "argv",
        ["purpory", "extract", str(corpus), "--backend", "claude", "--no-cluster",
         "--out", str(out_dir), *extra_argv],
    )
    return corpus, gout / "graph.json"


def test_no_cluster_incomplete_build_refuses_to_shrink(tmp_path, monkeypatch, capsys):
    corpus, _ = _arm_no_cluster(monkeypatch, tmp_path)

    with pytest.raises(SystemExit) as exc:
        mainmod.main()

    assert exc.value.code == 1
    assert "Refusing to overwrite" in capsys.readouterr().err
    # The existing 5-node graph is untouched — the partial 1-node graph was refused.
    assert len(_load_graph(corpus)["nodes"]) == 5


def test_no_cluster_allow_partial_overwrites(tmp_path, monkeypatch):
    corpus, _ = _arm_no_cluster(monkeypatch, tmp_path, extra_argv=["--allow-partial"])

    with pytest.raises(SystemExit) as exc:
        mainmod.main()

    assert exc.value.code == 0  # the raw --no-cluster path exits 0 on success
    assert len(_load_graph(corpus)["nodes"]) == 1


def test_no_cluster_incomplete_build_fails_closed_on_malformed_existing_graph(
    tmp_path, monkeypatch, capsys
):
    """A present-but-unparseable existing graph.json (corrupt or mid-write) could
    be hiding a complete graph, so an incomplete --no-cluster build must refuse
    to overwrite it — matching to_json's #479 fail-closed handling, not the
    fail-open 'proceed when we can't count' path."""
    corpus, graph = _arm_no_cluster(monkeypatch, tmp_path)
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_text("{corrupt json", encoding="utf-8")  # non-empty, unparseable

    with pytest.raises(SystemExit) as exc:
        mainmod.main()

    assert exc.value.code == 1
    assert "Refusing to overwrite" in capsys.readouterr().err
    assert len(_load_graph(corpus)["nodes"]) == 5
    assert graph.read_text() == "{corrupt json"
