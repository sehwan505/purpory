"""Shared domain service used by both CLI and HTTP adapters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from purpory.supervise.bridge import seed_from_graph
from purpory.supervise.freshness import DEFAULT_STALE_DAYS, is_stale
from purpory.supervise.repository import ContextGraphRepository
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
        self.root = Path(root or Path.cwd()).expanduser().resolve()
        configured_project = os.environ.get("PURPORY_PROJECT_ID", "").strip()
        self.project_id = configured_project or str(self.root)
        output_directory = os.environ.get("PURPORY_OUT", "purpory-out")
        default_graph = self.root / output_directory / "graph.json"
        self.graph_path = (
            Path(graph_path).expanduser().resolve() if graph_path is not None else default_graph
        )
        self.stale_after_days = stale_after_days
        self.gate_provider = gate_provider

    def _ensure_graph_imported(self) -> bool:
        if not self.graph_path.is_file():
            return False
        self.repository.import_graph(self.graph_path, project=self.project_id)
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

        return ContextProvisioningService(
            repository=self.repository,
            root=self.root,
            graph_project=self.project_id,
            project=project or self.root.name,
            stale_after_days=self.stale_after_days,
        )

    def view(self, *, session_id: str | None = None, since: int | None = None) -> dict[str, Any]:
        self._ensure_graph_imported()
        return {
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
            "stale": is_stale(topic["set_at"], stale_after_days=self.stale_after_days),
            **resolved,
        }

    def set_topic(
        self,
        key: str,
        *,
        value: str | None = None,
        source: str | None = None,
        kind: str = "note",
    ) -> dict[str, str]:
        return {
            "key": key,
            "action": self.repository.set_topic(
                key, value=value, source=source, kind=kind, origin="human"
            ),
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
        return self.repository.delete_topic(key)

    def confirm_topic(self, key: str) -> bool:
        return self.repository.confirm_topic(key)

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
            project=project or self.root.name,
        )

    def create_request(
        self,
        need: str,
        *,
        session_id: str | None = None,
        project: str | None = None,
    ) -> int:
        return self.repository.create_request(
            current_session_id(session_id), need, project=project or self.root.name
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
        self._ensure_graph_imported()
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
        self._ensure_graph_imported()
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
        self._ensure_graph_imported()
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
        self._ensure_graph_imported()
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
        self._ensure_graph_imported()
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
        selected_project = project or self.root.name
        self._ensure_graph_imported()
        gateway = GatewayService(
            repository=self.repository,
            root=self.root,
            graph_project=self.project_id,
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
