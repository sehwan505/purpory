from __future__ import annotations

import sys

import pytest

import purpory.__main__ as mainmod


@pytest.mark.parametrize(
    "flag",
    ("--backend=ollama", "--model=qwen3.5:9b", "--mode=deep", "--dedup-llm"),
)
def test_extract_rejects_removed_model_features(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["purpory", "extract", str(tmp_path), flag])

    with pytest.raises(SystemExit) as error:
        mainmod.main()

    assert error.value.code == 2
    assert "used only for session reconciliation" in capsys.readouterr().err


def test_extract_structurally_parses_documents_without_calling_a_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Notes\n", encoding="utf-8")
    monkeypatch.setattr(
        "purpory.llm._call_llm",
        lambda *_args, **_kwargs: pytest.fail("structural extraction called a model"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["purpory", "extract", str(tmp_path), "--no-cluster"],
    )

    with pytest.raises(SystemExit) as result:
        mainmod.main()

    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "structural extraction on 2 files" in output
