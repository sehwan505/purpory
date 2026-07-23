"""Production-conscious stdlib HTTP adapter for the context service."""

from __future__ import annotations

import hmac
import json
import mimetypes
import queue
import secrets
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from purpory.supervise.library import ContextService

MAX_BODY_BYTES = 1_048_576
READ_TOKEN_BYTES = 24
WRITE_TOKEN_BYTES = 32
AGENT_TOKEN_BYTES = 32
STATIC_ROOT = Path(__file__).with_name("static")
AGENT_MUTATION_PATHS = frozenset(
    {
        "/api/context/prepare",
    }
)


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: set[queue.Queue[str]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[str]:
        channel: queue.Queue[str] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue[str]) -> None:
        with self._lock:
            self._subscribers.discard(channel)

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        message = f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(message)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(message)
                except queue.Empty:
                    pass


class ContextHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: ContextService,
        read_token: str,
        write_token: str,
        agent_token: str | None = None,
    ) -> None:
        super().__init__(server_address, ContextRequestHandler)
        self.service = service
        self.read_token = read_token
        self.write_token = write_token
        self.agent_token = agent_token or write_token
        self.events = EventBroker()


class ContextRequestHandler(BaseHTTPRequestHandler):
    server: ContextHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # Log only the method and sanitized path; never tokens, values, or request bodies.
        split = urlsplit(self.path)
        print(f"[purpory] {self.command or '-'} {split.path}")

    def do_GET(self) -> None:
        split = urlsplit(self.path)
        if split.path.startswith("/api/"):
            if not self._authorized_read(split.query):
                self._error(HTTPStatus.UNAUTHORIZED, "invalid read token")
                return
            try:
                self._handle_get(split.path, parse_qs(split.query))
            except (KeyError, ValueError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except OSError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
            return
        self._serve_static(split.path)

    def do_HEAD(self) -> None:
        split = urlsplit(self.path)
        if split.path.startswith("/api/viz/"):
            if not self._authorized_read(split.query):
                self._error(HTTPStatus.UNAUTHORIZED, "invalid read token", head_only=True)
                return
            self._serve_viz(split.path, head_only=True)
            return
        if split.path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "API endpoint not found", head_only=True)
            return
        self._serve_static(split.path, head_only=True)

    def do_POST(self) -> None:
        self._handle_mutation("POST")

    def do_DELETE(self) -> None:
        self._handle_mutation("DELETE")

    def _handle_mutation(self, method: str) -> None:
        split = urlsplit(self.path)
        if not split.path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if parse_qs(split.query).get("t"):
            self._error(HTTPStatus.FORBIDDEN, "query tokens cannot authorize mutations")
            return
        if not self._same_origin_request():
            self._error(HTTPStatus.FORBIDDEN, "cross-origin mutation rejected")
            return
        if not self._authorized_mutation(split.path):
            self._error(HTTPStatus.UNAUTHORIZED, "invalid mutation token")
            return
        try:
            payload = self._read_json() if method == "POST" else {}
            self._handle_write(method, split.path, payload)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except OSError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))

    def _authorized_read(self, query: str) -> bool:
        supplied = self.headers.get("X-Purpory-Read-Token", "")
        if not supplied:
            supplied = parse_qs(query).get("t", [""])[0]
        return hmac.compare_digest(supplied, self.server.read_token)

    def _authorized_mutation(self, path: str) -> bool:
        write_supplied = self.headers.get("X-Purpory-Token", "")
        if hmac.compare_digest(write_supplied, self.server.write_token):
            return True
        if path in AGENT_MUTATION_PATHS:
            agent_supplied = self.headers.get("X-Purpory-Agent-Token", "")
            return hmac.compare_digest(agent_supplied, self.server.agent_token)
        return False

    def _same_origin_request(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        expected_hosts = {
            self.headers.get("Host", ""),
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }
        parsed = urlsplit(origin)
        return parsed.scheme == "http" and parsed.netloc in expected_hosts

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError(f"request body exceeds {MAX_BODY_BYTES} bytes")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("content type must be application/json")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _handle_get(self, path: str, query: dict[str, list[str]]) -> None:
        service = self.server.service
        if path == "/api/view":
            session_id = _first(query, "session")
            since_raw = _first(query, "since")
            self._json(
                service.view(
                    session_id=session_id,
                    since=int(since_raw) if since_raw else None,
                )
            )
        elif path.startswith("/api/topics/"):
            key = unquote(path.removeprefix("/api/topics/"))
            try:
                self._json(service.topic(key))
            except KeyError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
        elif path == "/api/recall":
            self._json(service.recall(session_id=_first(query, "session")))
        elif path == "/api/graph":
            self._json(
                service.graph(
                    scope=_first(query, "scope"),
                    node_limit=_query_integer(query, "limit", default=200, minimum=1, maximum=500),
                    edge_limit=_query_integer(
                        query, "edgeLimit", default=500, minimum=1, maximum=2_000
                    ),
                )
            )
        elif path == "/api/requests":
            self._json(service.requests(status=_first(query, "status")))
        elif path == "/api/context/decisions":
            self._json(
                service.context_decisions(
                    limit=_query_integer(query, "limit", default=100, minimum=1, maximum=1_000)
                )
            )
        elif path == "/api/model/status":
            self._json(service.model_status())
        elif path == "/api/health":
            self._json({"ok": True, **service.repository.diagnostics()})
        elif path == "/api/stream":
            self._stream_events()
        elif path.startswith("/api/viz/"):
            self._serve_viz(path)
        else:
            self._error(HTTPStatus.NOT_FOUND, "API endpoint not found")

    def _handle_write(self, method: str, path: str, payload: dict[str, Any]) -> None:
        service = self.server.service
        if method == "POST" and path == "/api/topics":
            result = service.set_topic(
                str(payload.get("key", "")),
                value=_optional_string(payload, "value"),
                source=_optional_string(payload, "source"),
                kind=str(payload.get("kind", "note")),
            )
            self.server.events.publish("topic", result)
            self._json(
                result,
                status=HTTPStatus.CREATED if result["action"] == "created" else HTTPStatus.OK,
            )
        elif method == "DELETE" and path.startswith("/api/topics/"):
            key = unquote(path.removeprefix("/api/topics/"))
            deleted = service.delete_topic(key)
            if not deleted:
                self._error(HTTPStatus.NOT_FOUND, "topic not found")
                return
            self.server.events.publish("topic", {"key": key, "action": "deleted"})
            self._json({"ok": True})
        elif method == "POST" and path.startswith("/api/topics/") and path.endswith("/confirm"):
            key = unquote(path.removeprefix("/api/topics/").removesuffix("/confirm"))
            if not service.confirm_topic(key):
                self._error(HTTPStatus.NOT_FOUND, "topic not found")
                return
            self.server.events.publish("topic", {"key": key, "action": "confirmed"})
            self._json({"ok": True})
        elif method == "POST" and path == "/api/seed":
            graph = _optional_string(payload, "graph")
            result = service.seed(
                graph,
                labels_path=_optional_string(payload, "labels"),
                per_community=_bounded_integer(
                    payload, "perCommunity", default=3, minimum=1, maximum=100
                ),
                prune=bool(payload.get("prune", True)),
            )
            self.server.events.publish("seed", result)
            self._json(result)
        elif method == "POST" and path.startswith("/api/requests/") and path.endswith("/resolve"):
            request_id = int(
                path.removeprefix("/api/requests/").removesuffix("/resolve").strip("/")
            )
            key = str(payload.get("key", ""))
            if not service.resolve_request(request_id, key):
                self._error(HTTPStatus.NOT_FOUND, "open request not found")
                return
            result = {"ok": True, "id": request_id, "resolvedKey": key}
            self.server.events.publish("request", result)
            self._json(result)
        elif method == "POST" and path == "/api/context/prepare":
            message = str(payload.get("message", ""))
            retain_input = bool(payload.get("retainInput", False))
            if retain_input and not hmac.compare_digest(
                self.headers.get("X-Purpory-Token", ""), self.server.write_token
            ):
                self._error(
                    HTTPStatus.FORBIDDEN,
                    "retaining preparation input requires the human mutation token",
                )
                return
            result = service.prepare(
                message,
                session_id=_optional_string(payload, "sessionId"),
                project=_optional_string(payload, "project"),
                working_directory=_optional_string(payload, "workingDirectory"),
                active_paths=_string_list(payload, "activePaths", maximum=32),
                token_budget=_bounded_integer(
                    payload,
                    "tokenBudget",
                    default=2_000,
                    minimum=128,
                    maximum=32_768,
                ),
                retain_input=retain_input,
            )
            self.server.events.publish(
                "context",
                {"id": result["decisionId"], "action": result["action"]},
            )
            self._json(result)
        elif (
            method == "POST"
            and path.startswith("/api/context/decisions/")
            and path.endswith("/feedback")
        ):
            decision_id = int(
                path.removeprefix("/api/context/decisions/").removesuffix("/feedback").strip("/")
            )
            result = service.context_feedback(
                decision_id,
                verdict=str(payload.get("verdict", "")),
                expected_action=_optional_string(payload, "expectedAction"),
                expected_keys=_string_list(payload, "expectedKeys", maximum=100),
                note=_optional_string(payload, "note"),
            )
            self.server.events.publish("context", {"id": decision_id, "feedback": True})
            self._json(result)
        else:
            self._error(HTTPStatus.NOT_FOUND, "API endpoint not found")

    def _stream_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        channel = self.server.events.subscribe()
        try:
            self.wfile.write(b'event: ready\ndata: {"ok":true}\n\n')
            self.wfile.flush()
            while True:
                try:
                    message = channel.get(timeout=20)
                except queue.Empty:
                    message = ": heartbeat\n\n"
                self.wfile.write(message.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.server.events.unsubscribe(channel)

    def _serve_static(self, path: str, *, head_only: bool = False) -> None:
        relative = path.lstrip("/") or "index.html"
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            self._error(HTTPStatus.NOT_FOUND, "not found", head_only=head_only)
            return
        candidate = (STATIC_ROOT / Path(*pure.parts)).resolve()
        if not candidate.is_file() and "." not in pure.name:
            candidate = STATIC_ROOT / "index.html"
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "not found", head_only=head_only)
            return
        if not candidate.is_file():
            self._error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "dashboard assets are missing; run `npm --prefix ui run build`",
                head_only=head_only,
            )
            return
        content = b"" if head_only else candidate.read_bytes()
        content_length = candidate.stat().st_size if head_only else len(content)
        mime_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header(
            "Cache-Control",
            "no-cache" if candidate.name == "index.html" else "public, max-age=31536000, immutable",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'",
        )
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _serve_viz(self, path: str, *, head_only: bool = False) -> None:
        from purpory.paths import out_path

        filename = path.removeprefix("/api/viz/")
        pure = PurePosixPath(filename)
        if pure.is_absolute() or ".." in pure.parts or pure.suffix.lower() != ".html":
            self._error(HTTPStatus.NOT_FOUND, "not found", head_only=head_only)
            return
        candidate = (out_path() / Path(*pure.parts)).resolve()
        try:
            candidate.relative_to(out_path().resolve())
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "not found", head_only=head_only)
            return
        if not candidate.is_file():
            self._error(
                HTTPStatus.NOT_FOUND,
                f"{filename} is not generated yet",
                head_only=head_only,
            )
            return
        try:
            content = b"" if head_only else candidate.read_bytes()
            content_length = candidate.stat().st_size if head_only else len(content)
        except OSError as exc:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"read error: {exc}",
                head_only=head_only,
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cdn.jsdelivr.net https://d3js.org; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com",
        )
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _json(self, value: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _error(self, status: HTTPStatus, message: str, *, head_only: bool = False) -> None:
        if not head_only:
            self._json({"error": message}, status=status)
            return
        content = json.dumps({"error": message}, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _bounded_integer(
    payload: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return parsed


def _query_integer(
    query: dict[str, list[str]],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = _first(query, key)
    if value is None:
        return default
    return _bounded_integer({key: value}, key, default=default, minimum=minimum, maximum=maximum)


def _string_list(payload: dict[str, Any], key: str, *, maximum: int) -> list[str]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be an array of strings")
    if len(value) > maximum:
        raise ValueError(f"{key} cannot contain more than {maximum} items")
    return value


def serve_dashboard(
    service: ContextService,
    *,
    port: int = 0,
    open_browser: bool = True,
) -> None:
    if port < 0 or port > 65_535:
        raise ValueError("port must be between 0 and 65535")
    read_token = secrets.token_urlsafe(READ_TOKEN_BYTES)
    write_token = secrets.token_urlsafe(WRITE_TOKEN_BYTES)
    agent_token = secrets.token_urlsafe(AGENT_TOKEN_BYTES)
    server = ContextHTTPServer(("127.0.0.1", port), service, read_token, write_token, agent_token)
    url = (
        f"http://127.0.0.1:{server.server_port}/?t={read_token}"
        f"#write={write_token}&agent={agent_token}"
    )
    print(url, flush=True)
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
