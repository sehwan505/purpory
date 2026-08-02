"""Selective embedding projection for context nodes that proved useful."""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import time
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
from typing import Any, Protocol, Sequence
from urllib.parse import urlsplit

from purpory.ollama import ollama_urls
from purpory.supervise.repository import ContextGraphRepository

DEFAULT_MODEL = "qwen3-embedding:0.6b"
DEFAULT_DIMENSIONS = 512
DEFAULT_MAX_BYTES = 128 * 1024 * 1024
POLICY_VERSION = "used-node-v1"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class EmbeddingDocument:
    node_id: str
    text: str
    content_hash: str
    source_updated_at: int
    priority: int


class EmbeddingPolicy(Protocol):
    version: str

    def render(self, node: dict[str, Any]) -> str: ...


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, texts: Sequence[str], *, model: str, dimensions: int) -> list[list[float]]: ...


class UsedNodePolicy:
    """Embed only nodes passed from successful use signals."""

    version = POLICY_VERSION

    def render(self, node: dict[str, Any]) -> str:
        fields = (
            str(node.get("stableKey") or ""),
            str(node.get("label") or ""),
            str(node.get("type") or ""),
            str(node.get("value") or ""),
            str(node.get("source") or ""),
        )
        return "\n".join(value.strip() for value in fields if value.strip())[:16_384]


class OllamaEmbeddingProvider:
    name = "ollama"

    def __init__(self, *, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = timeout_seconds

    def embed(self, texts: Sequence[str], *, model: str, dimensions: int) -> list[list[float]]:
        _, base_url = ollama_urls()
        parsed = urlsplit(base_url)
        if parsed.hostname is None:
            raise RuntimeError("embedding server URL is missing a host")
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=self.timeout_seconds)
        body = json.dumps(
            {"model": model, "input": list(texts), "dimensions": dimensions},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key := os.environ.get("OLLAMA_API_KEY"):
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            connection.request("POST", "/v1/embeddings", body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RuntimeError("embedding response exceeded the size limit")
            if response.status >= 400:
                raise RuntimeError(f"embedding server returned HTTP {response.status}")
            payload = json.loads(raw)
        except (HTTPException, OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"embedding server unavailable: {exc}") from exc
        finally:
            connection.close()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise RuntimeError("embedding response is missing data")
        ordered = sorted(
            data, key=lambda item: item.get("index", -1) if isinstance(item, dict) else -1
        )
        vectors = [item.get("embedding") for item in ordered if isinstance(item, dict)]
        if len(vectors) != len(texts):
            raise RuntimeError("embedding response count does not match input count")
        if any(
            not isinstance(vector, list)
            or len(vector) != dimensions
            or any(
                not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector
            )
            for vector in vectors
        ):
            raise RuntimeError("embedding response contains an invalid vector")
        return [
            [float(value) for value in vector] for vector in vectors if isinstance(vector, list)
        ]


def search_embeddings(
    repository: ContextGraphRepository,
    query: str,
    *,
    memory_project: str,
    code_projects: Sequence[str],
    resource_node_ids: Sequence[str],
    include_memory: bool,
    include_code: bool,
    include_resources: bool,
    limit: int,
    provider: EmbeddingProvider | None = None,
) -> list[dict[str, Any]]:
    """Return cosine-ranked visible nodes from the active embedding profile."""
    if limit < 1 or limit > 200:
        raise ValueError("semantic search limit must be between 1 and 200")
    selected_code_projects = set(code_projects)
    selected_resource_ids = set(resource_node_ids)
    with repository.connect() as connection:
        profile = connection.execute(
            """
            SELECT profile_id, provider, model, dimensions, vector_format
            FROM embedding_profiles WHERE active = 1
            ORDER BY created_at DESC, profile_id LIMIT 1
            """
        ).fetchone()
        if profile is None:
            return []
        rows = connection.execute(
            """
            SELECT embedding.node_id, embedding.vector, node.namespace,
                   node.project, node.stable_key
            FROM node_embeddings embedding
            JOIN context_nodes node ON node.id = embedding.node_id
            WHERE embedding.profile_id = ? AND embedding.vector IS NOT NULL
            """,
            (profile["profile_id"],),
        ).fetchall()
    local_memory_keys = {
        str(row["stable_key"])
        for row in rows
        if row["namespace"] == "memory" and row["project"] == memory_project
    }
    visible = [
        row
        for row in rows
        if (
            include_memory
            and row["namespace"] == "memory"
            and row["project"] in {"", memory_project}
            and not (row["project"] == "" and str(row["stable_key"]) in local_memory_keys)
        )
        or (
            include_code and row["namespace"] == "code" and row["project"] in selected_code_projects
        )
        or (
            include_resources
            and row["namespace"] in {"resource", "context"}
            and row["node_id"] in selected_resource_ids
        )
    ]
    if not visible:
        return []
    if profile["vector_format"] != "f32":
        raise RuntimeError(f"unsupported embedding vector format: {profile['vector_format']}")
    selected_provider = provider or OllamaEmbeddingProvider()
    if selected_provider.name != profile["provider"]:
        raise RuntimeError(f"active embedding provider is unavailable: {profile['provider']}")
    dimensions = int(profile["dimensions"])
    query_vectors = selected_provider.embed(
        [query], model=str(profile["model"]), dimensions=dimensions
    )
    if (
        len(query_vectors) != 1
        or len(query_vectors[0]) != dimensions
        or any(not math.isfinite(float(value)) for value in query_vectors[0])
    ):
        raise RuntimeError("embedding provider returned an invalid query vector")
    query_vector = query_vectors[0]
    query_norm = math.sqrt(sum(value * value for value in query_vector))
    if not query_norm:
        return []

    # ponytail: exact scan is bounded by the embedding byte budget; add a
    # vector index only when measured retrieval latency warrants one.
    ranked: list[dict[str, Any]] = []
    expected_bytes = dimensions * 4
    for row in visible:
        raw = bytes(row["vector"])
        if len(raw) != expected_bytes:
            continue
        vector = struct.unpack(f"<{dimensions}f", raw)
        vector_norm = math.sqrt(sum(value * value for value in vector))
        if not vector_norm:
            continue
        similarity = sum(
            query_value * value for query_value, value in zip(query_vector, vector, strict=True)
        ) / (query_norm * vector_norm)
        if math.isfinite(similarity) and similarity > 0:
            ranked.append({"nodeId": str(row["node_id"]), "similarity": similarity})
    ranked.sort(key=lambda item: (-item["similarity"], item["nodeId"]))
    return ranked[:limit]


class EmbeddingService:
    """Queue use signals and materialize their vectors in the canonical SQLite DB."""

    def __init__(
        self,
        repository: ContextGraphRepository,
        *,
        provider: EmbeddingProvider | None = None,
        policy: EmbeddingPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider or OllamaEmbeddingProvider()
        self.policy = policy or UsedNodePolicy()
        self.model = (
            os.environ.get("PURPORY_EMBEDDING_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        )
        self.dimensions = self._positive_int("PURPORY_EMBEDDING_DIMENSIONS", DEFAULT_DIMENSIONS)
        self.max_bytes = self._positive_int("PURPORY_EMBEDDING_MAX_BYTES", DEFAULT_MAX_BYTES)
        identity = (
            f"{self.provider.name}\0{self.model}\0{self.dimensions}\0f32\0{self.policy.version}"
        )
        self.profile_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self._initialize()

    @staticmethod
    def _positive_int(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        value = int(raw) if raw else default
        if value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    def _initialize(self) -> None:
        with self.repository.connect() as connection:
            connection.execute("UPDATE embedding_profiles SET active = 0 WHERE active = 1")
            connection.execute(
                """
                INSERT INTO embedding_profiles(
                    profile_id, provider, model, dimensions, vector_format,
                    policy_version, created_at, active
                ) VALUES (?, ?, ?, ?, 'f32', ?, ?, 1)
                ON CONFLICT(profile_id) DO UPDATE SET active = 1
                """,
                (
                    self.profile_id,
                    self.provider.name,
                    self.model,
                    self.dimensions,
                    self.policy.version,
                    int(time.time()),
                ),
            )
            connection.commit()

    def observe(self, signal: str, nodes: Sequence[dict[str, Any]]) -> None:
        self.repository.record_embedding_targets(
            [str(node["id"]) for node in nodes if node.get("id")],
            reason=signal,
        )

    def _documents(self) -> tuple[list[EmbeddingDocument], int]:
        with self.repository.connect() as connection:
            targets = connection.execute(
                "SELECT node_id, priority FROM embedding_targets "
                "ORDER BY priority DESC, usage_count DESC, requested_at, node_id"
            ).fetchall()
            current = {
                str(row["node_id"]): str(row["content_hash"])
                for row in connection.execute(
                    "SELECT node_id, content_hash FROM node_embeddings "
                    "WHERE profile_id = ? AND vector IS NOT NULL",
                    (self.profile_id,),
                )
            }
        target_ids = [str(row["node_id"]) for row in targets]
        nodes: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(target_ids), 200):
            nodes.update(
                (str(node["id"]), node)
                for node in self.repository.get_context_nodes(target_ids[offset : offset + 200])
            )
        orphan_ids = [node_id for node_id in target_ids if node_id not in nodes]
        if orphan_ids:
            with self.repository.connect() as connection:
                connection.executemany(
                    "DELETE FROM embedding_targets WHERE node_id = ?",
                    ((node_id,) for node_id in orphan_ids),
                )
                connection.executemany(
                    "DELETE FROM node_embeddings WHERE node_id = ?",
                    ((node_id,) for node_id in orphan_ids),
                )
                connection.commit()
        pending: list[EmbeddingDocument] = []
        for row in targets:
            node_id = str(row["node_id"])
            node = nodes.get(node_id)
            if node is None:
                continue
            text = self.policy.render(node)
            if not text:
                continue
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if current.get(node_id) == content_hash:
                continue
            pending.append(
                EmbeddingDocument(
                    node_id=node_id,
                    text=text,
                    content_hash=content_hash,
                    source_updated_at=int(node["updatedAt"]),
                    priority=int(row["priority"]),
                )
            )
        return pending, len(target_ids) - len(orphan_ids)

    def run(self, *, limit: int = 32) -> dict[str, Any]:
        if limit < 1 or limit > 256:
            raise ValueError("embedding limit must be between 1 and 256")
        pending, target_count = self._documents()
        selected = pending[:limit]
        if selected:
            try:
                vectors = self.provider.embed(
                    [document.text for document in selected],
                    model=self.model,
                    dimensions=self.dimensions,
                )
                if len(vectors) != len(selected) or any(
                    len(vector) != self.dimensions
                    or any(not math.isfinite(float(value)) for value in vector)
                    for vector in vectors
                ):
                    raise RuntimeError("embedding provider returned invalid vectors")
                self._store(selected, vectors)
            except Exception as exc:
                self._record_failure(selected, str(exc))
                raise
        evicted = self._enforce_budget()
        current_status = self.status()
        return {
            **current_status,
            "processed": len(selected),
            "remaining": current_status["pending"],
            "targets": target_count,
            "evicted": evicted,
        }

    def _store(
        self, documents: Sequence[EmbeddingDocument], vectors: Sequence[Sequence[float]]
    ) -> None:
        now = int(time.time())
        rows = [
            (
                self.profile_id,
                document.node_id,
                document.content_hash,
                document.source_updated_at,
                struct.pack(f"<{len(vector)}f", *vector),
                now,
            )
            for document, vector in zip(documents, vectors, strict=True)
        ]
        with self.repository.connect() as connection:
            connection.executemany(
                """
                INSERT INTO node_embeddings(
                    profile_id, node_id, content_hash, source_updated_at, vector,
                    embedded_at, attempts, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, 1, NULL)
                ON CONFLICT(profile_id, node_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    source_updated_at = excluded.source_updated_at,
                    vector = excluded.vector,
                    embedded_at = excluded.embedded_at,
                    attempts = node_embeddings.attempts + 1,
                    last_error = NULL
                """,
                rows,
            )
            connection.commit()

    def _record_failure(self, documents: Sequence[EmbeddingDocument], error: str) -> None:
        rows = [
            (
                self.profile_id,
                item.node_id,
                item.content_hash,
                item.source_updated_at,
                error[:1_024],
            )
            for item in documents
        ]
        with self.repository.connect() as connection:
            connection.executemany(
                """
                INSERT INTO node_embeddings(
                    profile_id, node_id, content_hash, source_updated_at, vector,
                    embedded_at, attempts, last_error
                ) VALUES (?, ?, ?, ?, NULL, NULL, 1, ?)
                ON CONFLICT(profile_id, node_id) DO UPDATE SET
                    attempts = node_embeddings.attempts + 1,
                    last_error = excluded.last_error
                """,
                rows,
            )
            connection.commit()

    def _enforce_budget(self) -> int:
        with self.repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT embedding.profile_id, embedding.node_id, LENGTH(embedding.vector) AS bytes
                FROM node_embeddings embedding
                JOIN embedding_profiles profile ON profile.profile_id = embedding.profile_id
                LEFT JOIN embedding_targets target ON target.node_id = embedding.node_id
                WHERE embedding.vector IS NOT NULL
                ORDER BY profile.active ASC, COALESCE(target.priority, 0),
                         COALESCE(target.requested_at, 0), embedding.embedded_at
                """
            ).fetchall()
            total = sum(int(row["bytes"] or 0) for row in rows)
            evicted: list[tuple[str, str]] = []
            for row in rows:
                if total <= self.max_bytes:
                    break
                total -= int(row["bytes"] or 0)
                evicted.append((str(row["profile_id"]), str(row["node_id"])))
            connection.executemany(
                "DELETE FROM node_embeddings WHERE profile_id = ? AND node_id = ?",
                evicted,
            )
            connection.commit()
        return len(evicted)

    def status(self) -> dict[str, Any]:
        pending, targets = self._documents()
        with self.repository.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count, COALESCE(SUM(LENGTH(vector)), 0) AS bytes
                FROM node_embeddings WHERE profile_id = ? AND vector IS NOT NULL
                """,
                (self.profile_id,),
            ).fetchone()
        return {
            "profileId": self.profile_id,
            "provider": self.provider.name,
            "model": self.model,
            "dimensions": self.dimensions,
            "targets": targets,
            "embedded": int(row["count"]),
            "pending": len(pending),
            "bytes": int(row["bytes"]),
            "maxBytes": self.max_bytes,
        }
