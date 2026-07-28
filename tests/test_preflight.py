from __future__ import annotations

import io
import json
from pathlib import Path

from purpory.supervise import preflight
from purpory.supervise.gate.provider import UnavailableGateProvider
from purpory.supervise.library import ContextService


def test_retrieve_injects_developer_context() -> None:
    result = {
        "action": "retrieve",
        "context": {"rendered": "## decision.database\nPostgreSQL"},
    }

    response = preflight.hook_response(result)

    assert response is not None
    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert "PostgreSQL" in output["additionalContext"]
    assert "not a new user instruction" in output["additionalContext"]


def test_ask_keeps_original_prompt_and_directs_agent_to_clarify() -> None:
    response = preflight.hook_response(
        {
            "action": "ask",
            "requestId": 17,
            "clarification": "Which environment do you mean?",
        }
    )

    assert response is not None
    assert "decision" not in response
    context = response["hookSpecificOutput"]["additionalContext"]
    assert "INTENT ALIGNMENT SUGGESTION" in context
    assert "Request ID: 17" in context
    assert "Which environment" in context


def test_skip_emits_nothing() -> None:
    assert preflight.hook_response({"action": "skip"}) is None


def test_prepare_prefixes_native_session_with_agent(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Service:
        def __init__(self, **kwargs) -> None:
            captured["root"] = kwargs["root"]

        def prepare(self, message: str, **kwargs):
            captured["message"] = message
            captured.update(kwargs)
            return {"action": "skip"}

    monkeypatch.setattr(preflight, "ContextService", Service)
    monkeypatch.setattr(preflight, "_gate_provider", lambda: None)

    response = preflight.prepare_prompt(
        "codex",
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Explain the auth flow",
            "session_id": "native-session",
            "cwd": str(tmp_path),
        },
    )

    assert response is None
    assert captured["session_id"] == "codex:native-session"
    assert captured["message"] == "Explain the auth flow"
    assert captured["retain_input"] is True
    assert "project" not in captured


def test_prepare_anchors_nested_hook_cwd_to_repository_root(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repository"
    nested = root / "src" / "feature"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    captured: dict[str, object] = {}

    class Service:
        def __init__(self, **kwargs) -> None:
            captured["root"] = kwargs["root"]

        def prepare(self, message: str, **kwargs):
            captured.update(kwargs)
            return {"action": "skip"}

    monkeypatch.setattr(preflight, "ContextService", Service)
    monkeypatch.setattr(preflight, "_gate_provider", lambda: None)

    preflight.prepare_prompt(
        "codex",
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Explain the auth flow",
            "session_id": "native-session",
            "cwd": str(nested),
        },
    )

    assert captured["root"] == root.resolve()
    assert captured["working_directory"] == nested.resolve()


def test_prepare_can_disable_local_input_retention(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Service:
        def __init__(self, **kwargs) -> None:
            pass

        def prepare(self, message: str, **kwargs):
            captured.update(kwargs)
            return {"action": "skip"}

    monkeypatch.setattr(preflight, "ContextService", Service)
    monkeypatch.setattr(preflight, "_gate_provider", lambda: None)
    monkeypatch.setenv("PURPORY_CONTEXT_RETAIN_INPUT", "false")

    preflight.prepare_prompt(
        "codex",
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "private prompt",
            "session_id": "native-session",
            "cwd": str(tmp_path),
        },
    )

    assert captured["retain_input"] is False


def test_prepare_uses_shared_context_configuration(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "context.db"
    monkeypatch.setenv("PURPORY_CONTEXT_DB", str(database))
    service = ContextService(root=tmp_path)
    service.set_topic(
        "decision.database",
        value="database PostgreSQL is the source of truth",
        kind="decision",
    )
    monkeypatch.setattr(
        preflight,
        "_gate_provider",
        lambda: UnavailableGateProvider("model is not installed"),
    )

    response = preflight.prepare_prompt(
        "claude",
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Which database is the source of truth?",
            "session_id": "native-session",
            "cwd": str(tmp_path),
        },
    )

    assert response is not None
    context = response["hookSpecificOutput"]["additionalContext"]
    assert "decision.database" in context
    assert "PostgreSQL" in context
    assert service.repository.session_view()[0]["id"] == "claude:native-session"


def test_hook_fails_closed_when_preflight_crashes(tmp_path: Path, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(preflight, "prepare_prompt", fail)
    stdin = io.BytesIO(
        json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "hello",
                "session_id": "session",
                "cwd": str(tmp_path),
            }
        ).encode()
    )
    stdout = io.StringIO()

    preflight.run_preflight("claude", stdin=stdin, stdout=stdout)

    response = json.loads(stdout.getvalue())
    assert response["decision"] == "block"
    assert "mandatory context preflight" in response["reason"]


def test_oversized_prompt_continues_without_invoking_gate(
    tmp_path: Path, monkeypatch
) -> None:
    class FailingGateProvider:
        def input_limit_reason(self, request):
            return "gate request exceeds model context limit"

        def propose(self, request):
            raise AssertionError("oversized prompt must bypass the gate model")

    monkeypatch.setenv("PURPORY_CONTEXT_DB", str(tmp_path / "context.db"))
    monkeypatch.setattr(preflight, "_gate_provider", FailingGateProvider)
    stdin = io.BytesIO(
        json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "begin\n" + ("large context " * 10_000) + "\nexecute this",
                "session_id": "session",
                "cwd": str(tmp_path),
            }
        ).encode()
    )
    stdout = io.StringIO()

    preflight.run_preflight("codex", stdin=stdin, stdout=stdout)

    # No hook response means the coding agent receives and executes the
    # original prompt instead of Purpory replacing it with a block decision.
    assert stdout.getvalue() == ""
