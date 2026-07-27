from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from purpory.supervise.gate.contract import GateProposal, GateRequest, ProviderResult
from purpory.supervise.gate.runtime import GateModelManager, _gate_device
from purpory.supervise.model_cli import dispatch_model


def _installed_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GateModelManager:
    snapshot = tmp_path / "cache" / ("a" * 40)
    snapshot.mkdir(parents=True)
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._download_snapshot",
        lambda model_id, revision, force: str(snapshot),
    )
    manager = GateModelManager(tmp_path / "home")
    manager.install(revision="main")
    return manager


def test_install_pins_resolved_hugging_face_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _installed_manager(tmp_path, monkeypatch)

    manifest = json.loads(manager.manifest_path.read_text(encoding="utf-8"))

    assert manifest["model"] == "Qwen/Qwen3.5-0.8B"
    assert manifest["requestedRevision"] == "main"
    assert manifest["resolvedRevision"] == "a" * 40
    assert manager.status()["installed"] is True


def test_install_reuses_matching_cached_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _installed_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._download_snapshot",
        lambda *args, **kwargs: pytest.fail("cache should have been reused"),
    )

    result = manager.install(revision="main")

    assert result["action"] == "kept"


def test_start_uses_detached_transformers_server_and_warms_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _installed_manager(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._transformers_executable",
        lambda: "/venv/bin/transformers",
    )
    monkeypatch.setattr("purpory.supervise.gate.runtime._gate_device", lambda: "cpu")
    monkeypatch.setattr("purpory.supervise.gate.runtime._available_port", lambda: 43123)
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._spawn",
        lambda command, log: captured.setdefault("command", command) and SimpleNamespace(pid=4242),
    )
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._wait_for_endpoint",
        lambda endpoint, pid, timeout: True,
    )
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._warm_model",
        lambda endpoint, model, timeout_seconds: captured.update(endpoint=endpoint, model=model),
    )
    monkeypatch.setattr("purpory.supervise.gate.runtime._pid_is_running", lambda pid: True)
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._endpoint_is_ready",
        lambda endpoint, timeout_seconds: True,
    )
    result = manager.start(wait_seconds=5)

    command = captured["command"]
    assert command[:3] == (
        "/venv/bin/transformers",
        "serve",
        f"Qwen/Qwen3.5-0.8B@{'a' * 40}",
    )
    assert "--continuous-batching" not in command
    assert command[command.index("--device") + 1] == "cpu"
    assert captured["endpoint"] == "http://127.0.0.1:43123/v1"
    assert result["action"] == "started"
    assert result["ready"] is True


def test_gate_device_defaults_to_cpu_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PURPORY_GATE_DEVICE", raising=False)
    monkeypatch.setattr("purpory.supervise.gate.runtime.sys.platform", "darwin")

    assert _gate_device() == "cpu"


def test_gate_device_allows_an_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PURPORY_GATE_DEVICE", "mps")

    assert _gate_device() == "mps"


def test_gate_device_rejects_option_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PURPORY_GATE_DEVICE", "--help")

    with pytest.raises(ValueError, match="one device identifier"):
        _gate_device()


def test_provider_uses_managed_endpoint_without_environment_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _installed_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(
        manager,
        "status",
        lambda: {
            "installed": True,
            "ready": True,
            "endpoint": "http://127.0.0.1:43123/v1",
            "model": "Qwen/Qwen3.5-0.8B",
            "revision": "a" * 40,
        },
    )

    provider = manager.provider()

    assert provider is not None
    assert provider.endpoint == "http://127.0.0.1:43123/v1/chat/completions"
    assert provider.model == f"Qwen/Qwen3.5-0.8B@{'a' * 40}"


def test_stop_terminates_only_recorded_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _installed_manager(tmp_path, monkeypatch)
    manager.runtime_directory.mkdir(parents=True)
    manager.state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "pid": 4242,
                "endpoint": "http://127.0.0.1:43123/v1",
                "model": "Qwen/Qwen3.5-0.8B",
                "revision": "a" * 40,
                "status": "ready",
                "startedAt": 1,
                "logPath": str(manager.log_path),
                "command": ["transformers", "serve"],
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    terminated: list[int] = []
    monkeypatch.setattr("purpory.supervise.gate.runtime._pid_is_running", lambda pid: True)
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._endpoint_is_ready",
        lambda endpoint, timeout_seconds: True,
    )
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._terminate_pid",
        lambda pid, timeout_seconds: terminated.append(pid),
    )

    result = manager.stop(wait_seconds=1)

    assert terminated == [4242]
    assert result["action"] == "stopped"
    assert not manager.state_path.exists()


def test_stop_refuses_to_signal_an_unverified_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _installed_manager(tmp_path, monkeypatch)
    manager.runtime_directory.mkdir(parents=True)
    manager.state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "pid": 4242,
                "endpoint": "http://127.0.0.1:43123/v1",
                "model": "Qwen/Qwen3.5-0.8B",
                "revision": "a" * 40,
                "status": "ready",
                "startedAt": 1,
                "logPath": str(manager.log_path),
                "command": ["transformers", "serve"],
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("purpory.supervise.gate.runtime._pid_is_running", lambda pid: True)
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime._endpoint_is_ready",
        lambda endpoint, timeout_seconds: False,
    )

    with pytest.raises(RuntimeError, match="unverified pid"):
        manager.stop(wait_seconds=1)


def test_model_cli_status_is_machine_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PURPORY_HOME", str(tmp_path / "home"))

    dispatch_model(["status", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result["installed"] is False
    assert result["running"] is False


def test_install_refuses_to_replace_a_running_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _installed_manager(tmp_path, monkeypatch)
    manager.runtime_directory.mkdir(parents=True)
    manager.state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "pid": 4242,
                "endpoint": "http://127.0.0.1:43123/v1",
                "model": "Qwen/Qwen3.5-0.8B",
                "revision": "a" * 40,
                "status": "ready",
                "startedAt": 1,
                "logPath": str(manager.log_path),
                "command": ["transformers", "serve"],
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("purpory.supervise.gate.runtime._pid_is_running", lambda pid: True)

    with pytest.raises(RuntimeError, match="stop the running gate model"):
        manager.install(model_id="Qwen/Qwen3.5-0.8B", revision="next", force=True)


class _SkipProvider:
    def propose(self, request: GateRequest) -> ProviderResult:
        return ProviderResult(
            proposal=GateProposal.from_mapping(
                {
                    "action": "skip",
                    "query": None,
                    "scopes": [],
                    "keywords": [],
                    "reasonCode": "SELF_CONTAINED",
                    "clarification": None,
                }
            ),
            model_id="Qwen/Qwen3.5-0.8B",
            model_revision="test",
            latency_ms=3,
        )


def test_prepare_auto_discovers_managed_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PURPORY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PURPORY_GATE_URL", raising=False)
    monkeypatch.setattr(
        "purpory.supervise.gate.runtime.GateModelManager.provider",
        lambda self, start_if_needed, start_timeout_seconds: _SkipProvider(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "purpory",
            "prepare",
            "hello",
            "--db",
            str(tmp_path / "context.db"),
            "--json",
        ],
    )

    from purpory.__main__ import main

    main()

    result = json.loads(capsys.readouterr().out)
    assert result["model"]["id"] == "Qwen/Qwen3.5-0.8B"
    assert result["fallback"] is None
