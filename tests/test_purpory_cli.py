from __future__ import annotations

import sys
import json

import pytest

from purpory.__main__ import main
from purpory.supervise.repository import ContextGraphRepository


def test_root_help_is_purpory_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["purpory", "--help"])
    main()
    output = capsys.readouterr().out
    assert output.startswith("Usage: purpory")
    assert "remember <key>" in output
    assert 'prepare "<request>"' in output
    assert "dashboard" in output
    assert "purpory context" not in output
    assert output.count("Usage: purpory") == 1


def test_version_uses_purpory_distribution(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["purpory", "--version"])
    main()
    assert capsys.readouterr().out.startswith("purpory ")


def test_removed_context_command_is_rejected(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["purpory", "context"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 1
    assert "unknown command 'context'" in capsys.readouterr().err


def test_prepare_emits_machine_readable_context_result(monkeypatch, capsys, tmp_path) -> None:
    database = tmp_path / "context.db"
    ContextGraphRepository(database).set_topic(
        "decision.database", value="database PostgreSQL", kind="decision"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "purpory",
            "prepare",
            "database",
            "--session",
            "cli-session",
            "--db",
            str(database),
            "--json",
            "--no-model-start",
        ],
    )

    main()

    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "retrieve"
    assert result["proposal"]["reasonCode"] == "GATE_UNAVAILABLE"
    assert ContextGraphRepository(database).list_gate_decisions()[0]["inputText"] == "database"


def test_remember_stores_human_context(monkeypatch, capsys, tmp_path) -> None:
    database = tmp_path / "context.db"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "purpory",
            "remember",
            "decision.database",
            "--value",
            "PostgreSQL",
            "--kind",
            "decision",
            "--db",
            str(database),
            "--json",
        ],
    )

    main()

    assert json.loads(capsys.readouterr().out)["action"] == "created"
    topic = ContextGraphRepository(database).get_topic("decision.database")
    assert topic is not None
    assert topic["value"] == "PostgreSQL"


def test_remember_lists_and_atomically_applies_project_batch(
    monkeypatch, capsys, tmp_path
) -> None:
    database = tmp_path / "context.db"
    root = tmp_path / "project"
    root.mkdir()
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            {
                "changes": [
                    {
                        "key": "intent.product.simplicity",
                        "kind": "decision",
                        "value": "Keep the product simple and consistent.",
                        "expectedHash": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "purpory",
            "remember",
            "--batch",
            str(batch),
            "--root",
            str(root),
            "--db",
            str(database),
            "--json",
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out)["applied"] is False

    monkeypatch.setattr(sys, "argv", [*sys.argv, "--apply"])
    main()
    assert json.loads(capsys.readouterr().out)["applied"] is True

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "purpory",
            "remember",
            "--list",
            "--prefix",
            "intent",
            "--root",
            str(root),
            "--db",
            str(database),
            "--json",
        ],
    )
    main()
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["key"] == "intent.product.simplicity"
    assert listed[0]["project"] == str(root)
    assert listed[0]["hash"]


def test_update_synchronizes_the_canonical_context_graph(monkeypatch, capsys, tmp_path) -> None:
    from purpory.supervise.library import ContextService
    import purpory.watch

    monkeypatch.setenv("PURPORY_CONTEXT_DB", str(tmp_path / "context.db"))
    monkeypatch.setattr(purpory.watch, "_rebuild_code", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        ContextService,
        "sync_graph",
        lambda self: {"imported": True, "nodes": 12, "edges": 34},
    )
    monkeypatch.setattr(sys, "argv", ["purpory", "update", str(tmp_path)])

    main()

    assert "Context graph synchronized: 12 nodes, 34 edges." in capsys.readouterr().out
