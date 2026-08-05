"""Canonical SQLite repository for Purpory's unified context graph.

Code structure, human knowledge, and session activity share one node/edge model.
Indexed operational projections support bounded reads while graph files remain
optional import/export artifacts. The module intentionally depends only on the
Python standard library.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from purpory.supervise.freshness import DEFAULT_STALE_DAYS, is_stale
from purpory.supervise.identity import resolve_project_id

DEFAULT_DB_PATH = Path.home() / ".purpory" / "context.db"
SCHEMA_VERSION = 6
MEMORY_NAMESPACE = "memory"
EMBEDDING_PRIORITIES = {"expanded": 60, "remembered": 80, "delivered": 100}
RESOURCE_NAMESPACE = "resource"
CONTEXT_NAMESPACE = "context"
TOPIC_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)*$")
RESOURCE_FIELD_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TOPIC_KINDS = frozenset({"note", "code-area", "doc-ref", "decision", "seeded"})
TOPIC_ORIGINS = frozenset({"human", "graph-seed"})
REQUEST_STATUSES = frozenset({"open", "resolved"})
NEEDS_REVIEW_STATUSES = frozenset({"open", "resolved"})
NEEDS_REVIEW_OUTCOMES = frozenset({"keep", "change"})
GLOBAL_MEMORY_REQUEST_STATUSES = frozenset({"pending", "approved", "rejected"})
GATE_FINAL_ACTIONS = frozenset({"skip", "retrieve", "ask"})
GATE_FEEDBACK_VERDICTS = frozenset({"correct", "incorrect"})
MEMORY_CATEGORY_BY_KIND = {
    "decision": "intent",
    "note": "knowledge",
    "doc-ref": "reference",
}


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


def memory_category(kind: str) -> str | None:
    return MEMORY_CATEGORY_BY_KIND.get(kind)


def _registry_text(value: str, *, field: str, maximum: int, required: bool = True) -> str:
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{field} cannot be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return normalized


def _registry_kind(value: str, *, field: str) -> str:
    normalized = value.strip().lower()
    if not RESOURCE_FIELD_RE.fullmatch(normalized):
        raise ValueError(f"{field} must use lowercase letters, numbers, or dashes")
    return normalized


def _registry_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _context_projects(primary: str, additional: Sequence[str]) -> tuple[str, ...]:
    projects = tuple(
        dict.fromkeys(
            item.strip()
            for item in (primary, *additional)
            if isinstance(item, str) and item.strip()
        )
    )
    if not projects:
        raise ValueError("at least one context project is required")
    return projects


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
                    hyperedge_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
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

                CREATE TABLE IF NOT EXISTS memory_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    key TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    value TEXT,
                    source TEXT,
                    origin TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(project, key, version_number)
                );

                CREATE TABLE IF NOT EXISTS needs_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    outcome TEXT,
                    result_version_id INTEGER,
                    created_at INTEGER NOT NULL,
                    resolved_at INTEGER,
                    CHECK (status IN ('open', 'resolved')),
                    CHECK (outcome IS NULL OR outcome IN ('keep', 'change')),
                    UNIQUE(project, key, source_type, source_id, content_hash),
                    FOREIGN KEY(result_version_id) REFERENCES memory_versions(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS memory_usage (
                    node_id TEXT PRIMARY KEY,
                    selected_count INTEGER NOT NULL DEFAULT 0,
                    expanded_count INTEGER NOT NULL DEFAULT 0,
                    last_selected_at INTEGER,
                    last_expanded_at INTEGER,
                    FOREIGN KEY(node_id) REFERENCES context_nodes(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS global_memory_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    key TEXT NOT NULL,
                    initial_json TEXT NOT NULL,
                    proposed_json TEXT NOT NULL,
                    final_json TEXT,
                    rationale TEXT NOT NULL,
                    requested_from_project TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    edited_at INTEGER,
                    decided_at INTEGER,
                    CHECK (status IN ('pending', 'approved', 'rejected'))
                );

                CREATE TABLE IF NOT EXISTS embedding_targets (
                    node_id TEXT PRIMARY KEY,
                    priority INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    requested_at INTEGER NOT NULL,
                    usage_count INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS embedding_profiles (
                    profile_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector_format TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS node_embeddings (
                    profile_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    source_updated_at INTEGER NOT NULL,
                    vector BLOB,
                    embedded_at INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    PRIMARY KEY(profile_id, node_id)
                );

                """
            )
            graph_snapshot_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(graph_snapshots)").fetchall()
            }
            if "hyperedge_count" not in graph_snapshot_columns:
                connection.execute(
                    "ALTER TABLE graph_snapshots "
                    "ADD COLUMN hyperedge_count INTEGER NOT NULL DEFAULT 0"
                )
            if "metadata_json" not in graph_snapshot_columns:
                connection.execute(
                    "ALTER TABLE graph_snapshots "
                    "ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
                )
            self._migrate_registry_tables(connection)
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
                CREATE INDEX IF NOT EXISTS idx_memory_versions_lookup
                    ON memory_versions(project, key, version_number DESC);
                CREATE INDEX IF NOT EXISTS idx_needs_reviews_status
                    ON needs_reviews(project, status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_global_memory_requests_status
                    ON global_memory_requests(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_embedding_targets_priority
                    ON embedding_targets(priority DESC, usage_count DESC, requested_at);
                """
            )
            versioned = {
                (str(row["project"]), str(row["key"]))
                for row in connection.execute(
                    "SELECT project, key FROM memory_versions"
                ).fetchall()
            }
            memory_rows = connection.execute(
                """
                SELECT project, stable_key AS key, node_type AS kind, value,
                       source, origin, updated_at
                FROM context_nodes
                WHERE namespace = 'memory' AND origin = 'human'
                """
            ).fetchall()
            for row in memory_rows:
                identity = (str(row["project"]), str(row["key"]))
                if identity in versioned:
                    continue
                snapshot = dict(row)
                connection.execute(
                    """
                    INSERT INTO memory_versions(
                        project, key, version_number, kind, value, source,
                        origin, content_hash, created_at
                    ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot["project"],
                        snapshot["key"],
                        snapshot["kind"],
                        snapshot["value"],
                        snapshot["source"],
                        snapshot["origin"],
                        topic_hash(snapshot),
                        snapshot["updated_at"],
                    ),
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

    @staticmethod
    def _upsert_registry_node(
        connection: sqlite3.Connection,
        *,
        namespace: str,
        project: str,
        stable_key: str,
        node_type: str,
        label: str,
        value: str | None,
        source: str | None,
        origin: str,
        properties: dict[str, Any],
        timestamp: int,
    ) -> str:
        node_id = _stable_id(namespace, project, stable_key)
        connection.execute(
            """
            INSERT INTO context_nodes(
                id, namespace, project, stable_key, node_type, label,
                value, source, origin, properties_json, set_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace, project, stable_key) DO UPDATE SET
                node_type = excluded.node_type,
                label = excluded.label,
                value = excluded.value,
                source = excluded.source,
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
                value,
                source,
                origin,
                stable_json(properties),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return node_id

    @classmethod
    def _migrate_registry_tables(cls, connection: sqlite3.Connection) -> None:
        """Move the pre-v5 project registry into the canonical graph once."""
        legacy_tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "context_namespaces" not in legacy_tables:
            return
        projects = connection.execute(
            """
            SELECT id, name, description, parent_id, created_at, updated_at
            FROM context_namespaces
            WHERE namespace_kind = 'project'
            ORDER BY id
            """
        ).fetchall()
        project_ids: dict[str, str] = {}
        for project in projects:
            project_id = str(project["id"])
            timestamp = int(project["updated_at"])
            project_ids[project_id] = cls._upsert_registry_node(
                connection,
                namespace=CONTEXT_NAMESPACE,
                project=project_id,
                stable_key=project_id,
                node_type="project",
                label=str(project["name"]),
                value=str(project["description"]) or None,
                source=None,
                origin="human",
                properties={
                    "description": str(project["description"]),
                    "parentId": project["parent_id"],
                },
                timestamp=timestamp,
            )

        resources = connection.execute(
            """
            SELECT id, provider, resource_kind, external_identity, label,
                   properties_json, updated_at
            FROM context_resources
            ORDER BY id
            """
        ).fetchall()
        resource_ids: dict[str, str] = {}
        for resource in resources:
            resource_id = str(resource["id"])
            properties = _json_object(resource["properties_json"])
            aliases = [
                str(row["alias"])
                for row in connection.execute(
                    """
                    SELECT alias FROM context_namespace_resources
                    WHERE resource_id = ? AND alias IS NOT NULL
                    ORDER BY alias
                    """,
                    (resource_id,),
                ).fetchall()
            ]
            properties.update(
                {
                    "provider": resource["provider"],
                    "resourceKind": resource["resource_kind"],
                    "externalIdentity": resource["external_identity"],
                    "aliases": aliases,
                }
            )
            resource_ids[resource_id] = cls._upsert_registry_node(
                connection,
                namespace=RESOURCE_NAMESPACE,
                project="",
                stable_key=resource_id,
                node_type=f"resource.{resource['resource_kind']}",
                label=str(resource["label"]),
                value=stable_json(properties),
                source=str(resource["external_identity"]),
                origin="registered",
                properties=properties,
                timestamp=int(resource["updated_at"]),
            )

        bindings = connection.execute(
            """
            SELECT namespace_id, resource_id, alias, created_at
            FROM context_namespace_resources
            ORDER BY namespace_id, resource_id
            """
        ).fetchall()
        for binding in bindings:
            project_node_id = project_ids.get(str(binding["namespace_id"]))
            resource_node_id = resource_ids.get(str(binding["resource_id"]))
            if project_node_id is None or resource_node_id is None:
                continue
            cls._upsert_edge(
                connection,
                source_id=project_node_id,
                target_id=resource_node_id,
                relation="contains",
                origin="registered",
                properties={"alias": binding["alias"]},
                timestamp=int(binding["created_at"]),
            )

        views = (
            connection.execute(
                """
                SELECT id, resource_id, locator, revision, state_hash,
                       properties_json, observed_at
                FROM context_resource_views
                ORDER BY id
                """
            ).fetchall()
            if "context_resource_views" in legacy_tables
            else []
        )
        for view in views:
            resource_node_id = resource_ids.get(str(view["resource_id"]))
            if resource_node_id is None:
                continue
            properties = _json_object(view["properties_json"])
            properties.update(
                {
                    "resourceId": view["resource_id"],
                    "locator": view["locator"],
                    "revision": view["revision"],
                    "stateHash": view["state_hash"],
                }
            )
            label = str(properties.get("branch") or Path(str(view["locator"])).name)
            view_node_id = cls._upsert_registry_node(
                connection,
                namespace=RESOURCE_NAMESPACE,
                project="",
                stable_key=str(view["id"]),
                node_type="resource-view",
                label=label,
                value=stable_json(properties),
                source=str(view["locator"]),
                origin="observed",
                properties=properties,
                timestamp=int(view["observed_at"]),
            )
            cls._upsert_edge(
                connection,
                source_id=resource_node_id,
                target_id=view_node_id,
                relation="has-view",
                origin="observed",
                properties={"revision": view["revision"], "stateHash": view["state_hash"]},
                timestamp=int(view["observed_at"]),
            )
            code_nodes = connection.execute(
                """
                SELECT id FROM context_nodes
                WHERE namespace = 'code' AND project = ?
                ORDER BY stable_key
                """,
                (resolve_project_id(str(view["locator"])),),
            ).fetchall()
            for code_node in code_nodes:
                cls._upsert_edge(
                    connection,
                    source_id=view_node_id,
                    target_id=str(code_node["id"]),
                    relation="contains",
                    origin="derived",
                    properties={"graphProject": resolve_project_id(str(view["locator"]))},
                    timestamp=int(view["observed_at"]),
                )

        for project_id, project_node_id in project_ids.items():
            memory_nodes = connection.execute(
                """
                SELECT id FROM context_nodes
                WHERE namespace = ? AND project = ?
                ORDER BY stable_key
                """,
                (MEMORY_NAMESPACE, project_id),
            ).fetchall()
            for memory in memory_nodes:
                cls._upsert_edge(
                    connection,
                    source_id=project_node_id,
                    target_id=str(memory["id"]),
                    relation="contains",
                    origin="human",
                    timestamp=_now(),
                )
        for table in (
            "context_resource_views",
            "context_namespace_resources",
            "context_resources",
            "context_namespaces",
        ):
            if table in legacy_tables:
                connection.execute(f"DROP TABLE {table}")

    def create_project_namespace(
        self,
        name: str,
        *,
        description: str = "",
        created_at: int | None = None,
    ) -> dict[str, Any]:
        normalized_name = _registry_text(name, field="project name", maximum=120)
        normalized_description = _registry_text(
            description,
            field="project description",
            maximum=4_096,
            required=False,
        )
        timestamp = _now(created_at)
        namespace_id = _registry_id("project")
        with self.connect() as connection:
            duplicate = connection.execute(
                """
                SELECT 1 FROM context_nodes
                WHERE namespace = ? AND node_type = 'project' AND lower(label) = lower(?)
                """,
                (CONTEXT_NAMESPACE, normalized_name),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(f"project already exists: {normalized_name}")
            self._upsert_registry_node(
                connection,
                namespace=CONTEXT_NAMESPACE,
                project=namespace_id,
                stable_key=namespace_id,
                node_type="project",
                label=normalized_name,
                value=normalized_description or None,
                source=None,
                origin="human",
                properties={"description": normalized_description, "parentId": None},
                timestamp=timestamp,
            )
            self._record_event(
                connection,
                "project.created",
                project=namespace_id,
                payload={"name": normalized_name, "description": normalized_description},
                occurred_at=timestamp,
            )
            connection.commit()
        project = self.get_project_namespace(namespace_id)
        if project is None:
            raise RuntimeError("created project namespace could not be loaded")
        return project

    def ensure_project_namespace(self, namespace_id: str, *, name: str) -> dict[str, Any]:
        """Create the implicit project used when a repository is first observed."""
        normalized_id = _registry_text(namespace_id, field="project id", maximum=255)
        existing = self.get_project_namespace(normalized_id)
        if existing is not None:
            return existing
        normalized_name = _registry_text(name, field="project name", maximum=120)
        timestamp = _now()
        with self.connect() as connection:
            project_node_id = self._upsert_registry_node(
                connection,
                namespace=CONTEXT_NAMESPACE,
                project=normalized_id,
                stable_key=normalized_id,
                node_type="project",
                label=normalized_name,
                value=None,
                source=None,
                origin="observed",
                properties={"description": "", "parentId": None},
                timestamp=timestamp,
            )
            for memory in connection.execute(
                "SELECT id FROM context_nodes WHERE namespace = ? AND project = ?",
                (MEMORY_NAMESPACE, normalized_id),
            ).fetchall():
                self._upsert_edge(
                    connection,
                    source_id=project_node_id,
                    target_id=str(memory["id"]),
                    relation="contains",
                    origin="observed",
                    timestamp=timestamp,
                )
            self._record_event(
                connection,
                "project.discovered",
                project=normalized_id,
                payload={"name": normalized_name},
                occurred_at=timestamp,
            )
            connection.commit()
        project = self.get_project_namespace(normalized_id)
        if project is None:  # pragma: no cover - transaction invariant
            raise RuntimeError("discovered project namespace could not be loaded")
        return project

    def get_project_namespace(self, namespace_id: str) -> dict[str, Any] | None:
        normalized_id = _registry_text(
            namespace_id,
            field="project id",
            maximum=255,
        )
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM context_nodes
                WHERE namespace = ? AND stable_key = ? AND node_type = 'project'
                """,
                (CONTEXT_NAMESPACE, normalized_id),
            ).fetchone()
            if row is None:
                return None
            return self._project_namespace(connection, row)

    def list_project_namespaces(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM context_nodes
                WHERE namespace = ? AND node_type = 'project'
                ORDER BY lower(label), stable_key
                """
                , (CONTEXT_NAMESPACE,)
            ).fetchall()
            return [self._project_namespace(connection, row) for row in rows]

    @staticmethod
    def _project_namespace(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        resource_rows = connection.execute(
            """
            SELECT resource.*, binding.properties_json AS binding_properties
            FROM context_edges binding
            JOIN context_nodes resource ON resource.id = binding.target_id
            WHERE binding.source_id = ? AND binding.relation = 'contains'
              AND resource.namespace = ? AND resource.node_type LIKE 'resource.%'
            ORDER BY lower(resource.label), resource.stable_key
            """,
            (row["id"], RESOURCE_NAMESPACE),
        ).fetchall()
        resources: list[dict[str, Any]] = []
        for resource in resource_rows:
            view_rows = connection.execute(
                """
                SELECT view.* FROM context_edges relation
                JOIN context_nodes view ON view.id = relation.target_id
                WHERE relation.source_id = ? AND relation.relation = 'has-view'
                  AND view.namespace = ? AND view.node_type = 'resource-view'
                ORDER BY view.source
                """,
                (resource["id"], RESOURCE_NAMESPACE),
            ).fetchall()
            resource_properties = _json_object(resource["properties_json"])
            binding_properties = _json_object(resource["binding_properties"])
            resources.append(
                {
                    "id": resource["stable_key"],
                    "provider": resource_properties.get("provider"),
                    "kind": resource_properties.get("resourceKind"),
                    "externalIdentity": resource_properties.get("externalIdentity"),
                    "label": resource["label"],
                    "alias": binding_properties.get("alias"),
                    "properties": resource_properties,
                    "createdAt": resource["created_at"],
                    "updatedAt": resource["updated_at"],
                    "views": [
                        {
                            "id": view["stable_key"],
                            "locator": properties.get("locator", view["source"]),
                            "revision": properties.get("revision"),
                            "stateHash": properties.get("stateHash"),
                            "properties": properties,
                            "observedAt": view["set_at"],
                        }
                        for view in view_rows
                        for properties in [_json_object(view["properties_json"])]
                    ],
                }
            )
        properties = _json_object(row["properties_json"])
        return {
            "id": row["stable_key"],
            "kind": "project",
            "name": row["label"],
            "description": properties.get("description", row["value"] or ""),
            "parentId": properties.get("parentId"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "resources": resources,
        }

    def attach_resource(
        self,
        namespace_id: str,
        *,
        provider: str,
        resource_kind: str,
        external_identity: str,
        label: str,
        properties: dict[str, Any] | None = None,
        views: Sequence[dict[str, Any]] = (),
        alias: str | None = None,
        observed_at: int | None = None,
    ) -> dict[str, Any]:
        normalized_namespace = _registry_text(
            namespace_id,
            field="project id",
            maximum=255,
        )
        normalized_provider = _registry_kind(provider, field="resource provider")
        normalized_kind = _registry_kind(resource_kind, field="resource kind")
        normalized_identity = _registry_text(
            external_identity,
            field="resource identity",
            maximum=4_096,
        )
        normalized_label = _registry_text(label, field="resource label", maximum=255)
        normalized_alias = (
            _registry_text(alias, field="resource alias", maximum=255) if alias else None
        )
        if len(views) > 64:
            raise ValueError("resource cannot contain more than 64 views")
        timestamp = _now(observed_at)
        with self.connect() as connection:
            if (
                connection.execute(
                    """
                    SELECT 1 FROM context_nodes
                    WHERE namespace = ? AND stable_key = ? AND node_type = 'project'
                    """,
                    (CONTEXT_NAMESPACE, normalized_namespace),
                ).fetchone()
                is None
            ):
                raise KeyError(f"project namespace not found: {normalized_namespace}")
            existing = connection.execute(
                """
                SELECT stable_key FROM context_nodes
                WHERE namespace = ? AND source = ? AND node_type LIKE 'resource.%'
                  AND json_extract(properties_json, '$.provider') = ?
                LIMIT 1
                """,
                (RESOURCE_NAMESPACE, normalized_identity, normalized_provider),
            ).fetchone()
            resource_id = (
                str(existing["stable_key"]) if existing is not None else _registry_id("resource")
            )
            resource_properties = {
                **(properties or {}),
                "provider": normalized_provider,
                "resourceKind": normalized_kind,
                "externalIdentity": normalized_identity,
            }
            resource_node_id = self._upsert_registry_node(
                connection,
                namespace=RESOURCE_NAMESPACE,
                project="",
                stable_key=resource_id,
                node_type=f"resource.{normalized_kind}",
                label=normalized_label,
                value=stable_json(resource_properties),
                source=normalized_identity,
                origin="registered",
                properties=resource_properties,
                timestamp=timestamp,
            )
            project_node_id = _stable_id(
                CONTEXT_NAMESPACE, normalized_namespace, normalized_namespace
            )
            binding = connection.execute(
                """
                SELECT properties_json FROM context_edges
                WHERE source_id = ? AND target_id = ? AND relation = 'contains'
                """,
                (project_node_id, resource_node_id),
            ).fetchone()
            binding_alias = normalized_alias
            if binding_alias is None and binding is not None:
                binding_alias = _json_object(binding["properties_json"]).get("alias")
            self._upsert_edge(
                connection,
                source_id=project_node_id,
                target_id=resource_node_id,
                relation="contains",
                origin="registered",
                properties={"alias": binding_alias},
                timestamp=timestamp,
            )
            for raw_view in views:
                if not isinstance(raw_view, dict):
                    raise ValueError("resource views must be objects")
                locator = _registry_text(
                    str(raw_view.get("locator", "")),
                    field="resource view locator",
                    maximum=4_096,
                )
                revision_value = raw_view.get("revision")
                revision = (
                    _registry_text(
                        str(revision_value),
                        field="resource view revision",
                        maximum=512,
                    )
                    if revision_value is not None
                    else None
                )
                state_hash_value = raw_view.get("stateHash")
                state_hash = (
                    _registry_text(
                        str(state_hash_value),
                        field="resource view state hash",
                        maximum=512,
                    )
                    if state_hash_value is not None
                    else None
                )
                view_properties = raw_view.get("properties")
                if view_properties is not None and not isinstance(view_properties, dict):
                    raise ValueError("resource view properties must be an object")
                view = connection.execute(
                    """
                    SELECT view.stable_key FROM context_edges relation
                    JOIN context_nodes view ON view.id = relation.target_id
                    WHERE relation.source_id = ? AND relation.relation = 'has-view'
                      AND view.namespace = ? AND view.node_type = 'resource-view'
                      AND view.source = ?
                    """,
                    (resource_node_id, RESOURCE_NAMESPACE, locator),
                ).fetchone()
                view_id = (
                    str(view["stable_key"]) if view is not None else _registry_id("view")
                )
                canonical_view_properties = {
                    **(view_properties or {}),
                    "resourceId": resource_id,
                    "locator": locator,
                    "revision": revision,
                    "stateHash": state_hash,
                }
                view_node_id = self._upsert_registry_node(
                    connection,
                    namespace=RESOURCE_NAMESPACE,
                    project="",
                    stable_key=view_id,
                    node_type="resource-view",
                    label=str(canonical_view_properties.get("branch") or Path(locator).name),
                    value=stable_json(canonical_view_properties),
                    source=locator,
                    origin="observed",
                    properties=canonical_view_properties,
                    timestamp=timestamp,
                )
                self._upsert_edge(
                    connection,
                    source_id=resource_node_id,
                    target_id=view_node_id,
                    relation="has-view",
                    origin="observed",
                    properties={"revision": revision, "stateHash": state_hash},
                    timestamp=timestamp,
                )
            connection.execute(
                "UPDATE context_nodes SET updated_at = ? WHERE id = ?",
                (timestamp, project_node_id),
            )
            self._record_event(
                connection,
                "resource.attached",
                project=normalized_namespace,
                payload={
                    "resourceId": resource_id,
                    "provider": normalized_provider,
                    "kind": normalized_kind,
                },
                occurred_at=timestamp,
            )
            connection.commit()
        project = self.get_project_namespace(normalized_namespace)
        if project is None:
            raise RuntimeError("updated project namespace could not be loaded")
        return project

    def resource_by_identity(
        self,
        *,
        provider: str,
        external_identity: str,
    ) -> dict[str, Any] | None:
        normalized_provider = _registry_kind(provider, field="resource provider")
        normalized_identity = _registry_text(
            external_identity,
            field="resource identity",
            maximum=4_096,
        )
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT resource.stable_key AS resource_id,
                       project.stable_key AS namespace_id
                FROM context_nodes resource
                JOIN context_edges binding ON binding.target_id = resource.id
                JOIN context_nodes project ON project.id = binding.source_id
                WHERE resource.namespace = ? AND resource.source = ?
                  AND resource.node_type LIKE 'resource.%'
                  AND json_extract(resource.properties_json, '$.provider') = ?
                  AND binding.relation = 'contains' AND project.node_type = 'project'
                LIMIT 1
                """,
                (RESOURCE_NAMESPACE, normalized_identity, normalized_provider),
            ).fetchone()
        if row is None:
            return None
        return {"resourceId": row["resource_id"], "namespaceId": row["namespace_id"]}

    def resolve_resource_view(self, location: str | Path) -> dict[str, Any] | None:
        requested = Path(location).expanduser().resolve()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT project.stable_key AS namespace_id,
                       project.label AS namespace_name,
                       resource.stable_key AS resource_id,
                       resource.label AS resource_label,
                       resource.properties_json AS resource_properties,
                       view.stable_key AS view_id, view.source AS locator,
                       view.properties_json AS view_properties
                FROM context_nodes view
                JOIN context_edges view_edge ON view_edge.target_id = view.id
                    AND view_edge.relation = 'has-view'
                JOIN context_nodes resource ON resource.id = view_edge.source_id
                JOIN context_edges binding ON binding.target_id = resource.id
                    AND binding.relation = 'contains'
                JOIN context_nodes project ON project.id = binding.source_id
                WHERE view.namespace = ? AND view.node_type = 'resource-view'
                  AND project.node_type = 'project'
                ORDER BY length(view.source) DESC, view.source
                """
                , (RESOURCE_NAMESPACE,)
            ).fetchall()
        for row in rows:
            locator = Path(str(row["locator"])).expanduser()
            try:
                requested.relative_to(locator.resolve())
            except (OSError, ValueError):
                continue
            resource_properties = _json_object(row["resource_properties"])
            view_properties = _json_object(row["view_properties"])
            return {
                "namespaceId": row["namespace_id"],
                "namespaceName": row["namespace_name"],
                "resourceId": row["resource_id"],
                "provider": resource_properties.get("provider"),
                "resourceKind": resource_properties.get("resourceKind"),
                "resourceLabel": row["resource_label"],
                "externalIdentity": resource_properties.get("externalIdentity"),
                "viewId": row["view_id"],
                "locator": str(locator.resolve()),
                "revision": view_properties.get("revision"),
                "stateHash": view_properties.get("stateHash"),
                "properties": view_properties,
            }
        return None

    def project_resource_selection(
        self,
        namespace_id: str,
        *,
        active_view_id: str | None = None,
    ) -> dict[str, Any]:
        """Select one deterministic view per resource for an effective context."""
        project = self.get_project_namespace(namespace_id)
        if project is None:
            return {"projectNodeId": None, "resources": [], "viewIds": [], "nodeIds": []}
        selected_resources: list[dict[str, Any]] = []
        node_ids = [_stable_id(CONTEXT_NAMESPACE, namespace_id, namespace_id)]
        view_ids: list[str] = []
        for resource in project["resources"]:
            resource_node_id = _stable_id(RESOURCE_NAMESPACE, "", str(resource["id"]))
            node_ids.append(resource_node_id)
            views = list(resource["views"])
            selected_view = None
            if views:
                selected_view = min(
                    views,
                    key=lambda view: (
                        0 if view["id"] == active_view_id else 1,
                        0
                        if str(view.get("properties", {}).get("branch") or "")
                        in {"main", "master"}
                        else 1,
                        str(view["locator"]),
                    ),
                )
                view_ids.append(str(selected_view["id"]))
                node_ids.append(
                    _stable_id(RESOURCE_NAMESPACE, "", str(selected_view["id"]))
                )
            selected_resources.append(
                {
                    **resource,
                    "nodeId": resource_node_id,
                    "selectedView": (
                        {
                            **selected_view,
                            "nodeId": _stable_id(
                                RESOURCE_NAMESPACE,
                                "",
                                str(selected_view["id"]),
                            ),
                        }
                        if selected_view is not None
                        else None
                    ),
                }
            )
        return {
            "projectNodeId": node_ids[0],
            "resources": selected_resources,
            "viewIds": view_ids,
            "nodeIds": list(dict.fromkeys(node_ids)),
        }

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
            if origin == "human":
                self._upsert_embedding_target(
                    connection,
                    node_id=_stable_id(MEMORY_NAMESPACE, normalized_project, key),
                    priority=EMBEDDING_PRIORITIES["remembered"],
                    reason="remembered",
                    timestamp=timestamp,
                )
            if normalized_project:
                project_node = connection.execute(
                    """
                    SELECT id FROM context_nodes
                    WHERE namespace = ? AND stable_key = ? AND node_type = 'project'
                    """,
                    (CONTEXT_NAMESPACE, normalized_project),
                ).fetchone()
                if project_node is not None:
                    self._upsert_edge(
                        connection,
                        source_id=str(project_node["id"]),
                        target_id=_stable_id(MEMORY_NAMESPACE, normalized_project, key),
                        relation="contains",
                        origin="human",
                        timestamp=timestamp,
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
                SELECT id, stable_key AS key, value, source, node_type AS kind, origin,
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
                    self._upsert_embedding_target(
                        connection,
                        node_id=_stable_id(
                            MEMORY_NAMESPACE, normalized_project, change["key"]
                        ),
                        priority=EMBEDDING_PRIORITIES["remembered"],
                        reason="remembered",
                        timestamp=timestamp,
                    )
                    project_node = connection.execute(
                        """
                        SELECT id FROM context_nodes
                        WHERE namespace = ? AND stable_key = ? AND node_type = 'project'
                        """,
                        (CONTEXT_NAMESPACE, normalized_project),
                    ).fetchone()
                    if project_node is not None:
                        self._upsert_edge(
                            connection,
                            source_id=str(project_node["id"]),
                            target_id=_stable_id(
                                MEMORY_NAMESPACE, normalized_project, change["key"]
                            ),
                            relation="contains",
                            origin="human",
                            timestamp=timestamp,
                        )
                    version_id = self._record_memory_version(
                        connection,
                        project=normalized_project,
                        key=change["key"],
                        kind=change["kind"],
                        value=change["value"],
                        source=change["source"],
                        origin="human",
                        created_at=timestamp,
                    )
                    result["versionId"] = version_id
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
                            "versionId": version_id,
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

    @staticmethod
    def _record_memory_version(
        connection: sqlite3.Connection,
        *,
        project: str,
        key: str,
        kind: str,
        value: str | None,
        source: str | None,
        origin: str,
        created_at: int,
    ) -> int:
        snapshot = {"kind": kind, "value": value, "source": source}
        digest = topic_hash(snapshot)
        latest = connection.execute(
            """
            SELECT id, version_number, content_hash
            FROM memory_versions
            WHERE project = ? AND key = ?
            ORDER BY version_number DESC
            LIMIT 1
            """,
            (project, key),
        ).fetchone()
        if latest is not None and latest["content_hash"] == digest:
            return int(latest["id"])
        version_number = int(latest["version_number"]) + 1 if latest is not None else 1
        cursor = connection.execute(
            """
            INSERT INTO memory_versions(
                project, key, version_number, kind, value, source,
                origin, content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project,
                key,
                version_number,
                kind,
                value,
                source,
                origin,
                digest,
                created_at,
            ),
        )
        connection.execute(
            """
            DELETE FROM memory_versions
            WHERE project = ? AND key = ? AND id NOT IN (
                SELECT id FROM memory_versions
                WHERE project = ? AND key = ?
                ORDER BY version_number DESC
                LIMIT 3
            )
            """,
            (project, key, project, key),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("memory version insert did not return an id")
        return int(cursor.lastrowid)

    def list_memory_versions(self, key: str, *, project: str) -> list[dict[str, Any]]:
        normalized_key = validate_topic_key(key)
        normalized_project = project.strip()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, project, key, version_number, kind, value, source,
                       origin, content_hash, created_at
                FROM memory_versions
                WHERE project = ? AND key = ?
                ORDER BY version_number DESC
                LIMIT 3
                """,
                (normalized_project, normalized_key),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "project": row["project"],
                "key": row["key"],
                "version": int(row["version_number"]),
                "kind": row["kind"],
                "value": row["value"],
                "source": row["source"],
                "origin": row["origin"],
                "contentHash": row["content_hash"],
                "createdAt": int(row["created_at"]),
                "current": index == 0,
                "superseded": index > 0,
            }
            for index, row in enumerate(rows)
        ]

    def create_needs_review(
        self,
        key: str,
        *,
        project: str,
        source_type: str,
        source_id: str,
        content_hash: str,
        reason: str,
        created_at: int | None = None,
    ) -> dict[str, Any]:
        normalized_key = validate_topic_key(key)
        normalized_project = project.strip()
        normalized_source_type = source_type.strip()
        normalized_source_id = source_id.strip()
        normalized_hash = content_hash.strip().lower()
        normalized_reason = reason.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        if not normalized_source_type or len(normalized_source_type) > 64:
            raise ValueError("source_type must be between 1 and 64 characters")
        if not normalized_source_id or len(normalized_source_id) > 1024:
            raise ValueError("source_id must be between 1 and 1024 characters")
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_hash):
            raise ValueError("content_hash must be a SHA-256 hex digest")
        if not normalized_reason or len(normalized_reason) > 4096:
            raise ValueError("reason must be between 1 and 4096 characters")
        timestamp = _now(created_at)
        with self.connect() as connection:
            topic = connection.execute(
                """
                SELECT id FROM context_nodes
                WHERE namespace = ? AND stable_key = ? AND project IN ('', ?)
                ORDER BY CASE WHEN project = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (MEMORY_NAMESPACE, normalized_key, normalized_project, normalized_project),
            ).fetchone()
            if topic is None:
                raise KeyError(f"topic not found: {normalized_key}")
            existing = connection.execute(
                """
                SELECT * FROM needs_reviews
                WHERE project = ? AND key = ? AND source_type = ?
                  AND source_id = ? AND content_hash = ?
                """,
                (
                    normalized_project,
                    normalized_key,
                    normalized_source_type,
                    normalized_source_id,
                    normalized_hash,
                ),
            ).fetchone()
            if existing is not None:
                return self._needs_review_row(existing, created=False)
            cursor = connection.execute(
                """
                INSERT INTO needs_reviews(
                    project, key, source_type, source_id, content_hash,
                    reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_project,
                    normalized_key,
                    normalized_source_type,
                    normalized_source_id,
                    normalized_hash,
                    normalized_reason,
                    timestamp,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("needs-review insert did not return an id")
            review_id = int(cursor.lastrowid)
            self._record_event(
                connection,
                "memory.needs-review-created",
                object_id=topic["id"],
                project=normalized_project,
                payload={
                    "reviewId": review_id,
                    "key": normalized_key,
                    "sourceType": normalized_source_type,
                    "sourceId": normalized_source_id,
                    "contentHash": normalized_hash,
                    "reason": normalized_reason,
                },
                occurred_at=timestamp,
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM needs_reviews WHERE id = ?", (review_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("created needs-review could not be loaded")
        return self._needs_review_row(row, created=True)

    def list_needs_reviews(
        self,
        *,
        project: str,
        status: str | None = None,
        key: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_project = project.strip()
        if status is not None and status not in NEEDS_REVIEW_STATUSES:
            raise ValueError(f"unsupported needs-review status: {status}")
        clauses = ["project = ?"]
        parameters: list[Any] = [normalized_project]
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        if key is not None:
            clauses.append("key = ?")
            parameters.append(validate_topic_key(key))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM needs_reviews
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC
                """,  # nosec B608
                tuple(parameters),
            ).fetchall()
        return [self._needs_review_row(row) for row in rows]

    def resolve_needs_review(
        self,
        review_id: int,
        *,
        outcome: str,
        result_version_id: int | None = None,
        resolved_at: int | None = None,
    ) -> dict[str, Any] | None:
        if outcome not in NEEDS_REVIEW_OUTCOMES:
            raise ValueError(f"unsupported needs-review outcome: {outcome}")
        timestamp = _now(resolved_at)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM needs_reviews WHERE id = ? AND status = 'open'",
                (int(review_id),),
            ).fetchone()
            if row is None:
                return None
            if outcome == "change" and result_version_id is None:
                raise ValueError("change outcome requires result_version_id")
            if result_version_id is not None:
                version = connection.execute(
                    "SELECT id FROM memory_versions WHERE id = ?",
                    (int(result_version_id),),
                ).fetchone()
                if version is None:
                    raise KeyError(f"memory version not found: {result_version_id}")
            connection.execute(
                """
                UPDATE needs_reviews
                SET status = 'resolved', outcome = ?, result_version_id = ?, resolved_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (outcome, result_version_id, timestamp, int(review_id)),
            )
            self._record_event(
                connection,
                "memory.needs-review-resolved",
                project=row["project"],
                payload={
                    "reviewId": int(review_id),
                    "key": row["key"],
                    "outcome": outcome,
                    "resultVersionId": result_version_id,
                },
                occurred_at=timestamp,
            )
            connection.commit()
            resolved = connection.execute(
                "SELECT * FROM needs_reviews WHERE id = ?", (int(review_id),)
            ).fetchone()
        return self._needs_review_row(resolved) if resolved is not None else None

    @staticmethod
    def _needs_review_row(
        row: sqlite3.Row, *, created: bool | None = None
    ) -> dict[str, Any]:
        result = {
            "id": int(row["id"]),
            "project": row["project"],
            "key": row["key"],
            "status": row["status"],
            "sourceType": row["source_type"],
            "sourceId": row["source_id"],
            "contentHash": row["content_hash"],
            "reason": row["reason"],
            "outcome": row["outcome"],
            "resultVersionId": row["result_version_id"],
            "createdAt": int(row["created_at"]),
            "resolvedAt": row["resolved_at"],
        }
        if created is not None:
            result["created"] = created
        return result

    def create_global_memory_request(
        self,
        key: str,
        *,
        value: str | None,
        source: str | None,
        kind: str,
        rationale: str,
        requested_from_project: str,
        created_at: int | None = None,
    ) -> dict[str, Any]:
        proposal = self._validate_global_proposal(
            {
                "key": key,
                "value": value,
                "source": source,
                "kind": kind,
                "rationale": rationale,
            }
        )
        project = requested_from_project.strip()
        if not project:
            raise ValueError("requested_from_project cannot be empty")
        timestamp = _now(created_at)
        serialized = stable_json(proposal)
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM global_memory_requests
                WHERE status = 'pending'
                  AND key = ?
                  AND proposed_json = ?
                  AND requested_from_project = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (proposal["key"], serialized, project),
            ).fetchone()
            if existing is not None:
                result = self._global_memory_request_row(existing)
                result["created"] = False
                return result
            cursor = connection.execute(
                """
                INSERT INTO global_memory_requests(
                    key, initial_json, proposed_json, rationale,
                    requested_from_project, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal["key"],
                    serialized,
                    serialized,
                    proposal["rationale"],
                    project,
                    timestamp,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("global-memory request insert did not return an id")
            request_id = int(cursor.lastrowid)
            self._record_event(
                connection,
                "global-memory.requested",
                project=project,
                payload={"requestId": request_id, "proposal": proposal},
                occurred_at=timestamp,
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM global_memory_requests WHERE id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("created global-memory request could not be loaded")
        result = self._global_memory_request_row(row)
        result["created"] = True
        return result

    def update_global_memory_request(
        self,
        request_id: int,
        *,
        key: str,
        value: str | None,
        source: str | None,
        kind: str,
        rationale: str,
        edited_at: int | None = None,
    ) -> dict[str, Any] | None:
        proposal = self._validate_global_proposal(
            {
                "key": key,
                "value": value,
                "source": source,
                "kind": kind,
                "rationale": rationale,
            }
        )
        timestamp = _now(edited_at)
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM global_memory_requests WHERE id = ? AND status = 'pending'",
                (int(request_id),),
            ).fetchone()
            if existing is None:
                return None
            connection.execute(
                """
                UPDATE global_memory_requests
                SET key = ?, proposed_json = ?, rationale = ?, edited_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    proposal["key"],
                    stable_json(proposal),
                    proposal["rationale"],
                    timestamp,
                    int(request_id),
                ),
            )
            self._record_event(
                connection,
                "global-memory.edited",
                project=existing["requested_from_project"],
                payload={"requestId": int(request_id), "proposal": proposal},
                occurred_at=timestamp,
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM global_memory_requests WHERE id = ?", (int(request_id),)
            ).fetchone()
        return self._global_memory_request_row(row) if row is not None else None

    def decide_global_memory_request(
        self,
        request_id: int,
        *,
        decision: str,
        decided_at: int | None = None,
    ) -> dict[str, Any] | None:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        timestamp = _now(decided_at)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM global_memory_requests WHERE id = ? AND status = 'pending'",
                (int(request_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            proposal = self._validate_global_proposal(json.loads(row["proposed_json"]))
            status = "approved" if decision == "approve" else "rejected"
            memory_action: str | None = None
            version_id: int | None = None
            if decision == "approve":
                latest_relevant: str | None = None
                events = connection.execute(
                    """
                    SELECT event_type, payload_json
                    FROM context_events
                    WHERE event_type IN (
                        'global-memory.requested',
                        'global-memory.edited',
                        'global-memory.approved'
                    )
                    ORDER BY id
                    """
                ).fetchall()
                for event in events:
                    payload = json.loads(event["payload_json"])
                    event_request_id = int(payload.get("requestId", -1))
                    if (
                        event_request_id == int(request_id)
                        and event["event_type"]
                        in {"global-memory.requested", "global-memory.edited"}
                    ):
                        latest_relevant = "current"
                    elif (
                        event_request_id != int(request_id)
                        and event["event_type"] == "global-memory.approved"
                        and isinstance(payload.get("finalProposal"), dict)
                        and payload["finalProposal"].get("key") == proposal["key"]
                    ):
                        latest_relevant = "conflict"
                if latest_relevant == "conflict":
                    connection.rollback()
                    raise ValueError(
                        "global memory changed after this request was proposed; "
                        "review and save the pending request again before approval"
                    )
                before = connection.execute(
                    """
                    SELECT node_type AS kind, value, source
                    FROM context_nodes
                    WHERE namespace = ? AND project = '' AND stable_key = ?
                    """,
                    (MEMORY_NAMESPACE, proposal["key"]),
                ).fetchone()
                before_hash = topic_hash(dict(before)) if before is not None else None
                after_hash = topic_hash(proposal)
                if before_hash == after_hash:
                    memory_action = "unchanged"
                else:
                    memory_action = "created" if before is None else "updated"
                    connection.execute(
                        """
                        INSERT INTO context_nodes(
                            id, namespace, project, stable_key, node_type, label,
                            value, source, origin, properties_json, set_at,
                            created_at, updated_at
                        ) VALUES (?, ?, '', ?, ?, ?, ?, ?, 'human', '{}', ?, ?, ?)
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
                            _stable_id(MEMORY_NAMESPACE, "", proposal["key"]),
                            MEMORY_NAMESPACE,
                            proposal["key"],
                            proposal["kind"],
                            proposal["key"],
                            proposal["value"],
                            proposal["source"],
                            timestamp,
                            timestamp,
                            timestamp,
                        ),
                    )
                    self._upsert_embedding_target(
                        connection,
                        node_id=_stable_id(MEMORY_NAMESPACE, "", proposal["key"]),
                        priority=EMBEDDING_PRIORITIES["remembered"],
                        reason="remembered",
                        timestamp=timestamp,
                    )
                    version_id = self._record_memory_version(
                        connection,
                        project="",
                        key=proposal["key"],
                        kind=proposal["kind"],
                        value=proposal["value"],
                        source=proposal["source"],
                        origin="human",
                        created_at=timestamp,
                    )
            connection.execute(
                """
                UPDATE global_memory_requests
                SET status = ?, final_json = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, stable_json(proposal), timestamp, int(request_id)),
            )
            self._record_event(
                connection,
                f"global-memory.{status}",
                project=row["requested_from_project"],
                payload={
                    "requestId": int(request_id),
                    "initialProposal": json.loads(row["initial_json"]),
                    "finalProposal": proposal,
                    "memoryAction": memory_action,
                    "versionId": version_id,
                },
                occurred_at=timestamp,
            )
            connection.commit()
            decided = connection.execute(
                "SELECT * FROM global_memory_requests WHERE id = ?", (int(request_id),)
            ).fetchone()
        result = self._global_memory_request_row(decided) if decided is not None else None
        if result is not None:
            result["memoryAction"] = memory_action
            result["versionId"] = version_id
        return result

    def list_global_memory_requests(
        self, status: str | None = None
    ) -> list[dict[str, Any]]:
        if status is not None and status not in GLOBAL_MEMORY_REQUEST_STATUSES:
            raise ValueError(f"unsupported global-memory request status: {status}")
        query = "SELECT * FROM global_memory_requests"
        parameters: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY created_at DESC, id DESC"
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._global_memory_request_row(row) for row in rows]

    @staticmethod
    def _validate_global_proposal(raw: dict[str, Any]) -> dict[str, Any]:
        key = validate_topic_key(str(raw.get("key", "")))
        kind = str(raw.get("kind", "note"))
        if kind not in {"decision", "note", "doc-ref"}:
            raise ValueError("global memory kind must be decision, note, or doc-ref")
        value = raw.get("value")
        source = raw.get("source")
        if value is not None and not isinstance(value, str):
            raise ValueError("global memory value must be a string")
        if source is not None and not isinstance(source, str):
            raise ValueError("global memory source must be a string")
        if (value is None) == (source is None):
            raise ValueError("exactly one of value or source is required")
        if value is not None and (not value.strip() or len(value) > 65_536):
            raise ValueError("global memory value must be between 1 and 65536 characters")
        if source is not None and (not source.strip() or len(source) > 4096):
            raise ValueError("global memory source must be between 1 and 4096 characters")
        rationale = str(raw.get("rationale", "")).strip()
        if not rationale or len(rationale) > 4096:
            raise ValueError("rationale must be between 1 and 4096 characters")
        return {
            "key": key,
            "kind": kind,
            "value": value.strip() if value is not None else None,
            "source": source.strip() if source is not None else None,
            "rationale": rationale,
        }

    @staticmethod
    def _global_memory_request_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "status": row["status"],
            "key": row["key"],
            "initialProposal": json.loads(row["initial_json"]),
            "proposal": json.loads(row["proposed_json"]),
            "finalProposal": json.loads(row["final_json"]) if row["final_json"] else None,
            "rationale": row["rationale"],
            "requestedFromProject": row["requested_from_project"],
            "createdAt": int(row["created_at"]),
            "editedAt": row["edited_at"],
            "decidedAt": row["decided_at"],
        }

    def project_memory_report(
        self,
        *,
        project: str,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        normalized_project = project.strip()
        clauses = ["project = ?", "event_type LIKE 'memory.%'"]
        parameters: list[Any] = [normalized_project]
        if since is not None:
            clauses.append("occurred_at >= ?")
            parameters.append(int(since))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, event_type, payload_json, occurred_at
                FROM context_events
                WHERE {' AND '.join(clauses)}
                ORDER BY occurred_at DESC, id DESC
                """,  # nosec B608
                tuple(parameters),
            ).fetchall()
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            date = time.strftime("%Y-%m-%d", time.localtime(int(row["occurred_at"])))
            groups.setdefault(date, []).append(
                {
                    "id": int(row["id"]),
                    "type": row["event_type"],
                    "payload": json.loads(row["payload_json"]),
                    "occurredAt": int(row["occurred_at"]),
                }
            )
        return [{"date": date, "events": groups[date]} for date in sorted(groups, reverse=True)]

    def record_memory_usage(
        self,
        node_id: str,
        *,
        event: str,
        occurred_at: int | None = None,
    ) -> None:
        if event not in {"selected", "expanded"}:
            raise ValueError(f"unsupported memory usage event: {event}")
        timestamp = _now(occurred_at)
        count_column = "selected_count" if event == "selected" else "expanded_count"
        time_column = "last_selected_at" if event == "selected" else "last_expanded_at"
        with self.connect() as connection:
            node = connection.execute(
                "SELECT id FROM context_nodes WHERE id = ? AND namespace = ?",
                (node_id, MEMORY_NAMESPACE),
            ).fetchone()
            if node is None:
                raise KeyError(f"memory node not found: {node_id}")
            connection.execute(
                f"""
                INSERT INTO memory_usage(node_id, {count_column}, {time_column})
                VALUES (?, 1, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    {count_column} = {count_column} + 1,
                    {time_column} = excluded.{time_column}
                """,  # nosec B608
                (node_id, timestamp),
            )
            connection.commit()

    def memory_usage(self, node_ids: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
        normalized = list(dict.fromkeys(node_id.strip() for node_id in node_ids if node_id.strip()))
        parameters: tuple[Any, ...] = ()
        query = "SELECT * FROM memory_usage"
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            query += f" WHERE node_id IN ({placeholders})"  # nosec B608
            parameters = tuple(normalized)
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return {
            str(row["node_id"]): {
                "selectedCount": int(row["selected_count"]),
                "expandedCount": int(row["expanded_count"]),
                "lastSelectedAt": row["last_selected_at"],
                "lastExpandedAt": row["last_expanded_at"],
            }
            for row in rows
        }

    def delete_topic(self, key: str, *, project: str = "") -> bool:
        key = validate_topic_key(key)
        normalized_project = project.strip()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM context_nodes
                WHERE namespace = ? AND project = ? AND stable_key = ?
                """,
                (MEMORY_NAMESPACE, normalized_project, key),
            )
            connection.commit()
            return cursor.rowcount > 0

    def confirm_topic(
        self,
        key: str,
        *,
        project: str = "",
        confirmed_at: int | None = None,
    ) -> bool:
        key = validate_topic_key(key)
        normalized_project = project.strip()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE context_nodes SET set_at = ?, updated_at = ?
                WHERE namespace = ? AND project = ? AND stable_key = ?
                """,
                (
                    _now(confirmed_at),
                    _now(confirmed_at),
                    MEMORY_NAMESPACE,
                    normalized_project,
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
        topics = self.list_topics(project=project)
        node_ids = [
            _stable_id(MEMORY_NAMESPACE, str(topic["project"]), str(topic["key"]))
            for topic in topics
        ]
        usage = self.memory_usage(node_ids)
        open_reviews = self.list_needs_reviews(project=project, status="open") if project else []
        review_counts: dict[str, int] = {}
        for review in open_reviews:
            review_counts[review["key"]] = review_counts.get(review["key"], 0) + 1
        with self.connect() as connection:
            version_rows = connection.execute(
                """
                SELECT project, key, COUNT(*) AS count
                FROM memory_versions
                WHERE project IN ('', ?)
                GROUP BY project, key
                """,
                (project.strip(),),
            ).fetchall()
        version_counts = {
            (str(row["project"]), str(row["key"])): int(row["count"]) for row in version_rows
        }
        return [
            {
                **topic,
                "category": memory_category(str(topic["kind"])),
                "stale": is_stale(
                    topic["set_at"], now=now, stale_after_days=stale_after_days
                ),
                "usage": usage.get(
                    _stable_id(
                        MEMORY_NAMESPACE,
                        str(topic["project"]),
                        str(topic["key"]),
                    ),
                    {
                        "selectedCount": 0,
                        "expandedCount": 0,
                        "lastSelectedAt": None,
                        "lastExpandedAt": None,
                    },
                ),
                "needsReviewCount": review_counts.get(str(topic["key"]), 0),
                "versionCount": version_counts.get(
                    (str(topic["project"]), str(topic["key"])), 0
                ),
            }
            for topic in topics
        ]

    def import_graph(self, graph_path: str | Path, *, project: str) -> dict[str, Any]:
        """Import a complete graph artifact into the canonical structural graph."""
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
                "hyperedges": int(snapshot["hyperedge_count"]),
                "contentHash": snapshot["content_hash"],
            }

        raw_bytes = path.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        value = json.loads(raw_bytes.decode("utf-8"))
        return self.replace_structural_graph(
            value,
            project=normalized_project,
            source_path=path,
            source_mtime_ns=stat.st_mtime_ns,
            source_size=stat.st_size,
            content_hash=digest,
            event_type="graph.imported",
        )

    def replace_structural_graph(
        self,
        graph: dict[str, Any],
        *,
        project: str,
        source_path: str | Path | None = None,
        source_mtime_ns: int = 0,
        source_size: int = 0,
        content_hash: str | None = None,
        event_type: str = "graph.replaced",
    ) -> dict[str, Any]:
        """Atomically replace a project's canonical structural graph."""
        normalized_project = project.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
            raise ValueError("graph must contain a nodes list")
        nodes = [node for node in graph["nodes"] if isinstance(node, dict)]
        links_value = graph.get("links", graph.get("edges", []))
        links = (
            [link for link in links_value if isinstance(link, dict)]
            if isinstance(links_value, list)
            else []
        )
        hyperedges_value = graph.get("hyperedges", [])
        hyperedges = (
            [hyperedge for hyperedge in hyperedges_value if isinstance(hyperedge, dict)]
            if isinstance(hyperedges_value, list)
            else []
        )
        metadata = {
            key: value
            for key, value in graph.items()
            if key not in {"nodes", "links", "edges", "hyperedges"}
        }
        normalized_source = (
            str(Path(source_path).expanduser().resolve()) if source_path is not None else ""
        )
        digest = content_hash or hashlib.sha256(
            stable_json(
                {
                    **metadata,
                    "nodes": nodes,
                    "links": links,
                    "hyperedges": hyperedges,
                }
            ).encode("utf-8")
        ).hexdigest()
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
                    normalized_source or None,
                    timestamp,
                    timestamp,
                    timestamp,
                )
            )

        edge_rows: list[tuple[Any, ...]] = []
        edge_occurrences: dict[str, int] = {}
        for link in links:
            source_key = str(link.get("source", ""))
            target_key = str(link.get("target", ""))
            source_id = node_ids.get(source_key)
            target_id = node_ids.get(target_key)
            if source_id is None or target_id is None:
                continue
            relation = str(link.get("relation") or "related")
            edge_identity = stable_json([normalized_project, source_key, target_key, relation, link])
            occurrence = edge_occurrences.get(edge_identity, 0)
            edge_occurrences[edge_identity] = occurrence + 1
            edge_rows.append(
                (
                    hashlib.sha256(f"{edge_identity}\0{occurrence}".encode("utf-8")).hexdigest(),
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
            matching_views = [
                view
                for view in connection.execute(
                    """
                    SELECT id, stable_key, source AS locator FROM context_nodes
                    WHERE namespace = ? AND node_type = 'resource-view'
                    ORDER BY stable_key
                    """,
                    (RESOURCE_NAMESPACE,),
                ).fetchall()
                if resolve_project_id(str(view["locator"])) == normalized_project
            ]
            for view in matching_views:
                for code_node_id in node_ids.values():
                    self._upsert_edge(
                        connection,
                        source_id=str(view["id"]),
                        target_id=code_node_id,
                        relation="contains",
                        origin="derived",
                        properties={"graphProject": normalized_project},
                        timestamp=timestamp,
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
                    hyperedge_count, metadata_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project) DO UPDATE SET
                    source_path = excluded.source_path,
                    source_mtime_ns = excluded.source_mtime_ns,
                    source_size = excluded.source_size,
                    content_hash = excluded.content_hash,
                    built_at_commit = excluded.built_at_commit,
                    node_count = excluded.node_count,
                    edge_count = excluded.edge_count,
                    hyperedge_count = excluded.hyperedge_count,
                    metadata_json = excluded.metadata_json,
                    imported_at = excluded.imported_at
                """,
                (
                    normalized_project,
                    normalized_source,
                    int(source_mtime_ns),
                    int(source_size),
                    digest,
                    graph.get("built_at_commit"),
                    len(node_rows),
                    len(edge_rows),
                    len(hyperedges),
                    stable_json({**metadata, "hyperedges": hyperedges}),
                    timestamp,
                ),
            )
            self._restore_seed_links(connection, normalized_project, timestamp)
            self._record_event(
                connection,
                event_type,
                project=normalized_project,
                payload={
                    "source": normalized_source or None,
                    "contentHash": digest,
                    "nodes": len(node_rows),
                    "edges": len(edge_rows),
                    "hyperedges": len(hyperedges),
                },
                occurred_at=timestamp,
            )
            connection.commit()
        return {
            "imported": True,
            "project": normalized_project,
            "nodes": len(node_rows),
            "edges": len(edge_rows),
            "hyperedges": len(hyperedges),
            "contentHash": digest,
        }

    def structural_graph(self, *, project: str) -> dict[str, Any] | None:
        normalized_project = project.strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        with self.connect() as connection:
            snapshot = connection.execute(
                "SELECT metadata_json FROM graph_snapshots WHERE project = ?",
                (normalized_project,),
            ).fetchone()
            if snapshot is None:
                return None
            node_rows = connection.execute(
                """
                SELECT stable_key, properties_json
                FROM context_nodes
                WHERE namespace = 'code' AND project = ?
                ORDER BY stable_key
                """,
                (normalized_project,),
            ).fetchall()
            edge_rows = connection.execute(
                """
                SELECT edge.properties_json,
                       source.stable_key AS source_key,
                       target.stable_key AS target_key
                FROM context_edges edge
                JOIN context_nodes source ON source.id = edge.source_id
                JOIN context_nodes target ON target.id = edge.target_id
                WHERE edge.origin = 'structural'
                  AND source.project = ? AND target.project = ?
                ORDER BY edge.id
                """,
                (normalized_project, normalized_project),
            ).fetchall()
        stored_metadata = json.loads(snapshot["metadata_json"])
        hyperedges = stored_metadata.pop("hyperedges", [])
        nodes: list[dict[str, Any]] = []
        for row in node_rows:
            node = json.loads(row["properties_json"])
            node["id"] = row["stable_key"]
            nodes.append(node)
        links: list[dict[str, Any]] = []
        for row in edge_rows:
            link = json.loads(row["properties_json"])
            link["source"] = row["source_key"]
            link["target"] = row["target_key"]
            links.append(link)
        return {
            **stored_metadata,
            "nodes": nodes,
            "links": links,
            "hyperedges": hyperedges,
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

    def list_retrieval_nodes(
        self,
        *,
        project: str,
        memory_project: str | None = None,
        code_projects: Sequence[str] = (),
        resource_node_ids: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """Return human memory and structural nodes visible to one project."""
        normalized_project = project.strip()
        normalized_memory_project = (memory_project or project).strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        if not normalized_memory_project:
            raise ValueError("memory_project cannot be empty")
        selected_code_projects = _context_projects(normalized_project, code_projects)
        selected_resource_ids = tuple(dict.fromkeys(resource_node_ids))
        code_placeholders = ",".join("?" for _ in selected_code_projects)
        resource_clause = ""
        parameters: list[Any] = [MEMORY_NAMESPACE, normalized_memory_project]
        parameters.extend(selected_code_projects)
        if selected_resource_ids:
            resource_placeholders = ",".join("?" for _ in selected_resource_ids)
            resource_clause = f" OR id IN ({resource_placeholders})"
            parameters.extend(selected_resource_ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, namespace, project, stable_key, node_type, label,
                       value, source, origin, properties_json, external_id,
                       source_graph, set_at, created_at, updated_at
                FROM context_nodes
                WHERE (namespace = ? AND project IN ('', ?))
                   OR (namespace = 'code' AND project IN ({code_placeholders}))
                   {resource_clause}
                ORDER BY namespace, stable_key
                """,  # nosec B608
                parameters,
            ).fetchall()
        nodes = [_context_node(row) for row in rows]
        return _prefer_project_memory(nodes, normalized_memory_project)

    def retrieval_inventory(
        self,
        *,
        project: str,
        memory_project: str | None = None,
        code_projects: Sequence[str] = (),
        resource_node_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Aggregate a compact catalog without loading node content."""
        normalized_project = project.strip()
        normalized_memory_project = (memory_project or project).strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        if not normalized_memory_project:
            raise ValueError("memory_project cannot be empty")
        selected_code_projects = _context_projects(normalized_project, code_projects)
        selected_resource_ids = tuple(dict.fromkeys(resource_node_ids))
        code_placeholders = ",".join("?" for _ in selected_code_projects)
        visible_topics = self.list_topics(project=normalized_memory_project)
        with self.connect() as connection:
            code_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM context_nodes "
                    f"WHERE namespace = 'code' AND project IN ({code_placeholders})",  # nosec B608
                    selected_code_projects,
                ).fetchone()[0]
            )
            type_rows = connection.execute(
                f"""
                SELECT node_type, COUNT(*) AS count
                FROM context_nodes
                WHERE namespace = 'code' AND project IN ({code_placeholders})
                GROUP BY node_type
                ORDER BY count DESC, node_type ASC
                LIMIT 16
                """,  # nosec B608
                selected_code_projects,
            ).fetchall()
            topic_rows = connection.execute(
                """
                SELECT stable_key FROM context_nodes
                WHERE namespace = ? AND project IN ('', ?) ORDER BY stable_key
                """,
                (MEMORY_NAMESPACE, normalized_memory_project),
            ).fetchall()
            resource_count = 0
            resource_type_rows: list[sqlite3.Row] = []
            if selected_resource_ids:
                placeholders = ",".join("?" for _ in selected_resource_ids)
                resource_count = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM context_nodes WHERE id IN ({placeholders})",  # nosec B608
                        selected_resource_ids,
                    ).fetchone()[0]
                )
                resource_type_rows = connection.execute(
                    f"""
                    SELECT node_type, COUNT(*) AS count
                    FROM context_nodes
                    WHERE id IN ({placeholders})
                    GROUP BY node_type
                    ORDER BY count DESC, node_type
                    """,  # nosec B608
                    selected_resource_ids,
                ).fetchall()
        namespaces: dict[str, int] = {}
        if visible_topics:
            namespaces[MEMORY_NAMESPACE] = len(visible_topics)
        if code_count:
            namespaces["code"] = code_count
        if resource_count:
            namespaces[RESOURCE_NAMESPACE] = resource_count
        return {
            "namespaces": namespaces,
            "codeTypes": [
                {"name": row["node_type"], "count": int(row["count"])} for row in type_rows
            ],
            "resourceTypes": [
                {"name": row["node_type"], "count": int(row["count"])}
                for row in resource_type_rows
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
        include_resources: bool = True,
        memory_project: str | None = None,
        code_projects: Sequence[str] = (),
        resource_node_ids: Sequence[str] = (),
        limit: int = 2_000,
    ) -> list[dict[str, Any]]:
        """Generate a bounded FTS/path candidate pool for precise ranking."""
        normalized_project = project.strip()
        normalized_memory_project = (memory_project or project).strip()
        if not normalized_project:
            raise ValueError("project cannot be empty")
        if not normalized_memory_project:
            raise ValueError("memory_project cannot be empty")
        selected_code_projects = _context_projects(normalized_project, code_projects)
        selected_resource_ids = tuple(dict.fromkeys(resource_node_ids))
        normalized_terms = list(
            dict.fromkeys(term.strip().lower() for term in terms if term.strip())
        )
        normalized_paths = list(
            dict.fromkeys(path.strip().lower() for path in active_paths if path.strip())
        )
        parsed_limit = int(limit)
        if parsed_limit < 1 or parsed_limit > 10_000:
            raise ValueError("retrieval candidate limit must be between 1 and 10000")
        if not include_memory and not include_code and not include_resources:
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
                if namespace == MEMORY_NAMESPACE:
                    visibility = "node.project IN ('', ?)"
                    visibility_parameters: tuple[Any, ...] = (normalized_memory_project,)
                elif namespace == "code":
                    placeholders = ",".join("?" for _ in selected_code_projects)
                    visibility = f"node.project IN ({placeholders})"
                    visibility_parameters = selected_code_projects
                else:
                    if not selected_resource_ids:
                        return
                    placeholders = ",".join("?" for _ in selected_resource_ids)
                    visibility = f"node.id IN ({placeholders})"
                    visibility_parameters = selected_resource_ids
                try:
                    rows = connection.execute(
                        select
                        + f"""
                        FROM context_nodes_fts search
                        JOIN context_nodes node ON node.id = search.id
                        WHERE context_nodes_fts MATCH ?
                          AND node.namespace = ?
                          AND {visibility}
                        ORDER BY bm25(context_nodes_fts), node.stable_key
                        LIMIT ?
                        """,  # nosec B608
                        (
                            fts_query,
                            namespace,
                            *visibility_parameters,
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

            if can_use_fts and include_resources:
                add_fts_candidates(
                    RESOURCE_NAMESPACE,
                    min(max(1, parsed_limit // 4), parsed_limit - len(by_id)),
                )
                add_fts_candidates(
                    CONTEXT_NAMESPACE,
                    min(max(1, parsed_limit // 8), parsed_limit - len(by_id)),
                )

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
                    + f"""
                    FROM context_nodes node
                    WHERE node.namespace = 'code'
                      AND node.project IN ({','.join('?' for _ in selected_code_projects)})
                      AND instr(lower(COALESCE(node.source, '')), ?) > 0
                    ORDER BY node.stable_key
                    LIMIT ?
                    """,  # nosec B608
                    (*selected_code_projects, path, remaining),
                ).fetchall()
                before = len(by_id)
                by_id.update((str(row["id"]), _context_node(row)) for row in rows)
                path_added += len(by_id) - before

            if can_use_fts and include_code:
                add_fts_candidates("code", parsed_limit - len(by_id))
            if not by_id:
                code_placeholders = ",".join("?" for _ in selected_code_projects)
                resource_clause = ""
                resource_parameters: tuple[str, ...] = ()
                if include_resources and selected_resource_ids:
                    placeholders = ",".join("?" for _ in selected_resource_ids)
                    resource_clause = f" OR node.id IN ({placeholders})"
                    resource_parameters = selected_resource_ids
                rows = connection.execute(
                    select
                    + f"""
                    FROM context_nodes node
                    WHERE (? = 1 AND node.namespace = ? AND node.project IN ('', ?))
                       OR (? = 1 AND node.namespace = 'code'
                           AND node.project IN ({code_placeholders}))
                       {resource_clause}
                    ORDER BY node.namespace, node.stable_key
                    LIMIT ?
                    """,  # nosec B608
                    (
                        int(include_memory),
                        MEMORY_NAMESPACE,
                        normalized_memory_project,
                        int(include_code),
                        *selected_code_projects,
                        *resource_parameters,
                        parsed_limit,
                    ),
                ).fetchall()
                by_id.update((str(row["id"]), _context_node(row)) for row in rows)
        return _prefer_project_memory(
            [by_id[node_id] for node_id in sorted(by_id)], normalized_memory_project
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
            "hyperedgeCount": row["hyperedge_count"],
            "importedAt": row["imported_at"],
        }

    def code_context(self, node_id: str, *, edge_limit: int = 12) -> dict[str, Any] | None:
        """Build a bounded context packet around one structural node."""
        with self.connect() as connection:
            node_row = connection.execute(
                """
                SELECT stable_key, label, source, node_type, project, properties_json
                FROM context_nodes WHERE id = ? AND namespace = 'code'
                """,
                (node_id,),
            ).fetchone()
            if node_row is None:
                return None
            view_rows = connection.execute(
                """
                SELECT view.stable_key AS view_id, view.source AS locator,
                       view.properties_json AS view_properties,
                       resource.stable_key AS resource_id,
                       resource.label AS resource_label,
                       resource.properties_json AS resource_properties
                FROM context_nodes view
                JOIN context_edges relation ON relation.target_id = view.id
                    AND relation.relation = 'has-view'
                JOIN context_nodes resource ON resource.id = relation.source_id
                WHERE view.namespace = ? AND view.node_type = 'resource-view'
                ORDER BY view.stable_key
                """,
                (RESOURCE_NAMESPACE,),
            ).fetchall()
            view_row = next(
                (
                    row
                    for row in view_rows
                    if resolve_project_id(str(row["locator"])) == node_row["project"]
                ),
                None,
            )
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
        view_properties = _json_object(view_row["view_properties"]) if view_row else {}
        resource_properties = _json_object(view_row["resource_properties"]) if view_row else {}
        resource_context = (
            {
                "resourceId": view_row["resource_id"],
                "resourceLabel": view_row["resource_label"],
                "provider": resource_properties.get("provider"),
                "externalIdentity": resource_properties.get("externalIdentity"),
                "viewId": view_row["view_id"],
                "locator": view_row["locator"],
                "revision": view_properties.get("revision"),
                "view": view_properties,
            }
            if view_row is not None
            else None
        )
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
            "resource": resource_context,
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
        session_context: dict[str, Any] | None = None,
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
                properties=session_context,
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
            self._upsert_embedding_target(
                connection,
                node_id=node_id,
                priority=100,
                reason="delivered",
                timestamp=timestamp,
            )
            connection.commit()
        return digest

    def record_embedding_targets(
        self,
        node_ids: Sequence[str],
        *,
        reason: str,
        recorded_at: int | None = None,
    ) -> None:
        if reason not in EMBEDDING_PRIORITIES:
            raise ValueError(f"unsupported embedding signal: {reason}")
        normalized = list(dict.fromkeys(node_id.strip() for node_id in node_ids if node_id.strip()))
        if not normalized:
            return
        timestamp = _now(recorded_at)
        with self.connect() as connection:
            for node_id in normalized:
                self._upsert_embedding_target(
                    connection,
                    node_id=node_id,
                    priority=EMBEDDING_PRIORITIES[reason],
                    reason=reason,
                    timestamp=timestamp,
                )
            connection.commit()

    @staticmethod
    def _upsert_embedding_target(
        connection: sqlite3.Connection,
        *,
        node_id: str,
        priority: int,
        reason: str,
        timestamp: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO embedding_targets(node_id, priority, reason, requested_at, usage_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(node_id) DO UPDATE SET
                priority = MAX(priority, excluded.priority),
                reason = CASE
                    WHEN excluded.priority >= priority THEN excluded.reason
                    ELSE reason
                END,
                requested_at = excluded.requested_at,
                usage_count = usage_count + 1
            """,
            (node_id, priority, reason, timestamp),
        )

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
                properties_json = CASE
                    WHEN excluded.properties_json = '{}' THEN context_nodes.properties_json
                    ELSE excluded.properties_json
                END,
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
            session_rows = connection.execute(
                """
                SELECT stable_key, project, properties_json
                FROM context_nodes
                WHERE namespace = 'session' AND node_type = 'session'
                """
            ).fetchall()
            event_rows = connection.execute(
                """
                SELECT event.session_id, event.payload_json,
                       node.namespace, node.node_type, node.label, node.source, node.origin
                FROM context_events event
                LEFT JOIN context_nodes node ON node.id = event.object_id
                WHERE event.event_type = 'context.delivered'
                ORDER BY event.id DESC
                """
            ).fetchall()

        contexts = {
            (str(row["stable_key"]), str(row["project"])): _json_object(row["properties_json"])
            for row in session_rows
        }
        details: dict[tuple[str, str], dict[str, Any]] = {}
        for row in event_rows:
            payload = _json_object(row["payload_json"])
            key = str(payload.get("key") or "")
            identity = (str(row["session_id"]), key)
            if not key or identity in details:
                continue
            rendered = str(payload.get("rendered") or "").strip()
            details[identity] = {
                "label": row["label"] or key,
                "namespace": row["namespace"],
                "kind": row["node_type"],
                "origin": row["origin"],
                "source": row["source"],
                "preview": rendered[:400].rstrip() if rendered else None,
            }

        sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = sessions.setdefault(
                row["session_id"],
                {
                    "id": row["session_id"],
                    "project": row["project"],
                    "context": contexts.get(
                        (str(row["session_id"]), str(row["project"] or ""))
                    ),
                    "items": [],
                },
            )
            if entry["project"] is None and row["project"] is not None:
                entry["project"] = row["project"]
            entry["items"].append(
                {
                    "key": row["key"],
                    "valueHash": row["value_hash"],
                    "deliveredAt": row["delivered_at"],
                    **details.get((str(row["session_id"]), str(row["key"])), {}),
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

    def session_delivered_node_ids(self, session_id: str) -> set[str]:
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id cannot be empty")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT object_id
                FROM context_events
                WHERE session_id = ?
                  AND event_type = 'context.delivered'
                  AND object_id IS NOT NULL
                """,
                (normalized,),
            ).fetchall()
        return {str(row["object_id"]) for row in rows}

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
            request = connection.execute(
                "SELECT * FROM requests WHERE id = ? AND status = 'open'",
                (int(request_id),),
            ).fetchone()
            if request is None:
                return False
            project = str(request["project"] or "")
            topic = connection.execute(
                """
                SELECT id FROM context_nodes
                WHERE namespace = ? AND stable_key = ? AND project IN ('', ?)
                ORDER BY CASE WHEN project = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (MEMORY_NAMESPACE, key, project, project),
            ).fetchone()
            if topic is None:
                raise KeyError(f"topic not found: {key}")
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
                "projects": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_nodes WHERE namespace = ? AND node_type = 'project'",
                        (CONTEXT_NAMESPACE,),
                    ).fetchone()[0]
                ),
                "resources": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_nodes WHERE namespace = ? AND node_type LIKE 'resource.%'",
                        (RESOURCE_NAMESPACE,),
                    ).fetchone()[0]
                ),
                "resourceViews": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_nodes WHERE namespace = ? AND node_type = 'resource-view'",
                        (RESOURCE_NAMESPACE,),
                    ).fetchone()[0]
                ),
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
                "memoryVersions": int(
                    connection.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0]
                ),
                "needsReviews": int(
                    connection.execute("SELECT COUNT(*) FROM needs_reviews").fetchone()[0]
                ),
                "memoryUsage": int(
                    connection.execute("SELECT COUNT(*) FROM memory_usage").fetchone()[0]
                ),
                "globalMemoryRequests": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM global_memory_requests"
                    ).fetchone()[0]
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
