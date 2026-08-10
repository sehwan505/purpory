from __future__ import annotations

import json
from pathlib import Path

from purpory.supervise.library import ContextService
from purpory.supervise.session_reconcile import (
    HierarchicalReconciler,
    _split_text,
    apply_candidates,
    chunk_messages,
    drain_queue,
    enqueue,
    read_transcript,
)
from purpory.supervise.repository import ContextGraphRepository


class FakeModel:
    context_tokens = 8_192

    def __init__(self) -> None:
        self.maps = 0
        self.reductions = 0

    def extract(self, transcript: str, user_ids: set[str]):
        self.maps += 1
        if not user_ids:
            return []
        return [
            {
                "key": "intent.session.policy",
                "kind": "decision",
                "value": f"policy-{self.maps}",
                "evidenceIds": [sorted(user_ids)[-1]],
            }
        ]

    def consolidate(self, candidates):
        self.reductions += 1
        return {
            "key": candidates[-1]["key"],
            "kind": candidates[-1]["kind"],
            "value": candidates[-1]["value"],
            "evidenceIds": list(
                dict.fromkeys(
                    evidence for item in candidates for evidence in item["evidenceIds"]
                )
            ),
            "sourceIds": [item["id"] for item in candidates],
        }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def test_transcript_reader_normalizes_claude_and_codex_and_ignores_tools(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "keep this"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": "understood"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "correct it"}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "text": "secret output"}]}},
        ],
    )

    messages = read_transcript(transcript)

    assert [(item["role"], item["text"]) for item in messages] == [
        ("user", "keep this"),
        ("assistant", "understood"),
        ("user", "correct it"),
    ]


def test_chunking_preserves_oversized_unicode_and_the_tail() -> None:
    text = "가나다🙂" * 4_000 + "THE-END"
    assert "".join(_split_text(text, 257)) == text
    chunks = chunk_messages([{"id": "U000001", "role": "user", "text": text}], 128)
    assert len(chunks) > 1
    assert all("truncated" not in chunk.lower() for chunk in chunks)
    assert chunks[-1].endswith("THE-END")


def test_hierarchical_reconcile_visits_every_chunk_and_reduces_without_loss() -> None:
    model = FakeModel()
    messages = [
        {"id": f"U{index:06d}", "role": "user", "text": f"rule {index} " + "x" * 12_000}
        for index in range(1, 7)
    ]

    candidates = HierarchicalReconciler(model).propose(messages)

    assert model.maps >= len(messages)
    assert model.reductions >= 1
    assert candidates[0]["value"] == f"policy-{model.maps}"
    assert candidates[0]["evidenceIds"][0] == "U000001"
    assert candidates[0]["evidenceIds"][-1] == "U000006"


def test_enqueue_is_idempotent_and_snapshots_transcript(tmp_path: Path, monkeypatch) -> None:
    queue = tmp_path / "queue"
    monkeypatch.setenv("PURPORY_RECONCILE_DIR", str(queue))
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [{"type": "user", "message": {"role": "user", "content": "remember"}}])
    payload = {
        "hook_event_name": "SessionEnd",
        "session_id": "session-1",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "reason": "exit",
    }

    first = enqueue(payload, agent="codex")
    second = enqueue(payload, agent="codex")

    assert first == second
    job = json.loads(first.read_text(encoding="utf-8"))
    assert Path(job["transcript"]).read_text(encoding="utf-8") == transcript.read_text(encoding="utf-8")


def test_apply_candidates_is_idempotent_and_project_scoped(tmp_path: Path) -> None:
    database = tmp_path / "context.db"
    service = ContextService(db_path=database, root=tmp_path, project_id="demo")
    candidates = [{"key": "intent.ui", "kind": "decision", "value": "Keep it visual."}]

    first = apply_candidates(service, candidates, session_id="session-1")
    second = apply_candidates(service, candidates, session_id="session-1")

    assert first["changes"][0]["action"] == "created"
    assert second["changes"][0]["action"] == "unchanged"
    assert ContextGraphRepository(database).get_topic("intent.ui", project="demo")["value"] == "Keep it visual."


def test_apply_candidates_refreshes_one_concurrent_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    service = ContextService(db_path=tmp_path / "context.db", root=tmp_path, project_id="demo")
    original = service.repository.reconcile_topics
    calls = 0

    def reconcile(changes, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "applied": False,
                "project": "demo",
                "changes": [{"key": changes[0]["key"], "action": "conflict"}],
            }
        return original(changes, **kwargs)

    monkeypatch.setattr(service.repository, "reconcile_topics", reconcile)

    result = apply_candidates(
        service,
        [{"key": "intent.retry", "kind": "decision", "value": "Retry once."}],
        session_id="session-1",
    )

    assert calls == 2
    assert result["changes"][0]["action"] == "created"


def test_failed_drain_keeps_job_for_retry(tmp_path: Path, monkeypatch) -> None:
    queue = tmp_path / "queue"
    monkeypatch.setenv("PURPORY_RECONCILE_DIR", str(queue))
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [{"type": "user", "message": {"role": "user", "content": "remember"}}])
    job = enqueue(
        {
            "hook_event_name": "SessionEnd",
            "session_id": "session-1",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
        },
        agent="claude",
    )

    class BrokenModel(FakeModel):
        def extract(self, transcript: str, user_ids: set[str]):
            raise RuntimeError("model unavailable")

    result = drain_queue(model=BrokenModel())

    assert result["failed"] == 1
    assert job.exists()
    assert job.with_suffix(".error.json").exists()


def test_drain_recovers_a_lock_owned_by_a_dead_worker(tmp_path: Path, monkeypatch) -> None:
    queue = tmp_path / "queue"
    monkeypatch.setenv("PURPORY_RECONCILE_DIR", str(queue))
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [{"type": "user", "message": {"role": "user", "content": "remember"}}])
    job = enqueue(
        {
            "hook_event_name": "SessionEnd",
            "session_id": "session-1",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
        },
        agent="codex",
    )
    lock = job.with_suffix(".lock")
    lock.mkdir()
    (lock / "owner.json").write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")

    result = drain_queue(model=FakeModel())

    assert result["completed"] == 1
    assert not job.exists()
    assert not lock.exists()
