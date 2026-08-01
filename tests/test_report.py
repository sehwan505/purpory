import json
from pathlib import Path
from purpory.build import build_from_json
from purpory.cluster import cluster, score_all
from purpory.analyze import god_nodes, surprising_connections
from purpory.report import generate

FIXTURES = Path(__file__).parent / "fixtures"

def make_inputs():
    extraction = json.loads((FIXTURES / "extraction.json").read_text())
    G = build_from_json(extraction)
    communities = cluster(G)
    cohesion = score_all(G, communities)
    labels = {cid: f"Community {cid}" for cid in communities}
    gods = god_nodes(G)
    surprises = surprising_connections(G)
    detection = {"total_files": 4, "total_words": 62400, "needs_graph": True, "warning": None}
    tokens = {"input": extraction["input_tokens"], "output": extraction["output_tokens"]}
    return G, communities, cohesion, labels, gods, surprises, detection, tokens

def test_report_contains_header():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "# Graph Report" in report

def test_report_contains_corpus_check():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Corpus Check" in report

def test_report_contains_god_nodes():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## God Nodes" in report

def test_report_contains_surprising_connections():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Surprising Connections" in report

def test_report_contains_communities():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Communities" in report

def test_report_contains_ambiguous_section():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Ambiguous Edges" in report

def test_report_shows_token_cost():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "Token cost" in report
    assert "1,200" in report

def test_report_shows_raw_cohesion_scores():
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project", min_community_size=1)
    assert "Cohesion:" in report
    assert "✓" not in report
    assert "⚠" not in report


def test_import_cycles_section_present_for_code_corpus():
    # #1657: the fixture is a code corpus, so the Import Cycles section shows.
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Import Cycles" in report


def test_import_cycles_section_absent_for_documents_only_corpus():
    # #1657: a documents-only corpus has no imports; the section is pure noise
    # ("None detected") and must be suppressed.
    extraction = {
        "nodes": [
            {"id": "d1", "label": "intro.md", "file_type": "document"},
            {"id": "d2", "label": "guide.md", "file_type": "document"},
        ],
        "edges": [{"source": "d1", "target": "d2", "relation": "references"}],
        "input_tokens": 0, "output_tokens": 0,
    }
    G = build_from_json(extraction)
    communities = cluster(G)
    cohesion = score_all(G, communities)
    labels = {cid: f"Community {cid}" for cid in communities}
    gods = god_nodes(G)
    surprises = surprising_connections(G)
    detection = {"total_files": 2, "total_words": 100, "needs_graph": True, "warning": None}
    tokens = {"input": 0, "output": 0}
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project")
    assert "## Import Cycles" not in report


def test_report_hubs_are_plain_text_by_default():
    # #1712: without --obsidian the _COMMUNITY_*.md notes don't exist, so wikilinks
    # would dangle (and pollute an Obsidian vault's graph view). Default to plain text.
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    labels = {cid: f"Widget {cid}" for cid in communities}
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project", min_community_size=1)
    assert "## Community Hubs (Navigation)" in report
    assert "[[_COMMUNITY_" not in report, "must not emit dangling Obsidian wikilinks by default (#1712)"
    assert any(f"- Widget {cid}" in report for cid in communities)


def test_report_hubs_use_wikilinks_when_obsidian():
    # The opt-in path keeps the vault-navigable wikilink form.
    G, communities, cohesion, labels, gods, surprises, detection, tokens = make_inputs()
    labels = {cid: f"Widget {cid}" for cid in communities}
    report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens, "./project", min_community_size=1, obsidian=True)
    assert "[[_COMMUNITY_" in report
