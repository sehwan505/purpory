"""Use the shared Ollama runtime for Purpory's local gate model."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from purpory.ollama import ollama_urls
from purpory.supervise.gate.provider import GateProvider
from purpory.supervise.gate.qwen import (
    DEFAULT_MODEL,
    DEFAULT_RECONCILE_MODEL,
    RECOMMENDED_GATE_MODELS,
    RECOMMENDED_RECONCILE_MODELS,
    QwenGateProvider,
)

DEFAULT_START_TIMEOUT_SECONDS = 300.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
MAX_HTTP_RESPONSE_BYTES = 1_048_576


@dataclass(frozen=True)
class ModelInstallation:
    """One model reported by Ollama's inventory."""

    model_id: str
    resolved_revision: str


def _configured_model() -> str:
    return configured_model("gate")


def _model_config_path() -> Path:
    home = os.environ.get("PURPORY_HOME", "").strip()
    return (Path(home).expanduser() if home else Path.home() / ".purpory") / "models.json"


def _model_config() -> dict[str, str]:
    try:
        value = json.loads(_model_config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in {"gate", "reconcile"} and isinstance(item, str)
    } if isinstance(value, dict) else {}


def configured_model(role: str) -> str:
    defaults = {"gate": DEFAULT_MODEL, "reconcile": DEFAULT_RECONCILE_MODEL}
    if role not in defaults:
        raise ValueError(f"unsupported role: {role}")
    environment = f"PURPORY_{role.upper()}_MODEL"
    model = os.environ.get(environment, "").strip() or _model_config().get(role, "").strip()
    model = model or defaults[role]
    if not model or len(model) > 255 or any(character.isspace() for character in model):
        raise ValueError(f"{environment} must be one Ollama model name")
    return model


def _save_model(role: str, model: str) -> None:
    config = _model_config()
    config[role] = model
    path = _model_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _request_json(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    root, _ = ollama_urls()
    parsed = urlsplit(root)
    if parsed.hostname is None:  # ollama_urls validates this; keep the type boundary explicit.
        raise RuntimeError("Ollama URL omitted a hostname")
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout_seconds)
    encoded = json.dumps(body, separators=(",", ":")).encode("utf-8") if body else None
    try:
        connection.request(
            method,
            path,
            body=encoded,
            headers={"Content-Type": "application/json"} if encoded else {},
        )
        response = connection.getresponse()
        raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        if len(raw) > MAX_HTTP_RESPONSE_BYTES:
            raise RuntimeError("Ollama response exceeded the size limit")
        if response.status >= 400:
            try:
                error = json.loads(raw or b"{}").get("error")
            except (AttributeError, json.JSONDecodeError):
                error = None
            detail = f": {error}" if isinstance(error, str) and error.strip() else ""
            raise RuntimeError(f"Ollama returned HTTP {response.status}{detail}")
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise RuntimeError("Ollama returned a non-object response")
        return value
    except (HTTPException, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Ollama is unavailable: {exc}") from exc
    finally:
        connection.close()


def _models(*, timeout_seconds: float = 0.25) -> list[dict[str, Any]]:
    value = _request_json("GET", "/api/tags", timeout_seconds=timeout_seconds)
    models = value.get("models", [])
    return [model for model in models if isinstance(model, dict)] if isinstance(models, list) else []


def _find_model(models: list[dict[str, Any]], selected: str) -> dict[str, Any] | None:
    aliases = {selected}
    if ":" not in selected.rsplit("/", 1)[-1]:
        aliases.add(selected + ":latest")
    return next(
        (
            model
            for model in models
            if str(model.get("name") or model.get("model") or "") in aliases
        ),
        None,
    )


class GateModelManager:
    """Manage the gate model in the same Ollama daemon used by other local models."""

    def __init__(self, home: str | Path | None = None) -> None:
        # Kept for API compatibility; Ollama owns model files and process state.
        self.home = Path(home).expanduser() if home is not None else None

    def installation(self) -> ModelInstallation | None:
        selected = _configured_model()
        model = _find_model(_models(), selected)
        if model is None:
            return None
        return ModelInstallation(selected, str(model.get("digest") or "unknown"))

    def install(
        self,
        *,
        model_id: str = DEFAULT_MODEL,
        revision: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if revision:
            raise ValueError("Ollama models use tags; include the tag in --model")
        selected = model_id.strip()
        if not selected or len(selected) > 255 or any(character.isspace() for character in selected):
            raise ValueError("model must be one Ollama model name")
        current = _find_model(_models(timeout_seconds=2.0), selected)
        if current is not None and not force:
            return {"action": "kept", **self.status(model=selected)}
        _request_json(
            "POST",
            "/api/pull",
            body={"model": selected, "stream": False},
            timeout_seconds=1_800.0,
        )
        return {"action": "installed", **self.status(model=selected)}

    def start(
        self,
        *,
        port: int = 0,
        wait_seconds: float = DEFAULT_START_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        if port:
            raise ValueError("Ollama owns its port; configure OLLAMA_BASE_URL")
        if wait_seconds <= 0 or wait_seconds > 1_800:
            raise ValueError("model start timeout must be between 0 and 1800 seconds")
        status = self.status(timeout_seconds=min(wait_seconds, 2.0))
        if not status["running"]:
            raise RuntimeError("Ollama is not running; start Ollama first")
        if not status["installed"]:
            raise RuntimeError("gate model is not installed; run `purpory model install`")
        return {"action": "kept", **status}

    def stop(self, *, wait_seconds: float = 10.0, force: bool = False) -> dict[str, Any]:
        del force
        if wait_seconds <= 0 or wait_seconds > 120:
            raise ValueError("model stop timeout must be between 0 and 120 seconds")
        return {
            "action": "external-runtime",
            **self.status(),
            "message": "Purpory does not stop the shared Ollama daemon",
        }

    def list_installed_models(self, *, timeout_seconds: float = 0.5) -> list[str]:
        try:
            models = _models(timeout_seconds=timeout_seconds)
            return [
                str(m.get("name") or m.get("model") or "")
                for m in models
                if str(m.get("name") or m.get("model") or "")
            ]
        except RuntimeError:
            return []

    def select_model(self, model_id: str, *, role: str = "gate") -> dict[str, Any]:
        selected = model_id.strip()
        if not selected or len(selected) > 255 or any(character.isspace() for character in selected):
            raise ValueError("model must be one valid model name")
        if role not in {"gate", "reconcile"}:
            raise ValueError(f"unsupported role: {role}")
        if _find_model(_models(timeout_seconds=2.0), selected) is None:
            raise RuntimeError(f"model is not installed: {selected}")
        _save_model(role, selected)
        return self.status(model=selected)

    def status(
        self,
        *,
        model: str | None = None,
        timeout_seconds: float = 0.25,
    ) -> dict[str, Any]:
        selected = model or _configured_model()
        _, endpoint = ollama_urls()
        installed_models_list: list[str] = []
        try:
            all_models = _models(timeout_seconds=timeout_seconds)
            installed_models_list = [
                str(m.get("name") or m.get("model") or "")
                for m in all_models
                if str(m.get("name") or m.get("model") or "")
            ]
            installed_model = _find_model(all_models, selected)
        except RuntimeError as exc:
            return {
                "installed": False,
                "running": False,
                "ready": False,
                "model": selected,
                "revision": None,
                "runtime": "ollama",
                "endpoint": endpoint,
                "pid": None,
                "startedAt": None,
                "runtimeModel": None,
                "runtimeRevision": None,
                "logPath": None,
                "error": str(exc),
                "installedModels": [],
                "availablePresets": RECOMMENDED_GATE_MODELS,
                "reconcilePresets": RECOMMENDED_RECONCILE_MODELS,
            }
        digest = str(installed_model.get("digest") or "unknown") if installed_model else None
        return {
            "installed": installed_model is not None,
            "running": True,
            "ready": installed_model is not None,
            "model": selected,
            "revision": digest,
            "runtime": "ollama",
            "endpoint": endpoint,
            "pid": None,
            "startedAt": None,
            "runtimeModel": selected if installed_model else None,
            "runtimeRevision": digest,
            "logPath": None,
            "error": None,
            "installedModels": installed_models_list,
            "availablePresets": RECOMMENDED_GATE_MODELS,
            "reconcilePresets": RECOMMENDED_RECONCILE_MODELS,
        }

    def provider(
        self,
        *,
        start_if_needed: bool = False,
        start_timeout_seconds: float = DEFAULT_START_TIMEOUT_SECONDS,
    ) -> GateProvider | None:
        status = self.status(timeout_seconds=min(start_timeout_seconds, 2.0))
        if start_if_needed and status["running"] and not status["installed"]:
            return None
        if not status["ready"]:
            return None
        timeout_raw = os.environ.get("PURPORY_GATE_TIMEOUT", str(DEFAULT_REQUEST_TIMEOUT_SECONDS))
        try:
            request_timeout = float(timeout_raw)
        except ValueError as exc:
            raise ValueError("PURPORY_GATE_TIMEOUT must be a number") from exc
        return QwenGateProvider(
            base_url=str(status["endpoint"]),
            model=str(status["model"]),
            model_revision=str(status["revision"]),
            api_key=os.environ.get("OLLAMA_API_KEY"),
            timeout_seconds=request_timeout,
        )

    def logs(self, *, lines: int = 100) -> dict[str, Any]:
        if lines < 1 or lines > 1_000:
            raise ValueError("log lines must be between 1 and 1000")
        return {
            "path": None,
            "lines": [],
            "message": "Ollama owns runtime logs",
        }
