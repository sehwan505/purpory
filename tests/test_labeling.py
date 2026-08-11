from __future__ import annotations

import json
import sys

import purpory.__main__ as mainmod


def test_label_command_uses_deterministic_hub_names(tmp_path, monkeypatch) -> None:
    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "directed": False,
                "multigraph": False,
                "nodes": [
                    {"id": "orders", "label": "OrderService", "source_file": "app.py"}
                ],
                "links": [],
            }
        ),
        encoding="utf-8",
    )
    stored: dict = {}
    monkeypatch.setattr(
        "purpory.supervise.structural.store_structural_graph",
        lambda snapshot, **_kwargs: stored.update(snapshot),
    )
    monkeypatch.setattr(
        "purpory.llm._call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("labeling called a model")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["purpory", "label", str(tmp_path), "--graph", str(graph)],
    )

    mainmod.main()

    assert stored["nodes"][0]["community_name"] == "OrderService"
