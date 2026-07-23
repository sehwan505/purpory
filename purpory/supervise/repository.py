"""Canonical SQLite repository for Purpory's unified context graph.

Code structure, human knowledge, and session activity share one node/edge model.
Indexed operational projections support bounded reads while graph.json remains an
import/export artifact. The module intentionally depends only on the Python
standard library.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from purpory.supervise.freshness import DEFAULT_STALE_DAYS, is_stale

DEFAULT_DB_PATH = Path.home() / ".purpory" / "context.db"
SCHEMA_VERSION = 1
MEMORY_NAMESPACE = "memory"
TOPIC_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)*$")
TOPIC_KINDS = frozenset({"note", "code-area", "doc-ref", "decision", "seeded"})
TOPIC_ORIGINS = frozenset({"human", "graph-seed"})
REQUEST_STATUSES = frozenset({"open", "resolved"})
GATE_FINAL_ACTIONS = frozenset({"skip", "retrieve", "ask"})
GATE_FEEDBACK_VERDICTS = frozenset({"correct", "incorrect"})


def default_db_path() -> Path:
    configured = os.environ.get("PURPORY_CONTEXT_DB")
    return Path(configured).expanduser() if configured else DEFAULT_DB_PATH


def value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def topic_hash(topic: dict[str, Any]) -> str:
    """Hash only the durable content used for reconciliation conflicts."""
    return value_hash(
        stable_json(
            {
                "kind": topic.get("kind"),
                "source": topic.get("source"),
                "value": topic.get("value"),
            }
        )
    )


def validate_topic_key(key: str) -> str:
    normalized = key.strip()
    if not normalized or len(normalized) > 255 or not TOPIC_KEY_RE.fullmatch(normalized):
        raise ValueError(
            "topic key must be a dot-separated logical address using letters, "
            "numbers, dashes, or underscores"
        )
    return normalized


def _now(timestamp: int | None = None) -> int:
    return int(time.time()) if timestamp is None else int(timestamp)


def _stable_id(namespace: str, project: str, stable_key: str) -> str:
    identity = "\0".join((namespace, project, stable_key))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class ContextGraphRepository:
    """Concurrency-safe repository for the complete local context graph."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS context_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS context_nodes (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    project TEXT NOT NULL DEFAULT '',
                    stable_key TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    value TEXT,
                    source TEXT,
                    origin TEXT NOT NULL,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    external_id TEXT,
                    source_graph TEXT,
                    set_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(namespace, project, stable_key)
                );

                CREATE TABLE IF NOT EXISTS context_edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    confidence TEXT,
                    weight REAL,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES context_nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES context_nodes(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS context_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    subject_id TEXT,
                    object_id TEXT,
                    session_id TEXT,
                    project TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    occurred_at INTEGER NOT NULL,
                    FOREIGN KEY(subject_id) REFERENCES context_nodes(id) ON DELETE SET NULL,
                    FOREIGN KEY(object_id) REFERENCES context_nodes(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS graph_snapshots (
                    project TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    source_size INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    built_at_commit TEXT,
                    node_count INTEGER NOT NULL,
                    edge_count INTEGER NOT NULL,
                    imported_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    project TEXT,
                    key TEXT NOT NULL,
                    value_hash TEXT NOT NULL,
                    delivered_at INTEGER NOT NULL,
                    UNIQUE(session_id, key)
                );

                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    project TEXT,
                    need TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    resolved_key TEXT,
                    created_at INTEGER NOT NULL,
                    resolved_at INTEGER,
                    CHECK (status IN ('open', 'resolved'))
                );

                CREATE TABLE IF NOT EXISTS touches (
                    session_id TEXT NOT NULL,
                    project TEXT,
                    dir TEXT NOT NULL,
                    touched_at INTEGER NOT NULL,
                    PRIMARY KEY(session_id, dir)
                );

                CREATE TABLE IF NOT EXISTS gate_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    project TEXT,
                    input_hash TEXT NOT NULL,
                    input_text TEXT,
                    proposal_json TEXT NOT NULL,
                    final_action TEXT NOT NULL,
                    delivery_json TEXT NOT NULL,
                    request_id INTEGER,
                    model_id TEXT,
                    model_revision TEXT,
                    prompt_version TEXT NOT NULL,
                    latency_ms INTEGER,
                    fallback_reason TEXT,
                    created_at INTEGER NOT NULL,
                    CHECK (final_action IN ('skip', 'retrieve', 'ask')),
                    FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS gate_feedback (
                    decision_id INTEGER PRIMARY KEY,
                    verdict TEXT NOT NULL,
                    expected_action TEXT,
                    expected_keys_json TEXT NOT NULL,
                    note TEXT,
                    created_at INTEGER NOT NULL,
                    CHECK (verdict IN ('correct', 'incorrect')),
                    CHECK (expected_action IS NULL OR expected_action IN ('skip', 'retrieve', 'ask')),
                    FOREIGN KEY(decision_id) REFERENCES gate_decisions(id) ON DELETE CASCADE
                );

                """
            )
            self._initialize_views(connection)
            self._initialize_fts(connection)
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_context_nodes_lookup
                    ON context_nodes(namespace, project, stable_key);
                CREATE INDEX IF NOT EXISTS idx_context_nodes_type
                    ON context_nodes(project, node_type, origin);
                CREATE INDEX IF NOT EXISTS idx_context_nodes_source
                    ON context_nodes(project, source);
                CREATE INDEX IF NOT EXISTS idx_context_edges_source
                    ON context_edges(source_id, relation);
                CREATE INDEX IF NOT EXISTS idx_context_edges_target
                    ON context_edges(target_id, relation);
                CREATE INDEX IF NOT EXISTS idx_context_events_time
                    ON context_events(event_type, occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_deliveries_key_time
                    ON deliveries(key, delivered_at DESC);
                CREATE INDEX IF NOT EXISTS idx_deliveries_session_time
                    ON deliveries(session_id, delivered_at DESC);
                CREATE INDEX IF NOT EXISTS idx_requests_status_time
                    ON requests(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_touches_project_dir
                    ON touches(project, dir);
                CREATE INDEX IF NOT EXISTS idx_gate_decisions_session_time
                    ON gate_decisions(session_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_gate_decisions_action_time
                    ON gate_decisions(final_action, created_at DESC);
                """
            )
            connection.execute(
                "INSERT INTO context_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()

    @staticmethod
    def _initialize_fts(connection: sqlite3.Connection) -> None:
        existed = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'context_nodes_fts'"
        ).fetchone()
        try:
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS context_nodes_fts USING fts5(
                    id UNINDEXED, stable_key, label, source, node_type, value,
                    tokenize = 'unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS context_nodes_fts_insert
                AFTER INSERT ON context_nodes BEGIN
                    INSERT INTO context_nodes_fts(
                        id, stable_key, label, source, node_type, value
                    ) VALUES (
                        new.id, new.stable_key, new.label, new.source,
                        new.node_type, new.value
                    );
                END;
                CREATE TRIGGER IF NOT EXISTS context_nodes_fts_delete
                AFTER DELETE ON context_nodes BEGIN
                    DELETE FROM context_nodes_fts WHERE id = old.id;
                END;
                CREATE TRIGGER IF NOT EXISTS context_nodes_fts_update
                AFTER UPDATE ON context_nodes BEGIN
                    DELETE FROM context_nodes_fts WHERE id = old.id;
                    INSERT INTO context_nodes_fts(
                        id, stable_key, label, source, node_type, value
                    ) VALUES (
                        new.id, new.stable_key, new.label, new.source,
                        new.node_type, new.value
                    );
                END;
                """
            )
            if existed is None:
                connection.execute(
                    """
                    INSERT INTO context_nodes_fts(
                        id, stable_key, label, source, node_type, value
                    )
                    SELECT id, stable_key, label, source, node_type, value
                    FROM context_nodes
                    """
                )
            connection.execute(
                """
                INSERT INTO context_meta(key, value) VALUES('fts5', 'enabled')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )
        except sqlite3.OperationalError:
            connection.execute(
                """
                INSERT INTO context_meta(key, value) VALUES('fts5', 'unavailable')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )

    @staticmethod
    def _initialize_views(connection: sqlite3.Connection) -> None:
        existing = connection.execute(
            "SELECT type FROM sqlite_master WHERE name = 'topics'"
        ).fetchone()
        if existing is not None:
            if existing["type"] != "view":
                raise RuntimeError("context database contains an unsupported pre-release schema")
            return
        connection.execute(
            """
            CREATE VIEW topics AS
            SELECT stable_key AS key, value, source, node_type AS kind, origin,
                   set_at, external_id AS seed_node_id, source_graph AS seed_graph
            FROM context_nodes
            WHERE namespace = 'memory' AND project = ''
            """
        )

    def set_topic(
        self,
        key: str,
        *,
        value: str | None = None,
        source: str | None = None,
        kind: str = "note",
        origin: str = "human",
        set_at: int | None = None,
        seed_node_id: str | None = None,
        seed_graph: str | None = None,
        project: str = "",
    ) -> str:
        key = validate_topic_key(key)
        if (value is None) == (source is None):
            raise ValueError("exactly one of value or source is required")
        if value is not None and not value.strip():
            raise ValueError("value cannot be empty")
        if source is not None and not source.strip():
            raise ValueError("source cannot be empty")
        if kind not in TOPIC_KINDS:
            raise ValueError(f"unsupported topic kind: {kind}")
        if origin not in TOPIC_ORIGINS:
            raise ValueError(f"unsupported topic origin: {origin}")
        normalized_project = project.strip()
        timestamp = _now(set_at)

        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT origin FROM context_nodes
                WHERE namespace = ? AND project = ? AND stable_key = ?
                """,
                (MEMORY_NAMESPACE, normalized_project, key),
            ).fetchone()
            if existing is not None and existing["origin"] == "human" and origin == "graph-seed":
                return "kept"
            action = "created" if existing is None else "updated"
            connection.execute(
                """
                INSERT INTO context_nodes(
                    id, namespace, project, stable_key, node_type, label,
                    value, source, origin, properties_json, external_id,
                    source_graph, set_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, project, stable_key) DO UPDATE SET
                    node_type = excluded.node_type,
                    label = excluded.label,
                    value = excluded.value,
                    source = excluded.source,
                    origin = excluded.origin,
                    set_at = excluded.set_at,
                    external_id = excluded.external_id,
                    source_graph = excluded.source_graph,
                    updated_at = excluded.updated_at
                """,
                (
                    _stable_id(MEMORY_NAMESPACE, normalized_project, key),
                    MEMORY_NAMESPACE,
                    normalized_project,
                    key,
                    kind,
                    key,
                    value,
                    source,
                    origin,
                    seed_node_id if origin == "graph-seed" else None,
                    seed_graph if origin == "graph-seed" else None,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            if origin == "graph-seed" and seed_node_id:
                self._link_seed(connection, key, seed_node_id, seed_graph, timestamp)
            connection.commit()
            return action

    def get_topic(self, key: str, *, project: str = "") -> dict[str, Any] | None:
        key = validate_topic_key(key)
        normalized_project = project.strip()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT stable_key AS key, value, source, node_type AS kind, origin,
                       set_at, external_id AS seed_node_id, source_graph AS seed_graph,
                       project
                FROM context_nodes
                WHERE namespace = ? AND stable_key = ? AND project IN ('', ?)
                ORDER BY CASE WHEN project = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (MEMORY_NAMESPACE, key, normalized_project, normalized_project),
            ).fetchone()
        if row is None:
            return None
        topic = dict(row)
        topic["hash"] = topic_hash(topic)
        return topic

    def list_topics(
        self,
        prefixes: Sequence[str] | None = None,
        *,
        project: str = "",
    ) -> list[dict[str, Any]]:
        normalized_project = project.strip()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT stable_key AS key, value, source, node_type AS kind, origin,
                       set_at, external_id AS seed_node_id, source_graph AS seed_graph,
                       project
                FROM context_nodes
                WHERE namespace = ? AND project IN ('', ?)
                ORDER BY stable_key ASC, CASE WHEN project = ? THEN 0 ELSE 1 END
                """,
                (MEMORY_NAMESPACE, normalized_project, normalized_project),
            ).fetchall()
        topics_by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
            topic = dict(row)
            if topic["key"] in topics_by_key:
                continue
            topic["hash"] = topic_hash(topic)
            topics_by_key[topic["key"]] = topic
        topics = list(topics_by_key.values())
        if not prefixes:
            return topics
        normalized = [validate_topic_key(prefix) for prefix in prefixes]
        return [
            topic
            for topic in topics
            if any(
                topic["key"] == prefix or topic["key"].startswith(prefix + ".")
                for prefix in normalized
            )
        ]

    def reconcile_topics(
        self,
        changes: Sequence[dict[str, Any]],
        *,
        project: str,
        apply: bool = False,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Preview or atomically apply a small project-scoped memory batch."""
        normalized_project = project.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        if not changes:
            raise ValueError("batch must contain at least one change")
        if len(changes) > 20:
            raise ValueError("batch cannot contain more than 20 changes")

        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in changes:
            if not isinstance(raw, dict):
                raise ValueError("each batch change must be an object")
            key = validate_topic_key(str(raw.get("key", "")))
            if key in seen:
                raise ValueError(f"duplicate batch key: {key}")
            seen.add(key)
            value = raw.get("value")
            source = raw.get("source")
            if value is not None and not isinstance(value, str):
                raise ValueError(f"value for {key} must be a string")
            if source is not None and not isinstance(source, str):
                raise ValueError(f"source for {key} must be a string")
            if (value is None) == (source is None):
                raise ValueError(f"exactly one of value or source is required for {key}")
            if value is not None and not value.strip():
                raise ValueError(f"value for {key} cannot be empty")
            if source is not None and not source.strip():
                raise ValueError(f"source for {key} cannot be empty")
            kind = str(raw.get("kind", "note"))
            if kind not in TOPIC_KINDS:
                raise ValueError(f"unsupported topic kind: {kind}")
            expected_hash = raw.get("expectedHash")
            if expected_hash is not None and not isinstance(expected_hash, str):
                raise ValueError(f"expectedHash for {key} must be a string or null")
            if apply and "expectedHash" not in raw:
                raise ValueError(f"expectedHash is required when applying {key}")
            prepared.append(
                {
                    "key": key,
                    "value": value,
                    "source": source,
                    "kind": kind,
                    "expectedHash": expected_hash,
                    "hasExpectedHash": "expectedHash" in raw,
                }
            )

        timestamp = _now()
        with self.connect() as connection:
            if apply:
                connection.execute("BEGIN IMMEDIATE")
            results: list[dict[str, Any]] = []
            for change in prepared:
                row = connection.execute(
                    """
                    SELECT stable_key AS key, value, source, node_type AS kind, origin,
                           set_at, external_id AS seed_node_id, source_graph AS seed_graph,
                           project
                    FROM context_nodes
                    WHERE namespace = ? AND project = ? AND stable_key = ?
                    """,
                    (MEMORY_NAMESPACE, normalized_project, change["key"]),
                ).fetchone()
                before = dict(row) if row is not None else None
                current_hash = topic_hash(before) if before is not None else None
                proposed = {
                    "key": change["key"],
                    "value": change["value"],
                    "source": change["source"],
                    "kind": change["kind"],
                }
                proposed_hash = topic_hash(proposed)
                conflict = bool(
                    apply
                    and change["hasExpectedHash"]
                    and change["expectedHash"] != current_hash
                )
                if conflict:
                    action = "conflict"
                elif current_hash == proposed_hash:
                    action = "unchanged"
                elif before is None:
                    action = "created"
                else:
                    action = "updated"
                results.append(
                    {
                        "key": change["key"],
                        "action": action,
                        "currentHash": current_hash,
                        "proposedHash": proposed_hash,
                        "expectedHash": current_hash,
                    }
                )

            if apply and any(item["action"] == "conflict" for item in results):
                connection.rollback()
                return {"applied": False, "project": normalized_project, "changes": results}

            if apply:
                event_changes: list[dict[str, Any]] = []
                for change, result in zip(prepared, results):
                    if result["action"] == "unchanged":
                        continue
                    before_row = connection.execute(
                        """
                        SELECT value, source, node_type AS kind FROM context_nodes
                        WHERE namespace = ? AND project = ? AND stable_key = ?
                        """,
                        (MEMORY_NAMESPACE, normalized_project, change["key"]),
                    ).fetchone()
                    before = dict(before_row) if before_row is not None else None
                    connection.execute(
                        """
                        INSERT INTO context_nodes(
                            id, namespace, project, stable_key, node_type, label,
                            value, source, origin, properties_json, set_at,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'human', '{}', ?, ?, ?)
                        ON CONFLICT(namespace, project, stable_key) DO UPDATE SET
                            node_type = excluded.node_type,
                            label = excluded.label,
                            value = excluded.value,
                            source = excluded.source,
                            origin = excluded.origin,
                            set_at = excluded.set_at,
                            external_id = NULL,
                            source_graph = NULL,
                            updated_at = excluded.updated_at
                        """,
                        (
                            _stable_id(MEMORY_NAMESPACE, normalized_project, change["key"]),
                            MEMORY_NAMESPACE,
                            normalized_project,
                            change["key"],
                            change["kind"],
                            change["key"],
                            change["value"],
                            change["source"],
                            timestamp,
                            timestamp,
                            timestamp,
                        ),
                    )
                    event_changes.append(
                        {
                            "key": change["key"],
                            "action": result["action"],
                            "before": before,
                            "after": {
                                "kind": change["kind"],
                                "source": change["source"],
                                "value": change["value"],
                            },
                        }
                    )
                if event_changes:
                    self._record_event(
                        connection,
                        "memory.reconciled",
                        session_id=session_id,
                        project=normalized_project,
                        payload={"changes": event_changes},
                        occurred_at=timestamp,
                    )
                connection.commit()
            return {"applied": apply, "project": normalized_project, "changes": results}

    def delete_topic(self, key: str) -> bool:
        key = validate_topic_key(key)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM context_nodes
                WHERE namespace = ? AND project = '' AND stable_key = ?
                """,
                (MEMORY_NAMESPACE, key),
            )
            connection.commit()
            return cursor.rowcount > 0

    def confirm_topic(self, key: str, *, confirmed_at: int | None = None) -> bool:
        key = validate_topic_key(key)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE context_nodes SET set_at = ?, updated_at = ?
                WHERE namespace = ? AND project = '' AND stable_key = ?
                """,
                (
                    _now(confirmed_at),
                    _now(confirmed_at),
                    MEMORY_NAMESPACE,
                    key,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def topic_view(
        self,
        *,
        project: str = "",
        stale_after_days: int = DEFAULT_STALE_DAYS,
        now: int | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                **topic,
                "stale": is_stale(topic["set_at"], now=now, stale_after_days=stale_after_days),
            }
            for topic in self.list_topics(project=project)
        ]

    def import_graph(self, graph_path: str | Path, *, project: str) -> dict[str, Any]:
        """Import a complete graph artifact as the structural graph snapshot."""
        normalized_project = project.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        path = Path(graph_path).expanduser().resolve()
        from purpory.security import check_graph_file_size_cap

        check_graph_file_size_cap(path)
        stat = path.stat()
        with self.connect() as connection:
            snapshot = connection.execute(
                "SELECT * FROM graph_snapshots WHERE project = ?",
                (normalized_project,),
            ).fetchone()
        if (
            snapshot is not None
            and snapshot["source_path"] == str(path)
            and snapshot["source_mtime_ns"] == stat.st_mtime_ns
            and snapshot["source_size"] == stat.st_size
        ):
            return {
                "imported": False,
                "project": normalized_project,
                "nodes": int(snapshot["node_count"]),
                "edges": int(snapshot["edge_count"]),
                "contentHash": snapshot["content_hash"],
            }

        raw_bytes = path.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        value = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("nodes"), list):
            raise ValueError("graph artifact must contain a nodes list")
        nodes = [node for node in value["nodes"] if isinstance(node, dict)]
        links_value = value.get("links", value.get("edges", []))
        links = (
            [link for link in links_value if isinstance(link, dict)]
            if isinstance(links_value, list)
            else []
        )
        timestamp = _now()
        node_rows: list[tuple[Any, ...]] = []
        node_ids: dict[str, str] = {}
        for node in nodes:
            external_id = str(node.get("id", "")).strip()
            if not external_id:
                continue
            node_id = _stable_id("code", normalized_project, external_id)
            node_ids[external_id] = node_id
            node_type = str(node.get("type") or node.get("file_type") or "code.symbol")
            label = str(node.get("label") or external_id)
            source = str(node.get("source_file") or "") or None
            node_rows.append(
                (
                    node_id,
                    "code",
                    normalized_project,
                    external_id,
                    node_type,
                    label,
                    source,
                    stable_json(node),
                    external_id,
                    str(path),
                    timestamp,
                    timestamp,
                    timestamp,
                )
            )

        edge_rows: list[tuple[Any, ...]] = []
        for position, link in enumerate(links):
            source_key = str(link.get("source", ""))
            target_key = str(link.get("target", ""))
            source_id = node_ids.get(source_key)
            target_id = node_ids.get(target_key)
            if source_id is None or target_id is None:
                continue
            relation = str(link.get("relation") or "related")
            edge_identity = stable_json(
                [normalized_project, source_key, target_key, relation, position, link]
            )
            edge_rows.append(
                (
                    hashlib.sha256(edge_identity.encode("utf-8")).hexdigest(),
                    source_id,
                    target_id,
                    relation,
                    "structural",
                    str(link.get("confidence")) if link.get("confidence") is not None else None,
                    _optional_float(link.get("weight")),
                    stable_json(link),
                    timestamp,
                    timestamp,
                )
            )

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM context_nodes WHERE namespace = 'code' AND project = ?",
                (normalized_project,),
            )
            connection.executemany(
                """
                INSERT INTO context_nodes(
                    id, namespace, project, stable_key, node_type, label,
                    source, origin, properties_json, external_id, source_graph,
                    set_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'structural', ?, ?, ?, ?, ?, ?)
                """,
                node_rows,
            )
            connection.executemany(
                """
                INSERT INTO context_edges(
                    id, source_id, target_id, relation, origin, confidence,
                    weight, properties_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                edge_rows,
            )
            connection.execute(
                """
                INSERT INTO graph_snapshots(
                    project, source_path, source_mtime_ns, source_size,
                    content_hash, built_at_commit, node_count, edge_count,
                    imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project) DO UPDATE SET
                    source_path = excluded.source_path,
                    source_mtime_ns = excluded.source_mtime_ns,
                    source_size = excluded.source_size,
                    content_hash = excluded.content_hash,
                    built_at_commit = excluded.built_at_commit,
                    node_count = excluded.node_count,
                    edge_count = excluded.edge_count,
                    imported_at = excluded.imported_at
                """,
                (
                    normalized_project,
                    str(path),
                    stat.st_mtime_ns,
                    stat.st_size,
                    digest,
                    value.get("built_at_commit"),
                    len(node_rows),
                    len(edge_rows),
                    timestamp,
                ),
            )
            self._restore_seed_links(connection, normalized_project, timestamp)
            self._record_event(
                connection,
                "graph.imported",
                project=normalized_project,
                payload={
                    "source": str(path),
                    "contentHash": digest,
                    "nodes": len(node_rows),
                    "edges": len(edge_rows),
                },
                occurred_at=timestamp,
            )
            connection.commit()
        return {
            "imported": True,
            "project": normalized_project,
            "nodes": len(node_rows),
            "edges": len(edge_rows),
            "contentHash": digest,
        }

    def graph_payload(
        self,
        *,
        project: str,
        scope: str | None = None,
        node_limit: int | None = None,
        edge_limit: int | None = None,
    ) -> dict[str, Any]:
        normalized_project = project.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        for name, value in (("node_limit", node_limit), ("edge_limit", edge_limit)):
            if value is not None and (isinstance(value, bool) or value < 1):
                raise ValueError(f"{name} must be a positive integer")
        with self.connect() as connection:
            node_rows = connection.execute(
                """
                SELECT id, stable_key, properties_json FROM context_nodes
                WHERE namespace = 'code' AND project = ?
                ORDER BY stable_key
                """,
                (normalized_project,),
            ).fetchall()
            edge_rows = connection.execute(
                """
                SELECT e.properties_json, source.stable_key AS source_key,
                       target.stable_key AS target_key
                FROM context_edges e
                JOIN context_nodes source ON source.id = e.source_id
                JOIN context_nodes target ON target.id = e.target_id
                WHERE e.origin = 'structural'
                  AND source.project = ? AND target.project = ?
                ORDER BY e.id
                """,
                (normalized_project, normalized_project),
            ).fetchall()
        eligible_nodes: list[dict[str, Any]] = []
        normalized_scope = scope.replace("\\", "/").strip("/") if scope else None
        for row in node_rows:
            node = json.loads(row["properties_json"])
            node["id"] = row["stable_key"]
            if normalized_scope and not _path_in_scope(
                str(node.get("source_file", "")), normalized_scope
            ):
                continue
            eligible_nodes.append(node)

        eligible_ids = {str(node["id"]) for node in eligible_nodes}
        eligible_edges = [
            row
            for row in edge_rows
            if row["source_key"] in eligible_ids and row["target_key"] in eligible_ids
        ]
        if node_limit is not None and len(eligible_nodes) > node_limit:
            degree: dict[str, int] = {}
            for row in eligible_edges:
                degree[str(row["source_key"])] = degree.get(str(row["source_key"]), 0) + 1
                degree[str(row["target_key"])] = degree.get(str(row["target_key"]), 0) + 1
            eligible_nodes.sort(key=lambda node: (-degree.get(str(node["id"]), 0), str(node["id"])))
            nodes = eligible_nodes[:node_limit]
        else:
            nodes = eligible_nodes

        selected_ids = {str(node["id"]) for node in nodes}
        selected_edges = [
            row
            for row in eligible_edges
            if row["source_key"] in selected_ids and row["target_key"] in selected_ids
        ]
        if edge_limit is not None:
            selected_edges = selected_edges[:edge_limit]

        links: list[dict[str, Any]] = []
        for row in selected_edges:
            link = json.loads(row["properties_json"])
            link["source"] = row["source_key"]
            link["target"] = row["target_key"]
            links.append(link)
        return {
            "nodes": nodes,
            "links": links,
            "totalNodes": len(eligible_nodes),
            "totalLinks": len(eligible_edges),
            "truncated": len(nodes) < len(eligible_nodes) or len(links) < len(eligible_edges),
        }

    def list_retrieval_nodes(self, *, project: str) -> list[dict[str, Any]]:
        """Return human memory and structural nodes visible to one project."""
        normalized_project = project.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, namespace, project, stable_key, node_type, label,
                       value, source, origin, properties_json, external_id,
                       source_graph, set_at, created_at, updated_at
                FROM context_nodes
                WHERE (namespace = ? AND project IN ('', ?))
                   OR (namespace = 'code' AND project = ?)
                ORDER BY namespace, stable_key
                """,
                (MEMORY_NAMESPACE, normalized_project, normalized_project),
            ).fetchall()
        nodes = [_context_node(row) for row in rows]
        return _prefer_project_memory(nodes, normalized_project)

    def retrieval_inventory(self, *, project: str) -> dict[str, Any]:
        """Aggregate a compact catalog without loading node content."""
        normalized_project = project.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        visible_topics = self.list_topics(project=normalized_project)
        with self.connect() as connection:
            code_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM context_nodes WHERE namespace = 'code' AND project = ?",
                    (normalized_project,),
                ).fetchone()[0]
            )
            type_rows = connection.execute(
                """
                SELECT node_type, COUNT(*) AS count
                FROM context_nodes
                WHERE namespace = 'code' AND project = ?
                GROUP BY node_type
                ORDER BY count DESC, node_type ASC
                LIMIT 16
                """,
                (normalized_project,),
            ).fetchall()
            topic_rows = connection.execute(
                """
                SELECT stable_key FROM context_nodes
                WHERE namespace = ? AND project IN ('', ?) ORDER BY stable_key
                """,
                (MEMORY_NAMESPACE, normalized_project),
            ).fetchall()
        namespaces: dict[str, int] = {}
        if visible_topics:
            namespaces[MEMORY_NAMESPACE] = len(visible_topics)
        if code_count:
            namespaces["code"] = code_count
        return {
            "namespaces": namespaces,
            "codeTypes": [
                {"name": row["node_type"], "count": int(row["count"])} for row in type_rows
            ],
            "topicKeys": list(dict.fromkeys(str(row["stable_key"]) for row in topic_rows)),
        }

    def search_retrieval_nodes(
        self,
        *,
        project: str,
        terms: Sequence[str],
        active_paths: Sequence[str] = (),
        include_memory: bool = True,
        include_code: bool = True,
        limit: int = 2_000,
    ) -> list[dict[str, Any]]:
        """Generate a bounded FTS/path candidate pool for precise ranking."""
        normalized_project = project.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        normalized_terms = list(
            dict.fromkeys(term.strip().lower() for term in terms if term.strip())
        )
        normalized_paths = list(
            dict.fromkeys(path.strip().lower() for path in active_paths if path.strip())
        )
        parsed_limit = int(limit)
        if parsed_limit < 1 or parsed_limit > 10_000:
            raise ValueError("retrieval candidate limit must be between 1 and 10000")
        if not include_memory and not include_code:
            return []
        select = """
            SELECT node.id, node.namespace, node.project, node.stable_key,
                   node.node_type, node.label, node.value, node.source,
                   node.origin, node.properties_json, node.external_id,
                   node.source_graph, node.set_at, node.created_at,
                   node.updated_at
        """
        by_id: dict[str, dict[str, Any]] = {}
        with self.connect() as connection:
            fts_enabled = connection.execute(
                "SELECT value FROM context_meta WHERE key = 'fts5'"
            ).fetchone()
            fts_query = " OR ".join(
                f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in normalized_terms
            )

            def add_fts_candidates(namespace: str, maximum: int) -> None:
                if maximum <= 0:
                    return
                try:
                    rows = connection.execute(
                        select
                        + """
                        FROM context_nodes_fts search
                        JOIN context_nodes node ON node.id = search.id
                        WHERE context_nodes_fts MATCH ?
                          AND node.namespace = ?
                          AND (
                              (node.namespace = ? AND node.project IN ('', ?))
                              OR (node.namespace = 'code' AND node.project = ?)
                          )
                        ORDER BY bm25(context_nodes_fts), node.stable_key
                        LIMIT ?
                        """,
                        (
                            fts_query,
                            namespace,
                            MEMORY_NAMESPACE,
                            normalized_project,
                            normalized_project,
                            maximum,
                        ),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                by_id.update((str(row["id"]), _context_node(row)) for row in rows)

            can_use_fts = bool(
                normalized_terms and fts_enabled is not None and fts_enabled["value"] == "enabled"
            )
            if can_use_fts and include_memory:
                memory_limit = parsed_limit if not include_code else max(1, parsed_limit // 4)
                add_fts_candidates(MEMORY_NAMESPACE, memory_limit)

            path_budget = (
                max(1, parsed_limit // 4) if normalized_paths and normalized_terms else parsed_limit
            )
            path_added = 0
            for path in normalized_paths if include_code else ():
                remaining = min(path_budget - path_added, parsed_limit - len(by_id))
                if remaining <= 0:
                    break
                rows = connection.execute(
                    select
                    + """
                    FROM context_nodes node
                    WHERE node.namespace = 'code' AND node.project = ?
                      AND instr(lower(COALESCE(node.source, '')), ?) > 0
                    ORDER BY node.stable_key
                    LIMIT ?
                    """,
                    (normalized_project, path, remaining),
                ).fetchall()
                before = len(by_id)
                by_id.update((str(row["id"]), _context_node(row)) for row in rows)
                path_added += len(by_id) - before

            if can_use_fts and include_code:
                add_fts_candidates("code", parsed_limit - len(by_id))
            if not by_id:
                rows = connection.execute(
                    select
                    + """
                    FROM context_nodes node
                    WHERE (? = 1 AND node.namespace = ? AND node.project IN ('', ?))
                       OR (? = 1 AND node.namespace = 'code' AND node.project = ?)
                    ORDER BY node.namespace, node.stable_key
                    LIMIT ?
                    """,
                    (
                        int(include_memory),
                        MEMORY_NAMESPACE,
                        normalized_project,
                        int(include_code),
                        normalized_project,
                        parsed_limit,
                    ),
                ).fetchall()
                by_id.update((str(row["id"]), _context_node(row)) for row in rows)
        return _prefer_project_memory(
            [by_id[node_id] for node_id in sorted(by_id)], normalized_project
        )

    def get_context_nodes(self, node_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Fetch context nodes by canonical id while preserving caller order."""
        normalized = list(dict.fromkeys(node_id.strip() for node_id in node_ids if node_id.strip()))
        if not normalized:
            return []
        if len(normalized) > 200:
            raise ValueError("cannot fetch more than 200 context nodes at once")
        placeholders = ",".join("?" for _ in normalized)
        query = f"""
            SELECT id, namespace, project, stable_key, node_type, label,
                   value, source, origin, properties_json, external_id,
                   source_graph, set_at, created_at, updated_at
            FROM context_nodes
            WHERE id IN ({placeholders})
        """  # nosec B608
        with self.connect() as connection:
            rows = connection.execute(query, normalized).fetchall()
        by_id = {str(row["id"]): _context_node(row) for row in rows}
        return [by_id[node_id] for node_id in normalized if node_id in by_id]

    def adjacent_context_edges(
        self,
        node_ids: Sequence[str],
        *,
        relations: Sequence[str] = (),
        limit: int = 1_000,
    ) -> list[dict[str, Any]]:
        """Return a deterministic, bounded edge frontier around canonical ids."""
        normalized_ids = list(
            dict.fromkeys(node_id.strip() for node_id in node_ids if node_id.strip())
        )
        normalized_relations = list(
            dict.fromkeys(relation.strip() for relation in relations if relation.strip())
        )
        if not normalized_ids:
            return []
        if len(normalized_ids) > 400:
            raise ValueError("cannot expand more than 400 context nodes at once")
        if len(normalized_relations) > 64:
            raise ValueError("cannot filter more than 64 relations at once")
        parsed_limit = int(limit)
        if parsed_limit < 1 or parsed_limit > 10_000:
            raise ValueError("edge limit must be between 1 and 10000")
        node_placeholders = ",".join("?" for _ in normalized_ids)
        relation_clause = ""
        parameters: list[Any] = [*normalized_ids, *normalized_ids]
        if normalized_relations:
            relation_placeholders = ",".join("?" for _ in normalized_relations)
            relation_clause = f" AND edge.relation IN ({relation_placeholders})"
            parameters.extend(normalized_relations)
        parameters.append(parsed_limit + 1)
        query = f"""
            SELECT edge.id, edge.source_id, edge.target_id, edge.relation,
                   edge.origin, edge.confidence, edge.weight,
                   edge.properties_json,
                   source.namespace AS source_namespace,
                   source.project AS source_project,
                   source.stable_key AS source_key,
                   source.node_type AS source_type,
                   source.label AS source_label,
                   source.source AS source_source,
                   source.origin AS source_origin,
                   target.namespace AS target_namespace,
                   target.project AS target_project,
                   target.stable_key AS target_key,
                   target.node_type AS target_type,
                   target.label AS target_label,
                   target.source AS target_source,
                   target.origin AS target_origin
            FROM context_edges edge
            JOIN context_nodes source ON source.id = edge.source_id
            JOIN context_nodes target ON target.id = edge.target_id
            WHERE (edge.source_id IN ({node_placeholders})
                OR edge.target_id IN ({node_placeholders}))
                {relation_clause}
            ORDER BY edge.relation, source.stable_key, target.stable_key, edge.id
            LIMIT ?
        """  # nosec B608
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        edges = [_context_edge(row) for row in rows[:parsed_limit]]
        if len(rows) > parsed_limit and edges:
            edges[-1] = {**edges[-1], "frontierTruncated": True}
        return edges

    def graph_snapshot(self, *, project: str) -> dict[str, Any] | None:
        normalized_project = project.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM graph_snapshots WHERE project = ?",
                (normalized_project,),
            ).fetchone()
        if row is None:
            return None
        return {
            "project": row["project"],
            "sourcePath": row["source_path"],
            "contentHash": row["content_hash"],
            "builtAtCommit": row["built_at_commit"],
            "nodeCount": row["node_count"],
            "edgeCount": row["edge_count"],
            "importedAt": row["imported_at"],
        }

    def code_context(self, node_id: str, *, edge_limit: int = 12) -> dict[str, Any] | None:
        """Build a bounded context packet around one structural node."""
        with self.connect() as connection:
            node_row = connection.execute(
                """
                SELECT stable_key, label, source, node_type, properties_json
                FROM context_nodes WHERE id = ? AND namespace = 'code'
                """,
                (node_id,),
            ).fetchone()
            if node_row is None:
                return None
            edge_rows = connection.execute(
                """
                SELECT e.relation, e.confidence, e.weight, e.source_id, e.target_id,
                       source.stable_key AS source_key, source.label AS source_label,
                       target.stable_key AS target_key, target.label AS target_label
                FROM context_edges e
                JOIN context_nodes source ON source.id = e.source_id
                JOIN context_nodes target ON target.id = e.target_id
                WHERE e.origin = 'structural'
                  AND (e.source_id = ? OR e.target_id = ?)
                ORDER BY e.relation, source.stable_key, target.stable_key, e.id
                LIMIT ?
                """,
                (node_id, node_id, int(edge_limit) + 1),
            ).fetchall()
        node = json.loads(node_row["properties_json"])
        node["id"] = node_row["stable_key"]
        relationships = [
            {
                "direction": "out" if row["source_id"] == node_id else "in",
                "relation": row["relation"],
                "source": {"id": row["source_key"], "label": row["source_label"]},
                "target": {"id": row["target_key"], "label": row["target_label"]},
                "confidence": row["confidence"],
                "weight": row["weight"],
            }
            for row in edge_rows[:edge_limit]
        ]
        return {
            "node": node,
            "relationships": relationships,
            "truncated": max(0, len(edge_rows) - edge_limit),
        }

    def record_node_delivery(
        self,
        session_id: str,
        node_id: str,
        delivery_key: str,
        delivered_value: str,
        *,
        project: str,
        delivered_at: int | None = None,
    ) -> str:
        """Record the latest rendered delivery for any canonical context node."""
        normalized_session = session_id.strip()
        normalized_key = delivery_key.strip()
        if not normalized_session:
            raise ValueError("session_id cannot be empty")
        if not normalized_key:
            raise ValueError("delivery_key cannot be empty")
        timestamp = _now(delivered_at)
        digest = value_hash(delivered_value)
        with self.connect() as connection:
            node = connection.execute(
                "SELECT id FROM context_nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if node is None:
                raise KeyError(f"context node not found: {node_id}")
            connection.execute(
                """
                INSERT INTO deliveries(session_id, project, key, value_hash, delivered_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, key) DO UPDATE SET
                    project = excluded.project,
                    value_hash = excluded.value_hash,
                    delivered_at = excluded.delivered_at
                """,
                (normalized_session, project, normalized_key, digest, timestamp),
            )
            session_node_id = self._upsert_activity_node(
                connection,
                namespace="session",
                project=project,
                stable_key=normalized_session,
                node_type="session",
                label=normalized_session,
                origin="experiential",
                timestamp=timestamp,
            )
            self._upsert_edge(
                connection,
                source_id=session_node_id,
                target_id=node_id,
                relation="received",
                origin="experiential",
                properties={"valueHash": digest, "deliveredAt": timestamp},
                timestamp=timestamp,
            )
            self._record_event(
                connection,
                "context.delivered",
                subject_id=session_node_id,
                object_id=node_id,
                session_id=normalized_session,
                project=project,
                payload={
                    "key": normalized_key,
                    "valueHash": digest,
                    "rendered": delivered_value,
                },
                occurred_at=timestamp,
            )
            connection.commit()
        return digest

    @staticmethod
    def _record_event(
        connection: sqlite3.Connection,
        event_type: str,
        *,
        subject_id: str | None = None,
        object_id: str | None = None,
        session_id: str | None = None,
        project: str | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: int | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO context_events(
                event_type, subject_id, object_id, session_id, project,
                payload_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                subject_id,
                object_id,
                session_id,
                project,
                stable_json(payload or {}),
                _now(occurred_at),
            ),
        )

    @staticmethod
    def _upsert_edge(
        connection: sqlite3.Connection,
        *,
        source_id: str,
        target_id: str,
        relation: str,
        origin: str,
        properties: dict[str, Any] | None = None,
        timestamp: int,
    ) -> None:
        identity = "\0".join((source_id, target_id, relation, origin))
        edge_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO context_edges(
                id, source_id, target_id, relation, origin, properties_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                properties_json = excluded.properties_json,
                updated_at = excluded.updated_at
            """,
            (
                edge_id,
                source_id,
                target_id,
                relation,
                origin,
                stable_json(properties or {}),
                timestamp,
                timestamp,
            ),
        )

    @staticmethod
    def _upsert_activity_node(
        connection: sqlite3.Connection,
        *,
        namespace: str,
        project: str,
        stable_key: str,
        node_type: str,
        label: str,
        origin: str,
        timestamp: int,
        properties: dict[str, Any] | None = None,
    ) -> str:
        node_id = _stable_id(namespace, project, stable_key)
        connection.execute(
            """
            INSERT INTO context_nodes(
                id, namespace, project, stable_key, node_type, label, origin,
                properties_json, set_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace, project, stable_key) DO UPDATE SET
                node_type = excluded.node_type,
                label = excluded.label,
                origin = excluded.origin,
                properties_json = excluded.properties_json,
                set_at = excluded.set_at,
                updated_at = excluded.updated_at
            """,
            (
                node_id,
                namespace,
                project,
                stable_key,
                node_type,
                label,
                origin,
                stable_json(properties or {}),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return node_id

    def _link_seed(
        self,
        connection: sqlite3.Connection,
        topic_key: str,
        seed_node_id: str,
        seed_graph: str | None,
        timestamp: int,
    ) -> None:
        topic_id = _stable_id(MEMORY_NAMESPACE, "", topic_key)
        target = connection.execute(
            """
            SELECT id FROM context_nodes
            WHERE namespace = 'code' AND stable_key = ?
              AND (? IS NULL OR source_graph = ?)
            ORDER BY updated_at DESC LIMIT 1
            """,
            (seed_node_id, seed_graph, seed_graph),
        ).fetchone()
        if target is not None:
            self._upsert_edge(
                connection,
                source_id=topic_id,
                target_id=target["id"],
                relation="represents",
                origin="graph-seed",
                timestamp=timestamp,
            )

    def _restore_seed_links(
        self,
        connection: sqlite3.Connection,
        project: str,
        timestamp: int,
    ) -> None:
        rows = connection.execute(
            """
            SELECT stable_key, external_id, source_graph FROM context_nodes
            WHERE namespace = ? AND project = '' AND origin = 'graph-seed'
              AND external_id IS NOT NULL
            ORDER BY stable_key
            """,
            (MEMORY_NAMESPACE,),
        ).fetchall()
        for row in rows:
            target = connection.execute(
                """
                SELECT id FROM context_nodes
                WHERE namespace = 'code' AND project = ? AND stable_key = ?
                """,
                (project, row["external_id"]),
            ).fetchone()
            if target is not None:
                self._upsert_edge(
                    connection,
                    source_id=_stable_id(MEMORY_NAMESPACE, "", row["stable_key"]),
                    target_id=target["id"],
                    relation="represents",
                    origin="graph-seed",
                    timestamp=timestamp,
                )

    def record_delivery(
        self,
        session_id: str,
        key: str,
        delivered_value: str,
        *,
        project: str | None = None,
        delivered_at: int | None = None,
    ) -> str:
        if not session_id.strip():
            raise ValueError("session_id cannot be empty")
        key = validate_topic_key(key)
        digest = value_hash(delivered_value)
        timestamp = _now(delivered_at)
        normalized_session = session_id.strip()
        normalized_project = project or ""
        with self.connect() as connection:
            topic = connection.execute(
                """
                SELECT id FROM context_nodes
                WHERE namespace = ? AND project = '' AND stable_key = ?
                """,
                (MEMORY_NAMESPACE, key),
            ).fetchone()
            if topic is None:
                raise KeyError(f"topic not found: {key}")
            connection.execute(
                """
                INSERT INTO deliveries(session_id, project, key, value_hash, delivered_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, key) DO UPDATE SET
                    project = excluded.project,
                    value_hash = excluded.value_hash,
                    delivered_at = excluded.delivered_at
                """,
                (normalized_session, project, key, digest, timestamp),
            )
            session_node_id = self._upsert_activity_node(
                connection,
                namespace="session",
                project=normalized_project,
                stable_key=normalized_session,
                node_type="session",
                label=normalized_session,
                origin="experiential",
                timestamp=timestamp,
            )
            self._upsert_edge(
                connection,
                source_id=session_node_id,
                target_id=topic["id"],
                relation="received",
                origin="experiential",
                properties={"valueHash": digest, "deliveredAt": timestamp},
                timestamp=timestamp,
            )
            self._record_event(
                connection,
                "context.delivered",
                subject_id=session_node_id,
                object_id=topic["id"],
                session_id=normalized_session,
                project=project,
                payload={"key": key, "valueHash": digest, "rendered": delivered_value},
                occurred_at=timestamp,
            )
            connection.commit()
        return digest

    def session_view(
        self,
        *,
        session_id: str | None = None,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT session_id, project, key, value_hash, delivered_at FROM deliveries"
        params: tuple[Any, ...]
        if session_id and since is not None:
            query += " WHERE session_id = ? AND delivered_at >= ?"
            params = (session_id, int(since))
        elif session_id:
            query += " WHERE session_id = ?"
            params = (session_id,)
        elif since is not None:
            query += " WHERE delivered_at >= ?"
            params = (int(since),)
        else:
            params = ()
        query += " ORDER BY delivered_at DESC, session_id ASC, key ASC"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()

        sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = sessions.setdefault(
                row["session_id"],
                {"id": row["session_id"], "project": row["project"], "items": []},
            )
            if entry["project"] is None and row["project"] is not None:
                entry["project"] = row["project"]
            entry["items"].append(
                {
                    "key": row["key"],
                    "valueHash": row["value_hash"],
                    "deliveredAt": row["delivered_at"],
                }
            )
        return sorted(
            sessions.values(),
            key=lambda item: (
                -max((entry["deliveredAt"] for entry in item["items"]), default=0),
                item["id"],
            ),
        )

    def session_topic_keys(self, session_id: str) -> list[str]:
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id cannot be empty")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT key FROM deliveries WHERE session_id = ? ORDER BY key ASC",
                (normalized,),
            ).fetchall()
        return [str(row["key"]) for row in rows]

    def session_delivery_hashes(self, session_id: str) -> dict[str, str]:
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id cannot be empty")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT key, value_hash FROM deliveries WHERE session_id = ? ORDER BY key ASC",
                (normalized,),
            ).fetchall()
        return {str(row["key"]): str(row["value_hash"]) for row in rows}

    def create_request(
        self,
        session_id: str,
        need: str,
        *,
        project: str | None = None,
        created_at: int | None = None,
    ) -> int:
        if not session_id.strip():
            raise ValueError("session_id cannot be empty")
        need = need.strip()
        if not need:
            raise ValueError("need cannot be empty")
        timestamp = _now(created_at)
        normalized_session = session_id.strip()
        normalized_project = project or ""
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO requests(session_id, project, need, created_at) VALUES (?, ?, ?, ?)",
                (normalized_session, project, need, timestamp),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a request id")
            request_id = int(cursor.lastrowid)
            session_node_id = self._upsert_activity_node(
                connection,
                namespace="session",
                project=normalized_project,
                stable_key=normalized_session,
                node_type="session",
                label=normalized_session,
                origin="experiential",
                timestamp=timestamp,
            )
            request_node_id = self._upsert_activity_node(
                connection,
                namespace="request",
                project=normalized_project,
                stable_key=str(request_id),
                node_type="knowledge.request",
                label=need,
                origin="experiential",
                timestamp=timestamp,
                properties={"status": "open", "need": need},
            )
            self._upsert_edge(
                connection,
                source_id=session_node_id,
                target_id=request_node_id,
                relation="requested",
                origin="experiential",
                timestamp=timestamp,
            )
            self._record_event(
                connection,
                "context.requested",
                subject_id=session_node_id,
                object_id=request_node_id,
                session_id=normalized_session,
                project=project,
                payload={"requestId": request_id, "need": need},
                occurred_at=timestamp,
            )
            connection.commit()
            return request_id

    def ensure_request(
        self,
        session_id: str,
        need: str,
        *,
        project: str | None = None,
        created_at: int | None = None,
    ) -> int:
        normalized_session = session_id.strip()
        normalized_need = need.strip()
        if not normalized_session:
            raise ValueError("session_id cannot be empty")
        if not normalized_need:
            raise ValueError("need cannot be empty")
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT id FROM requests
                WHERE session_id = ?
                  AND status = 'open'
                  AND need = ?
                  AND ((project IS NULL AND ? IS NULL) OR project = ?)
                ORDER BY id ASC LIMIT 1
                """,
                (normalized_session, normalized_need, project, project),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
        return self.create_request(
            normalized_session,
            normalized_need,
            project=project,
            created_at=created_at,
        )

    def list_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in REQUEST_STATUSES:
            raise ValueError(f"unsupported request status: {status}")
        query = "SELECT * FROM requests"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC, id DESC"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "id": row["id"],
                "sessionId": row["session_id"],
                "project": row["project"],
                "need": row["need"],
                "status": row["status"],
                "resolvedKey": row["resolved_key"],
                "createdAt": row["created_at"],
                "resolvedAt": row["resolved_at"],
            }
            for row in rows
        ]

    def resolve_request(self, request_id: int, key: str, *, resolved_at: int | None = None) -> bool:
        key = validate_topic_key(key)
        timestamp = _now(resolved_at)
        with self.connect() as connection:
            topic = connection.execute(
                """
                SELECT id FROM context_nodes
                WHERE namespace = ? AND project = '' AND stable_key = ?
                """,
                (MEMORY_NAMESPACE, key),
            ).fetchone()
            if topic is None:
                raise KeyError(f"topic not found: {key}")
            request = connection.execute(
                "SELECT * FROM requests WHERE id = ? AND status = 'open'",
                (int(request_id),),
            ).fetchone()
            if request is None:
                return False
            cursor = connection.execute(
                """
                UPDATE requests
                SET status = 'resolved', resolved_key = ?, resolved_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (key, timestamp, int(request_id)),
            )
            request_node_id = self._upsert_activity_node(
                connection,
                namespace="request",
                project=request["project"] or "",
                stable_key=str(request_id),
                node_type="knowledge.request",
                label=request["need"],
                origin="experiential",
                timestamp=timestamp,
                properties={"status": "resolved", "need": request["need"], "resolvedKey": key},
            )
            self._upsert_edge(
                connection,
                source_id=request_node_id,
                target_id=topic["id"],
                relation="resolved-as",
                origin="human",
                timestamp=timestamp,
            )
            self._record_event(
                connection,
                "context.request-resolved",
                subject_id=request_node_id,
                object_id=topic["id"],
                session_id=request["session_id"],
                project=request["project"],
                payload={"requestId": int(request_id), "resolvedKey": key},
                occurred_at=timestamp,
            )
            connection.commit()
            return cursor.rowcount > 0

    def record_touch(
        self,
        session_id: str,
        directory: str | Path,
        *,
        project: str | None = None,
        touched_at: int | None = None,
    ) -> bool:
        normalized = str(Path(directory).expanduser().resolve())
        normalized_session = session_id.strip()
        if not normalized_session:
            raise ValueError("session_id cannot be empty")
        timestamp = _now(touched_at)
        normalized_project = project or ""
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO touches(session_id, project, dir, touched_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_session, project, normalized, timestamp),
            )
            if cursor.rowcount > 0:
                session_node_id = self._upsert_activity_node(
                    connection,
                    namespace="session",
                    project=normalized_project,
                    stable_key=normalized_session,
                    node_type="session",
                    label=normalized_session,
                    origin="experiential",
                    timestamp=timestamp,
                )
                path_node_id = self._upsert_activity_node(
                    connection,
                    namespace="path",
                    project=normalized_project,
                    stable_key=normalized,
                    node_type="resource.path",
                    label=normalized,
                    origin="observed",
                    timestamp=timestamp,
                )
                self._upsert_edge(
                    connection,
                    source_id=session_node_id,
                    target_id=path_node_id,
                    relation="touched",
                    origin="experiential",
                    properties={"touchedAt": timestamp},
                    timestamp=timestamp,
                )
                self._record_event(
                    connection,
                    "path.touched",
                    subject_id=session_node_id,
                    object_id=path_node_id,
                    session_id=normalized_session,
                    project=project,
                    payload={"path": normalized},
                    occurred_at=timestamp,
                )
            connection.commit()
            return cursor.rowcount > 0

    def prune_orphaned_seeds(
        self,
        *,
        seed_graph: str,
        candidate_keys: set[str],
        live_node_ids: set[str],
    ) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT key, seed_node_id FROM topics
                WHERE origin = 'graph-seed' AND seed_graph = ?
                ORDER BY key ASC
                """,
                (seed_graph,),
            ).fetchall()
            removable = [
                row["key"]
                for row in rows
                if row["key"] not in candidate_keys
                and row["seed_node_id"] is not None
                and row["seed_node_id"] not in live_node_ids
            ]
            if removable:
                connection.executemany(
                    """
                    DELETE FROM context_nodes
                    WHERE namespace = ? AND project = ''
                      AND origin = 'graph-seed' AND stable_key = ?
                    """,
                    [(MEMORY_NAMESPACE, key) for key in removable],
                )
                connection.commit()
            return removable

    def record_gate_decision(
        self,
        *,
        session_id: str,
        project: str | None,
        input_hash: str,
        input_text: str | None,
        proposal: dict[str, Any],
        final_action: str,
        delivery: list[dict[str, Any]],
        request_id: int | None,
        model_id: str | None,
        model_revision: str | None,
        prompt_version: str,
        latency_ms: int | None,
        fallback_reason: str | None,
        created_at: int | None = None,
    ) -> int:
        normalized_session = session_id.strip()
        if not normalized_session:
            raise ValueError("session_id cannot be empty")
        if final_action not in GATE_FINAL_ACTIONS:
            raise ValueError(f"unsupported final gate action: {final_action}")
        if not re.fullmatch(r"[0-9a-f]{64}", input_hash):
            raise ValueError("input_hash must be a lowercase SHA-256 digest")
        if latency_ms is not None and latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO gate_decisions(
                    session_id, project, input_hash, input_text, proposal_json,
                    final_action, delivery_json, request_id, model_id,
                    model_revision, prompt_version, latency_ms, fallback_reason,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_session,
                    project,
                    input_hash,
                    input_text,
                    stable_json(proposal),
                    final_action,
                    stable_json(delivery),
                    request_id,
                    model_id,
                    model_revision,
                    prompt_version,
                    latency_ms,
                    fallback_reason,
                    _now(created_at),
                ),
            )
            connection.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a gate decision id")
            return int(cursor.lastrowid)

    def list_gate_decisions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or limit < 1 or limit > 1_000:
            raise ValueError("gate decision limit must be between 1 and 1000")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*, f.verdict, f.expected_action, f.expected_keys_json,
                       f.note, f.created_at AS feedback_at
                FROM gate_decisions d
                LEFT JOIN gate_feedback f ON f.decision_id = d.id
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._gate_decision_row(row) for row in rows]

    def record_gate_feedback(
        self,
        decision_id: int,
        *,
        verdict: str,
        expected_action: str | None = None,
        expected_keys: Sequence[str] = (),
        note: str | None = None,
        created_at: int | None = None,
    ) -> dict[str, Any]:
        normalized_verdict = verdict.strip().lower()
        if normalized_verdict not in GATE_FEEDBACK_VERDICTS:
            raise ValueError(f"unsupported gate feedback verdict: {verdict}")
        normalized_action = expected_action.strip().lower() if expected_action else None
        if normalized_action is not None and normalized_action not in GATE_FINAL_ACTIONS:
            raise ValueError(f"unsupported expected gate action: {expected_action}")
        normalized_keys = sorted({validate_topic_key(key) for key in expected_keys})
        normalized_note = note.strip() if note else None
        if normalized_note is not None and len(normalized_note) > 4_096:
            raise ValueError("gate feedback note exceeds 4096 characters")
        if normalized_verdict == "incorrect" and normalized_action is None:
            raise ValueError("incorrect feedback requires expected_action")
        with self.connect() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM gate_decisions WHERE id = ?", (int(decision_id),)
                ).fetchone()
                is None
            ):
                raise KeyError(f"gate decision not found: {decision_id}")
            connection.execute(
                """
                INSERT INTO gate_feedback(
                    decision_id, verdict, expected_action, expected_keys_json,
                    note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    verdict = excluded.verdict,
                    expected_action = excluded.expected_action,
                    expected_keys_json = excluded.expected_keys_json,
                    note = excluded.note,
                    created_at = excluded.created_at
                """,
                (
                    int(decision_id),
                    normalized_verdict,
                    normalized_action,
                    stable_json(normalized_keys),
                    normalized_note,
                    _now(created_at),
                ),
            )
            connection.commit()
        return {
            "decisionId": int(decision_id),
            "verdict": normalized_verdict,
            "expectedAction": normalized_action,
            "expectedKeys": normalized_keys,
            "note": normalized_note,
        }

    @staticmethod
    def _gate_decision_row(row: sqlite3.Row) -> dict[str, Any]:
        feedback = None
        if row["verdict"] is not None:
            feedback = {
                "verdict": row["verdict"],
                "expectedAction": row["expected_action"],
                "expectedKeys": json.loads(row["expected_keys_json"]),
                "note": row["note"],
                "createdAt": row["feedback_at"],
            }
        return {
            "id": row["id"],
            "sessionId": row["session_id"],
            "project": row["project"],
            "inputHash": row["input_hash"],
            "inputText": row["input_text"],
            "proposal": json.loads(row["proposal_json"]),
            "action": row["final_action"],
            "delivery": json.loads(row["delivery_json"]),
            "requestId": row["request_id"],
            "model": {
                "id": row["model_id"],
                "revision": row["model_revision"],
            },
            "promptVersion": row["prompt_version"],
            "latencyMs": row["latency_ms"],
            "fallback": row["fallback_reason"],
            "createdAt": row["created_at"],
            "feedback": feedback,
        }

    def diagnostics(self) -> dict[str, Any]:
        with self.connect() as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            counts = {
                "nodes": int(
                    connection.execute("SELECT COUNT(*) FROM context_nodes").fetchone()[0]
                ),
                "edges": int(
                    connection.execute("SELECT COUNT(*) FROM context_edges").fetchone()[0]
                ),
                "events": int(
                    connection.execute("SELECT COUNT(*) FROM context_events").fetchone()[0]
                ),
                "graphSnapshots": int(
                    connection.execute("SELECT COUNT(*) FROM graph_snapshots").fetchone()[0]
                ),
                "topics": int(connection.execute("SELECT COUNT(*) FROM topics").fetchone()[0]),
                "deliveries": int(
                    connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
                ),
                "requests": int(connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]),
                "touches": int(connection.execute("SELECT COUNT(*) FROM touches").fetchone()[0]),
                "contextDecisions": int(
                    connection.execute("SELECT COUNT(*) FROM gate_decisions").fetchone()[0]
                ),
                "contextFeedback": int(
                    connection.execute("SELECT COUNT(*) FROM gate_feedback").fetchone()[0]
                ),
            }
        return {
            "database": str(self.path),
            "schemaVersion": SCHEMA_VERSION,
            "integrity": integrity,
            "counts": counts,
        }


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _context_node(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "namespace": row["namespace"],
        "project": row["project"],
        "stableKey": row["stable_key"],
        "type": row["node_type"],
        "label": row["label"],
        "value": row["value"],
        "source": row["source"],
        "origin": row["origin"],
        "properties": _json_object(row["properties_json"]),
        "externalId": row["external_id"],
        "sourceGraph": row["source_graph"],
        "setAt": row["set_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _prefer_project_memory(
    nodes: Sequence[dict[str, Any]], project: str
) -> list[dict[str, Any]]:
    """Collapse duplicate memory keys, preferring the project-local value."""
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for node in nodes:
        identity = (str(node["namespace"]), str(node["stableKey"]))
        existing = selected.get(identity)
        if existing is None or (
            node["namespace"] == MEMORY_NAMESPACE and node["project"] == project
        ):
            selected[identity] = node
    return [selected[identity] for identity in sorted(selected)]


def _context_edge(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sourceId": row["source_id"],
        "targetId": row["target_id"],
        "relation": row["relation"],
        "origin": row["origin"],
        "confidence": row["confidence"],
        "weight": row["weight"],
        "properties": _json_object(row["properties_json"]),
        "source": {
            "id": row["source_id"],
            "namespace": row["source_namespace"],
            "project": row["source_project"],
            "stableKey": row["source_key"],
            "type": row["source_type"],
            "label": row["source_label"],
            "source": row["source_source"],
            "origin": row["source_origin"],
        },
        "target": {
            "id": row["target_id"],
            "namespace": row["target_namespace"],
            "project": row["target_project"],
            "stableKey": row["target_key"],
            "type": row["target_type"],
            "label": row["target_label"],
            "source": row["target_source"],
            "origin": row["target_origin"],
        },
    }


def _optional_float(value: object) -> float | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _path_in_scope(source_file: str, scope: str) -> bool:
    source_parts = tuple(
        part for part in source_file.replace("\\", "/").strip("/").split("/") if part
    )
    scope_parts = tuple(part for part in scope.replace("\\", "/").strip("/").split("/") if part)
    return bool(scope_parts) and source_parts[: len(scope_parts)] == scope_parts


def _paths_related(left: str, right: str) -> bool:
    normalized_left = left.strip("/")
    normalized_right = right.strip("/")
    return bool(normalized_left and normalized_right) and (
        normalized_left == normalized_right
        or normalized_left.startswith(normalized_right + "/")
        or normalized_right.startswith(normalized_left + "/")
    )
