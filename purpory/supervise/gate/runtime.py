"""Lifecycle management for Purpory's local gate model runtime.

The manager stays standard-library only during ordinary imports. Hugging Face is
loaded lazily by :meth:`GateModelManager.install`, while inference remains in a
separate ``transformers serve`` process so CLI processes never duplicate model
weights in memory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess  # nosec B404
import sys
import time
from collections import deque
from dataclasses import dataclass, replace
from http.client import HTTPConnection, HTTPException
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from purpory.paths import write_json_atomic
from purpory.supervise.gate.provider import GateProvider
from purpory.supervise.gate.qwen import DEFAULT_MODEL, QwenGateProvider

MANIFEST_VERSION = 1
RUNTIME_VERSION = 1
DEFAULT_START_TIMEOUT_SECONDS = 300.0
DEFAULT_STOP_TIMEOUT_SECONDS = 10.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
MAX_HTTP_RESPONSE_BYTES = 1_048_576
MAX_LOG_LINES = 1_000

MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


def default_purpory_home() -> Path:
    configured = os.environ.get("PURPORY_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".purpory"


def _validate_model_id(value: str) -> str:
    model_id = value.strip()
    if not MODEL_ID_RE.fullmatch(model_id):
        raise ValueError("model id must use the Hugging Face owner/model format")
    return model_id


def _validate_revision(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("model revision must be a string")
    revision = value.strip()
    if not REVISION_RE.fullmatch(revision) or ".." in revision.split("/"):
        raise ValueError("model revision contains unsupported characters")
    return revision


def _read_mapping(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid model state file: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"model state must be a JSON object: {path}")
    return value


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    write_json_atomic(path, dict(value), indent=2, ensure_ascii=False)
    try:
        path.chmod(0o600)
    except OSError:
        pass


@dataclass(frozen=True)
class ModelInstallation:
    model_id: str
    requested_revision: str | None
    resolved_revision: str
    snapshot_path: Path
    installed_at: int

    @property
    def canonical_model(self) -> str:
        return f"{self.model_id}@{self.resolved_revision}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": MANIFEST_VERSION,
            "model": self.model_id,
            "requestedRevision": self.requested_revision,
            "resolvedRevision": self.resolved_revision,
            "snapshotPath": str(self.snapshot_path),
            "installedAt": self.installed_at,
            "runtime": "transformers",
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelInstallation":
        if value.get("schemaVersion") != MANIFEST_VERSION:
            raise ValueError("unsupported gate model manifest version")
        snapshot = Path(str(value.get("snapshotPath", ""))).expanduser()
        resolved_revision = _validate_revision(str(value.get("resolvedRevision", "")))
        if resolved_revision is None:
            raise ValueError("model manifest is missing a resolved revision")
        return cls(
            model_id=_validate_model_id(str(value.get("model", ""))),
            requested_revision=_validate_revision(value.get("requestedRevision")),
            resolved_revision=resolved_revision,
            snapshot_path=snapshot,
            installed_at=int(value.get("installedAt", 0)),
        )


@dataclass(frozen=True)
class RuntimeState:
    pid: int
    endpoint: str
    model: str
    revision: str
    status: str
    started_at: int
    log_path: Path
    command: tuple[str, ...]
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": RUNTIME_VERSION,
            "pid": self.pid,
            "endpoint": self.endpoint,
            "model": self.model,
            "revision": self.revision,
            "status": self.status,
            "startedAt": self.started_at,
            "logPath": str(self.log_path),
            "command": list(self.command),
            "error": self.error,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeState":
        if value.get("schemaVersion") != RUNTIME_VERSION:
            raise ValueError("unsupported gate runtime state version")
        command = value.get("command", [])
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ValueError("gate runtime command must be an array of strings")
        pid = int(value.get("pid", 0))
        if pid <= 1:
            raise ValueError("gate runtime state contains an invalid pid")
        endpoint = str(value.get("endpoint", ""))
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("managed gate endpoint must be loopback HTTP")
        status = str(value.get("status", ""))
        if status not in {"starting", "ready", "error"}:
            raise ValueError("gate runtime state contains an invalid status")
        return cls(
            pid=pid,
            endpoint=endpoint.rstrip("/"),
            model=_validate_model_id(str(value.get("model", ""))),
            revision=_validate_revision(str(value.get("revision", ""))) or "",
            status=status,
            started_at=int(value.get("startedAt", 0)),
            log_path=Path(str(value.get("logPath", ""))).expanduser(),
            command=tuple(command),
            error=str(value["error"]) if value.get("error") else None,
        )


class GateModelManager:
    """Install and supervise one local model used by the memory gate."""

    def __init__(self, home: str | Path | None = None) -> None:
        self.home = Path(home).expanduser() if home is not None else default_purpory_home()
        self.model_directory = self.home / "models" / "gate"
        self.runtime_directory = self.home / "runtime"
        self.log_directory = self.home / "logs"
        self.manifest_path = self.model_directory / "manifest.json"
        self.state_path = self.runtime_directory / "gate.json"
        self.log_path = self.log_directory / "gate.log"

    def installation(self) -> ModelInstallation | None:
        value = _read_mapping(self.manifest_path)
        return ModelInstallation.from_mapping(value) if value is not None else None

    def runtime_state(self) -> RuntimeState | None:
        value = _read_mapping(self.state_path)
        return RuntimeState.from_mapping(value) if value is not None else None

    def install(
        self,
        *,
        model_id: str = DEFAULT_MODEL,
        revision: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        selected_model = _validate_model_id(model_id)
        selected_revision = _validate_revision(revision)
        current = self.installation()
        if (
            current is not None
            and current.model_id == selected_model
            and current.requested_revision == selected_revision
            and current.snapshot_path.is_dir()
            and not force
        ):
            return {"action": "kept", **self.status()}
        runtime = self.runtime_state()
        if runtime is not None and _pid_is_running(runtime.pid):
            raise RuntimeError("stop the running gate model before changing its installation")

        snapshot_path = _download_snapshot(
            selected_model,
            selected_revision,
            force=force,
        )
        snapshot = Path(snapshot_path).expanduser().resolve()
        if not snapshot.is_dir():
            raise RuntimeError("Hugging Face returned a missing model snapshot")
        resolved_revision = snapshot.name
        if _validate_revision(resolved_revision) is None:
            raise RuntimeError("could not resolve the installed model revision")
        installation = ModelInstallation(
            model_id=selected_model,
            requested_revision=selected_revision,
            resolved_revision=resolved_revision,
            snapshot_path=snapshot,
            installed_at=int(time.time()),
        )
        _write_private_json(self.manifest_path, installation.as_dict())
        return {"action": "installed", **self.status()}

    def start(
        self,
        *,
        port: int = 0,
        wait_seconds: float = DEFAULT_START_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        if wait_seconds <= 0 or wait_seconds > 1_800:
            raise ValueError("model start timeout must be between 0 and 1800 seconds")
        if port != 0 and not 1_024 <= port <= 65_535:
            raise ValueError("model port must be 0 or between 1024 and 65535")

        installation = self.installation()
        if installation is None or not installation.snapshot_path.is_dir():
            raise RuntimeError("gate model is not installed; run `purpory model install`")

        existing = self.runtime_state()
        if existing is not None and _pid_is_running(existing.pid):
            if (
                existing.model != installation.model_id
                or existing.revision != installation.resolved_revision
            ):
                raise RuntimeError(
                    "a different gate model is running; stop it before starting this installation"
                )
            if _endpoint_is_ready(existing.endpoint, timeout_seconds=0.5):
                return {"action": "kept", **self.status()}
            if _wait_for_endpoint(existing.endpoint, existing.pid, wait_seconds):
                ready = replace(existing, status="ready", error=None)
                _write_private_json(self.state_path, ready.as_dict())
                return {"action": "kept", **self.status()}
            raise RuntimeError(f"gate model process {existing.pid} did not become ready")
        if existing is not None:
            self.state_path.unlink(missing_ok=True)

        executable = _transformers_executable()
        selected_port = port or _available_port()
        endpoint = f"http://127.0.0.1:{selected_port}/v1"
        device = _gate_device()
        command = (
            executable,
            "serve",
            installation.canonical_model,
            "--port",
            str(selected_port),
            "--device",
            device,
            "--reasoning",
            "off",
            "--model-timeout",
            "-1",
        )
        self.log_directory.mkdir(parents=True, exist_ok=True)
        log_handle = self.log_path.open("ab")
        try:
            process = _spawn(command, log_handle)
        finally:
            log_handle.close()

        state = RuntimeState(
            pid=process.pid,
            endpoint=endpoint,
            model=installation.model_id,
            revision=installation.resolved_revision,
            status="starting",
            started_at=int(time.time()),
            log_path=self.log_path,
            command=command,
        )
        _write_private_json(self.state_path, state.as_dict())
        try:
            if not _wait_for_endpoint(endpoint, process.pid, wait_seconds):
                raise RuntimeError(
                    f"gate model did not become ready within {wait_seconds:g} seconds"
                )
            _warm_model(endpoint, installation.canonical_model, timeout_seconds=wait_seconds)
        except Exception as exc:
            _terminate_pid(process.pid, timeout_seconds=2.0)
            failed = replace(state, status="error", error=str(exc))
            _write_private_json(self.state_path, failed.as_dict())
            raise RuntimeError(f"could not start gate model; see {self.log_path}: {exc}") from exc

        ready = replace(state, status="ready", error=None)
        _write_private_json(self.state_path, ready.as_dict())
        return {"action": "started", **self.status()}

    def stop(
        self,
        *,
        wait_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS,
        force: bool = False,
    ) -> dict[str, Any]:
        if wait_seconds <= 0 or wait_seconds > 120:
            raise ValueError("model stop timeout must be between 0 and 120 seconds")
        state = self.runtime_state()
        if state is None:
            return {"action": "not-running", **self.status()}
        if _pid_is_running(state.pid):
            if not force and not _endpoint_is_ready(state.endpoint, timeout_seconds=0.5):
                raise RuntimeError(
                    "refusing to signal an unverified pid; retry with `purpory model stop --force`"
                )
            _terminate_pid(state.pid, timeout_seconds=wait_seconds)
        self.state_path.unlink(missing_ok=True)
        return {"action": "stopped", **self.status()}

    def status(self) -> dict[str, Any]:
        installation = self.installation()
        state = self.runtime_state()
        installed = installation is not None and installation.snapshot_path.is_dir()
        running = state is not None and _pid_is_running(state.pid)
        runtime_matches_installation = bool(
            installation
            and state
            and state.model == installation.model_id
            and state.revision == installation.resolved_revision
        )
        ready = bool(
            running
            and state is not None
            and runtime_matches_installation
            and state.status == "ready"
            and _endpoint_is_ready(state.endpoint, timeout_seconds=0.25)
        )
        return {
            "installed": installed,
            "running": running,
            "ready": ready,
            "model": installation.model_id if installation else None,
            "revision": installation.resolved_revision if installation else None,
            "runtime": "transformers" if installation else None,
            "endpoint": state.endpoint if running and state else None,
            "pid": state.pid if running and state else None,
            "startedAt": state.started_at if running and state else None,
            "runtimeModel": state.model if running and state else None,
            "runtimeRevision": state.revision if running and state else None,
            "logPath": str(self.log_path),
            "error": state.error if state and not running else None,
        }

    def provider(
        self,
        *,
        start_if_needed: bool = False,
        start_timeout_seconds: float = DEFAULT_START_TIMEOUT_SECONDS,
    ) -> GateProvider | None:
        status = self.status()
        if not status["installed"]:
            return None
        if not status["ready"] and start_if_needed:
            status = self.start(wait_seconds=start_timeout_seconds)
        if not status["ready"] or not status["endpoint"]:
            return None
        timeout_raw = os.environ.get(
            "PURPORY_GATE_TIMEOUT", str(DEFAULT_REQUEST_TIMEOUT_SECONDS)
        )
        try:
            request_timeout = float(timeout_raw)
        except ValueError as exc:
            raise ValueError("PURPORY_GATE_TIMEOUT must be a number") from exc
        return QwenGateProvider(
            base_url=str(status["endpoint"]),
            model=f"{status['model']}@{status['revision']}",
            model_revision=str(status["revision"]),
            timeout_seconds=request_timeout,
        )

    def logs(self, *, lines: int = 100) -> dict[str, Any]:
        if lines < 1 or lines > MAX_LOG_LINES:
            raise ValueError(f"log lines must be between 1 and {MAX_LOG_LINES}")
        if not self.log_path.is_file():
            content: list[str] = []
        else:
            with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
                content = list(deque((line.rstrip("\n") for line in handle), maxlen=lines))
        return {"path": str(self.log_path), "lines": content}


def _download_snapshot(model_id: str, revision: str | None, *, force: bool) -> str:
    try:
        snapshot_download = import_module("huggingface_hub").snapshot_download
    except (AttributeError, ImportError) as exc:
        raise RuntimeError(
            "local gate dependencies are missing; install `purpory[gate]`"
        ) from exc
    return snapshot_download(
        repo_id=model_id,
        revision=revision,
        force_download=force,
    )


def _transformers_executable() -> str:
    sibling = Path(sys.executable).with_name(
        "transformers.exe" if os.name == "nt" else "transformers"
    )
    if sibling.is_file():
        return str(sibling)
    executable = shutil.which("transformers")
    if executable:
        return str(executable)
    raise RuntimeError(
        "`transformers serve` is unavailable; install `purpory[gate]` in this environment"
    )


def _gate_device() -> str:
    configured = os.environ.get("PURPORY_GATE_DEVICE", "").strip()
    if configured:
        if (
            len(configured) > 64
            or configured.startswith("-")
            or any(character.isspace() for character in configured)
        ):
            raise ValueError("PURPORY_GATE_DEVICE must be one device identifier")
        return configured
    # Qwen 3.5's bfloat16 weights currently take tens of seconds per tensor to
    # cast onto MPS through Transformers' auto device selection. The gate handles
    # short, serialized classification requests, so CPU is the reliable default
    # on macOS while callers can still opt into MPS explicitly.
    return "cpu" if sys.platform == "darwin" else "auto"


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _spawn(command: tuple[str, ...], log_handle: Any) -> subprocess.Popen[Any]:
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return subprocess.Popen(list(command), **options)  # nosec B603


def _pid_is_running(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    if os.name != "nt":
        try:
            ps_path = next(
                (
                    candidate
                    for candidate in (Path("/bin/ps"), Path("/usr/bin/ps"))
                    if candidate.is_file()
                ),
                None,
            )
            if ps_path is not None:
                res = subprocess.run(
                    [str(ps_path), "-p", str(pid), "-o", "state="],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                state = res.stdout.strip()
                if state and "Z" in state:
                    return False
        except (OSError, subprocess.SubprocessError):
            pass

    return True


def _terminate_pid(pid: int, *, timeout_seconds: float) -> None:
    if not _pid_is_running(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return
        time.sleep(0.05)
    if hasattr(signal, "SIGKILL"):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if _pid_is_running(pid):
        raise RuntimeError(f"gate model process {pid} did not stop")


def _endpoint_is_ready(endpoint: str, *, timeout_seconds: float) -> bool:
    parsed = urlsplit(endpoint.rstrip("/") + "/models")
    if parsed.hostname is None:
        return False
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout_seconds)
    try:
        connection.request("GET", parsed.path)
        response = connection.getresponse()
        response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        return response.status == 200
    except (HTTPException, OSError, TimeoutError):
        return False
    finally:
        connection.close()


def _wait_for_endpoint(endpoint: str, pid: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return False
        if _endpoint_is_ready(endpoint, timeout_seconds=0.5):
            return True
        time.sleep(0.1)
    return False


def _warm_model(endpoint: str, model: str, *, timeout_seconds: float) -> None:
    parsed = urlsplit(endpoint)
    if parsed.hostname is None:
        raise RuntimeError("managed gate endpoint has no hostname")
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout_seconds)
    payload = json.dumps({"model": model}, separators=(",", ":")).encode("utf-8")
    total = 0
    terminal: dict[str, Any] | None = None
    try:
        connection.request(
            "POST",
            "/load_model",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status >= 400:
            response.read(MAX_HTTP_RESPONSE_BYTES + 1)
            raise RuntimeError(f"model loader returned HTTP {response.status}")
        while True:
            line = response.readline()
            if not line:
                break
            total += len(line)
            if total > MAX_HTTP_RESPONSE_BYTES:
                raise RuntimeError("model loader response exceeded the size limit")
            if not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line.removeprefix(b"data:").strip())
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("status") in {"ready", "error"}:
                terminal = event
        if terminal is None:
            raise RuntimeError("model loader ended without a terminal status")
        if terminal.get("status") != "ready":
            raise RuntimeError(str(terminal.get("message") or "model loading failed"))
    except (HTTPException, OSError, TimeoutError) as exc:
        raise RuntimeError(f"model loader unavailable: {exc}") from exc
    finally:
        connection.close()
