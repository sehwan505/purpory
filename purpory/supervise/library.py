"""Shared domain service used by both CLI and HTTP adapters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from purpory.supervise.bridge import seed_from_graph
from purpory.supervise.freshness import DEFAULT_STALE_DAYS, is_stale
from purpory.supervise.identity import resolve_project_id, resolve_project_root
from purpory.supervise.repository import ContextGraphRepository, memory_category, value_hash
from purpory.supervise.recall import cue, recall_summary
from purpory.supervise.resolve import resolve_topic

if TYPE_CHECKING:
    from purpory.supervise.gate.provider import GateProvider
    from purpory.supervise.provisioning import ContextProvisioningService


def current_session_id(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    for name in ("PURPORY_SESSION", "CODEX_THREAD_ID", "CLAUDE_SESSION_ID"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return "anon"


class ContextService:
    """Application boundary for deterministic context-graph operations."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        root: str | Path | None = None,
        graph_path: str | Path | None = None,
        stale_after_days: int = DEFAULT_STALE_DAYS,
        gate_provider: "GateProvider | None" = None,
    ) -> None:
        self.repository = ContextGraphRepository(db_path)
        self.root = resolve_project_root(root or Path.cwd())
        self.project_id = resolve_project_id(self.root)
        output_directory = os.environ.get("PURPORY_OUT", "purpory-out")
        default_graph = self.root / output_directory / "graph.json"
        self.graph_path = (
            Path(graph_path).expanduser().resolve() if graph_path is not None else default_graph
        )
        self.stale_after_days = stale_after_days
        self.gate_provider = gate_provider

    def _selected_project(self, project: str | None = None) -> str:
        return resolve_project_id(self.root, project) if project else self.project_id

    def _ensure_graph_imported(self, project: str | None = None) -> bool:
        if not self.graph_path.is_file():
            return False
        self.repository.import_graph(
            self.graph_path,
            project=self._selected_project(project),
        )
        return True

    def sync_graph(self) -> dict[str, Any]:
        """Synchronize the current structural artifact into canonical storage."""
        if not self.graph_path.is_file():
            return {
                "imported": False,
                "missing": True,
                "project": self.project_id,
                "nodes": 0,
                "edges": 0,
            }
        return {
            **self.repository.import_graph(self.graph_path, project=self.project_id),
            "missing": False,
        }

    def _provisioner(self, project: str | None = None) -> "ContextProvisioningService":
        from purpory.supervise.provisioning import ContextProvisioningService

        selected_project = self._selected_project(project)
        return ContextProvisioningService(
            repository=self.repository,
            root=self.root,
            graph_project=selected_project,
            project=selected_project,
            stale_after_days=self.stale_after_days,
        )

    def view(self, *, session_id: str | None = None, since: int | None = None) -> dict[str, Any]:
        self._ensure_graph_imported()
        return {
            "project": self.project_id,
            "topics": self.repository.topic_view(
                project=self.project_id, stale_after_days=self.stale_after_days
            ),
            "sessions": self.repository.session_view(session_id=session_id, since=since),
            "diagnostics": self.repository.diagnostics(),
        }

    def topic(self, key: str) -> dict[str, Any]:
        topic = self.repository.get_topic(key, project=self.project_id)
        if topic is None:
            raise KeyError(f"topic not found: {key}")
        if topic.get("kind") == "code-area":
            self._ensure_graph_imported()
        resolved = resolve_topic(
            topic,
            root=self.root,
            repository=self.repository,
            project=self.project_id,
        )
        return {
            **topic,
            "category": memory_category(str(topic["kind"])),
            "stale": is_stale(topic["set_at"], stale_after_days=self.stale_after_days),
            "versions": self.repository.list_memory_versions(
                key,
                project=str(topic["project"]),
            ),
            "needsReviews": self.repository.list_needs_reviews(
                project=self.project_id,
                key=key,
            ),
            **resolved,
        }

    def set_topic(
        self,
        key: str,
        *,
        value: str | None = None,
        source: str | None = None,
        kind: str = "note",
    ) -> dict[str, Any]:
        current = self.repository.get_topic(key, project=self.project_id)
        expected_hash = (
            current["hash"]
            if current is not None and current.get("project") == self.project_id
            else None
        )
        result = self.repository.reconcile_topics(
            [
                {
                    "key": key,
                    "value": value,
                    "source": source,
                    "kind": kind,
                    "expectedHash": expected_hash,
                }
            ],
            project=self.project_id,
            apply=True,
            session_id=current_session_id(),
        )
        change = result["changes"][0]
        return {
            "key": key,
            "action": change["action"],
            "versionId": change.get("versionId"),
        }

    def list_topics(self, *, prefix: str | None = None) -> list[dict[str, Any]]:
        prefixes = [prefix] if prefix else None
        return self.repository.list_topics(prefixes, project=self.project_id)

    def reconcile_topics(
        self,
        changes: Sequence[dict[str, Any]],
        *,
        apply: bool = False,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return self.repository.reconcile_topics(
            changes,
            project=self.project_id,
            apply=apply,
            session_id=current_session_id(session_id),
        )

    def delete_topic(self, key: str) -> bool:
        return self.repository.delete_topic(key, project=self.project_id)

    def confirm_topic(self, key: str) -> bool:
        return self.repository.confirm_topic(key, project=self.project_id)

    def memory_versions(self, key: str) -> list[dict[str, Any]]:
        topic = self.repository.get_topic(key, project=self.project_id)
        if topic is None:
            raise KeyError(f"topic not found: {key}")
        return self.repository.list_memory_versions(key, project=str(topic["project"]))

    def create_needs_review(
        self,
        key: str,
        *,
        source_type: str,
        source_id: str,
        evidence: str,
        reason: str,
    ) -> dict[str, Any]:
        if not evidence:
            raise ValueError("evidence cannot be empty")
        return self.repository.create_needs_review(
            key,
            project=self.project_id,
            source_type=source_type,
            source_id=source_id,
            content_hash=value_hash(evidence),
            reason=reason,
        )

    def needs_reviews(
        self,
        *,
        status: str | None = None,
        key: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.repository.list_needs_reviews(
            project=self.project_id,
            status=status,
            key=key,
        )

    def resolve_needs_review(
        self,
        review_id: int,
        *,
        outcome: str,
        change: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        version_id: int | None = None
        if outcome == "change":
            if not isinstance(change, dict):
                raise ValueError("change outcome requires a memory change")
            reviews = [
                item
                for item in self.repository.list_needs_reviews(project=self.project_id)
                if item["id"] == int(review_id) and item["status"] == "open"
            ]
            if not reviews:
                return None
            review = reviews[0]
            key = str(change.get("key", review["key"]))
            if key != review["key"]:
                raise ValueError("needs-review change must keep the reviewed key")
            current = self.repository.get_topic(key, project=self.project_id)
            if current is None:
                raise KeyError(f"topic not found: {key}")
            reconciled = self.repository.reconcile_topics(
                [
                    {
                        "key": key,
                        "kind": str(change.get("kind", current["kind"])),
                        "value": change.get("value"),
                        "source": change.get("source"),
                        "expectedHash": current["hash"],
                    }
                ],
                project=self.project_id,
                apply=True,
                session_id=current_session_id(),
            )
            applied = reconciled["changes"][0]
            if applied["action"] not in {"created", "updated"}:
                raise ValueError("needs-review change did not create a new memory version")
            version_id = int(applied["versionId"])
        return self.repository.resolve_needs_review(
            review_id,
            outcome=outcome,
            result_version_id=version_id,
        )

    def propose_global_memory(
        self,
        key: str,
        *,
        value: str | None,
        source: str | None,
        kind: str,
        rationale: str,
    ) -> dict[str, Any]:
        return self.repository.create_global_memory_request(
            key,
            value=value,
            source=source,
            kind=kind,
            rationale=rationale,
            requested_from_project=self.project_id,
        )

    def global_memory_requests(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_global_memory_requests(status)

    def edit_global_memory_request(
        self,
        request_id: int,
        *,
        key: str,
        value: str | None,
        source: str | None,
        kind: str,
        rationale: str,
    ) -> dict[str, Any] | None:
        return self.repository.update_global_memory_request(
            request_id,
            key=key,
            value=value,
            source=source,
            kind=kind,
            rationale=rationale,
        )

    def decide_global_memory_request(
        self,
        request_id: int,
        *,
        decision: str,
    ) -> dict[str, Any] | None:
        return self.repository.decide_global_memory_request(
            request_id,
            decision=decision,
        )

    def project_memory_report(self, *, since: int | None = None) -> list[dict[str, Any]]:
        return self.repository.project_memory_report(project=self.project_id, since=since)

    def seed(
        self,
        graph_path: str | Path | None = None,
        *,
        labels_path: str | Path | None = None,
        per_community: int = 3,
        prune: bool = True,
    ) -> dict[str, Any]:
        path = Path(graph_path).expanduser().resolve() if graph_path else self.graph_path
        return seed_from_graph(
            self.repository,
            path,
            project=self.project_id,
            labels_path=labels_path,
            per_community=per_community,
            prune=prune,
        )

    def recall(self, *, session_id: str | None = None) -> dict[str, Any]:
        return recall_summary(self.repository, session_id=current_session_id(session_id))

    def cue(
        self,
        paths: list[str | Path],
        *,
        session_id: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        return cue(
            self.repository,
            paths,
            session_id=current_session_id(session_id),
            project=self._selected_project(project),
        )

    def create_request(
        self,
        need: str,
        *,
        session_id: str | None = None,
        project: str | None = None,
    ) -> int:
        return self.repository.create_request(
            current_session_id(session_id),
            need,
            project=self._selected_project(project),
        )

    def requests(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_requests(status)

    def resolve_request(self, request_id: int, key: str) -> bool:
        return self.repository.resolve_request(request_id, key)

    def graph(
        self,
        *,
        scope: str | None = None,
        node_limit: int = 200,
        edge_limit: int = 500,
    ) -> dict[str, Any]:
        if not self._ensure_graph_imported():
            return {
                "nodes": [],
                "links": [],
                "totalNodes": 0,
                "totalLinks": 0,
                "truncated": False,
            }
        return self.repository.graph_payload(
            project=self.project_id,
            scope=scope,
            node_limit=node_limit,
            edge_limit=edge_limit,
        )

    def catalog(
        self,
        *,
        session_id: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_graph_imported(project)
        return self._provisioner(project).catalog(session_id=current_session_id(session_id))

    def search(
        self,
        query: str,
        *,
        session_id: str | None = None,
        project: str | None = None,
        scopes: Sequence[str] = (),
        keywords: Sequence[str] = (),
        active_paths: Sequence[str | Path] = (),
        limit: int = 12,
        connect: bool = True,
    ) -> dict[str, Any]:
        self._ensure_graph_imported(project)
        return self._provisioner(project).search(
            query,
            session_id=current_session_id(session_id),
            scopes=scopes,
            keywords=keywords,
            active_paths=active_paths,
            limit=limit,
            connect=connect,
        )

    def expand(
        self,
        node_ids: Sequence[str],
        *,
        project: str | None = None,
        depth: int = 1,
        relations: Sequence[str] = (),
        node_limit: int = 100,
        include_experiential: bool = False,
    ) -> dict[str, Any]:
        self._ensure_graph_imported(project)
        return self._provisioner(project).expand(
            node_ids,
            depth=depth,
            relations=relations,
            node_limit=node_limit,
            include_experiential=include_experiential,
        )

    def context_path(
        self,
        source_id: str,
        target_id: str,
        *,
        project: str | None = None,
        max_depth: int = 4,
        relations: Sequence[str] = (),
        include_experiential: bool = False,
    ) -> dict[str, Any]:
        self._ensure_graph_imported(project)
        return self._provisioner(project).path(
            source_id,
            target_id,
            max_depth=max_depth,
            relations=relations,
            include_experiential=include_experiential,
        )

    def deliver(
        self,
        node_ids: Sequence[str],
        *,
        session_id: str | None = None,
        project: str | None = None,
        token_budget: int = 2_000,
    ) -> dict[str, Any]:
        self._ensure_graph_imported(project)
        return self._provisioner(project).deliver(
            node_ids,
            session_id=current_session_id(session_id),
            token_budget=token_budget,
        )

    def prepare(
        self,
        message: str,
        *,
        session_id: str | None = None,
        project: str | None = None,
        working_directory: str | Path | None = None,
        active_paths: Sequence[str | Path] = (),
        token_budget: int = 2_000,
        retain_input: bool = False,
    ) -> dict[str, Any]:
        from purpory.supervise.gate.qwen import QwenGateProvider
        from purpory.supervise.gate.service import GatewayService

        provider = self.gate_provider
        if provider is None and os.environ.get("PURPORY_GATE_URL", "").strip():
            provider = QwenGateProvider.from_environment()
        session = current_session_id(session_id)
        selected_project = self._selected_project(project)
        self._ensure_graph_imported(selected_project)
        gateway = GatewayService(
            repository=self.repository,
            root=self.root,
            graph_project=selected_project,
            provider=provider,
        )
        return gateway.prepare(
            message=message,
            session_id=session,
            project=selected_project,
            working_directory=working_directory or self.root,
            active_paths=active_paths,
            token_budget=token_budget,
            retain_input=retain_input,
        )

    def context_decisions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_gate_decisions(limit=limit)

    def model_status(self) -> dict[str, Any]:
        from purpory.supervise.gate.provider import UnavailableGateProvider
        from purpory.supervise.gate.runtime import GateModelManager

        managed = GateModelManager().status()
        provider = self.gate_provider
        environment_url = os.environ.get("PURPORY_GATE_URL", "").strip()
        provider_endpoint = getattr(provider, "endpoint", None)
        managed_endpoint = managed.get("endpoint")
        if isinstance(provider, UnavailableGateProvider):
            source = "unavailable"
        elif provider is not None:
            source = (
                "managed"
                if managed_endpoint
                and isinstance(provider_endpoint, str)
                and provider_endpoint.startswith(str(managed_endpoint))
                else "explicit"
            )
        elif environment_url:
            source = "environment"
        else:
            source = "none"
        return {
            **managed,
            "providerConfigured": provider is not None or bool(environment_url),
            "providerSource": source,
            "providerModel": getattr(provider, "model", None),
        }

    def context_feedback(
        self,
        decision_id: int,
        *,
        verdict: str,
        expected_action: str | None = None,
        expected_keys: Sequence[str] = (),
        note: str | None = None,
    ) -> dict[str, Any]:
        return self.repository.record_gate_feedback(
            decision_id,
            verdict=verdict,
            expected_action=expected_action,
            expected_keys=expected_keys,
            note=note,
        )
