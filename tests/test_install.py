"""Supported Claude Code and Codex project integration tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


LEGACY_AGENTS = (
    "gemini",
    "cursor",
    "codebuddy",
    "opencode",
    "kilo",
    "aider",
    "copilot",
    "vscode",
    "claw",
    "droid",
    "trae",
    "antigravity",
    "hermes",
    "kiro",
    "pi",
    "devin",
)


def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    from purpory.__main__ import main

    monkeypatch.setattr(sys, "argv", ["purpory", *args])
    main()


def _third_party_hook(command: str = "third-party prepare") -> dict[str, object]:
    return {"hooks": [{"type": "command", "command": command}]}


def test_reconcile_skill_uses_an_evidence_gate_without_a_fixed_count() -> None:
    from purpory import install

    skill = Path(install.__file__).parent / "skills" / "purpory-reconcile" / "SKILL.md"
    content = skill.read_text(encoding="utf-8")

    assert all(signal in content for signal in ("Grounded", "Durable", "Consequential"))
    assert "Do not impose a fixed count" in content
    assert "importance score" in content
    assert "zero to three" not in content.lower()


def test_help_lists_only_supported_agent_integrations(monkeypatch, capsys):
    _run_main(monkeypatch, "--help")
    output = capsys.readouterr().out

    assert "claude" in output
    assert "codex" in output
    for agent in LEGACY_AGENTS:
        assert f"{agent} install" not in output


def test_claude_round_trip_is_idempotent_and_preserves_user_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Project\n\nKeep this rule.\n", encoding="utf-8")
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Read"]},
                "hooks": {
                    "UserPromptSubmit": [_third_party_hook()],
                    "PreToolUse": [_third_party_hook("third-party guard")],
                },
            }
        ),
        encoding="utf-8",
    )

    _run_main(monkeypatch, "claude", "install", "--project")
    _run_main(monkeypatch, "claude", "install", "--project")

    instructions = claude_md.read_text(encoding="utf-8")
    assert instructions.startswith("# Project")
    assert instructions.count("## purpory") == 1
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    prompt_hooks = settings["hooks"]["UserPromptSubmit"]
    assert len([hook for hook in prompt_hooks if "preflight claude" in str(hook)]) == 1
    assert _third_party_hook() in prompt_hooks
    assert settings["hooks"]["PreToolUse"] == [_third_party_hook("third-party guard")]
    assert settings["permissions"] == {"allow": ["Read"]}
    skill = tmp_path / ".claude" / "skills" / "purpory-reconcile" / "SKILL.md"
    assert skill.is_file()
    assert skill.read_text(encoding="utf-8").startswith("---\nname: purpory-reconcile")

    _run_main(monkeypatch, "claude", "uninstall", "--project")

    assert claude_md.read_text(encoding="utf-8") == "# Project\n\nKeep this rule.\n"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["hooks"]["UserPromptSubmit"] == [_third_party_hook()]
    assert settings["hooks"]["PreToolUse"] == [_third_party_hook("third-party guard")]
    assert "purpory" not in str(settings["hooks"])
    assert not skill.exists()


def test_codex_round_trip_is_idempotent_and_preserves_user_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Local instructions\n\nKeep this too.\n", encoding="utf-8")
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [_third_party_hook()]}}),
        encoding="utf-8",
    )

    _run_main(monkeypatch, "codex", "install", "--project")
    _run_main(monkeypatch, "codex", "install", "--project")

    instructions = agents_md.read_text(encoding="utf-8")
    assert instructions.startswith("# Local instructions")
    assert instructions.count("## purpory") == 1
    settings = json.loads(hooks_path.read_text(encoding="utf-8"))
    prompt_hooks = settings["hooks"]["UserPromptSubmit"]
    assert len([hook for hook in prompt_hooks if "preflight codex" in str(hook)]) == 1
    assert _third_party_hook() in prompt_hooks
    skill = tmp_path / ".agents" / "skills" / "purpory-reconcile" / "SKILL.md"
    assert skill.is_file()

    _run_main(monkeypatch, "codex", "uninstall", "--project")

    assert agents_md.read_text(encoding="utf-8") == "# Local instructions\n\nKeep this too.\n"
    settings = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert settings["hooks"]["UserPromptSubmit"] == [_third_party_hook()]
    assert "purpory" not in str(settings["hooks"])
    assert not skill.exists()


@pytest.mark.parametrize("agent", LEGACY_AGENTS)
def test_removed_agent_commands_are_not_dispatched(tmp_path, monkeypatch, agent):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, agent, "install")
    assert not any(tmp_path.iterdir())


def test_unknown_integration_option_fails_without_writing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, "claude", "install", "--global")
    assert exc.value.code == 2
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("agent", "instructions", "config_dir"),
    (("claude", "CLAUDE.md", ".claude"), ("codex", "AGENTS.md", ".codex")),
)
def test_uninstall_removes_files_created_only_for_integration(
    tmp_path, monkeypatch, agent, instructions, config_dir
):
    monkeypatch.chdir(tmp_path)
    _run_main(monkeypatch, agent, "install", "--project")
    _run_main(monkeypatch, agent, "uninstall", "--project")

    assert not (tmp_path / instructions).exists()
    assert not (tmp_path / config_dir).exists()
