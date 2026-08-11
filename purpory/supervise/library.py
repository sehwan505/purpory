"""Shared domain service used by both CLI and HTTP adapters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from purpory.supervise.bridge import seed_from_graph
from purpory.supervise.freshness import DEFAULT_STALE_DAYS, is_stale
from purpory.supervise.identity import resolve_project_id, resolve_project_root
from purpory.supervise.repository import ContextGraphRepository, memory_category, value_hash
from purpory.supervise.resources import discover_git_resource, discover_git_worktree
from purpory.supervise.recall import cue, recall_summary
from purpory.supervise.resolve import resolve_topic

if TYPE_CHECKING:
    from purpory.supervise.gate.provider import GateProvider
    from purpory.supervise.provisioning import ContextProvisioningService


def current_session_id(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    for name, prefix in (
        ("PURPORY_SESSION", ""),
        ("CODEX_THREAD_ID", "codex:"),
        ("CLAUDE_SESSION_ID", "claude:"),
    ):
        value = os.environ.get(name)
        if value and value.strip():
            normalized = value.strip()
            return normalized if not prefix or normalized.startswith(prefix) else prefix + normalized
    return "anon"


class ContextService:
    """Application boundary for deterministic context-graph operations."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        root: str | Path | None = None,
        project_id: str | None = None,
        graph_path: str | Path | None = None,
        stale_after_days: int = DEFAULT_STALE_DAYS,
        gate_provider: "GateProvider | None" = None,
    ) -> None:
        self.repository = ContextGraphRepository(db_path)
        self.root = resolve_project_root(root or Path.cwd())
        self._explicit_project_id = project_id.strip() if project_id and project_id.strip() else None
        self._default_project_id = resolve_project_id(self.root, project_id)
        output_directory = os.environ.get("PURPORY_OUT", "purpory-out")
        self.output_directory = output_directory
        self._graph_path_explicit = graph_path is not None
        default_graph = self.root / output_directory / "graph.json"
        self.graph_path = (
            Path(graph_path).expanduser().resolve() if graph_path is not None else default_graph
        )
        self.stale_after_days = stale_after_days
        self.gate_provider = gate_provider

    @property
    def project_id(self) -> str:
        return str(self._context_selection(refresh_git=True)["project"])

    def _selected_project(self, project: str | None = None) -> str:
        return project.strip() if project and project.strip() else self.project_id

    def _context_selection(
        self,
        *,
        project: str | None = None,
        working_directory: str | Path | None = None,
        refresh_git: bool = False,
    ) -> dict[str, Any]:
        directory = Path(working_directory or self.root).expanduser().resolve()
        binding = self.repository.resolve_resource_view(directory)
        if binding is None and refresh_git:
            try:
                discovered = discover_git_worktree(directory)
            except ValueError:
                discovered = None
            if discovered is not None:
                resource = self.repository.resource_by_identity(
                    provider="git",
                    external_identity=str(discovered["externalIdentity"]),
                )
                if resource is not None:
                    self.repository.attach_resource(
                        str(resource["namespaceId"]),
                        provider="git",
                        resource_kind=str(discovered["resourceKind"]),
                        external_identity=str(discovered["externalIdentity"]),
                        label=str(discovered["resourceLabel"]),
                        properties=discovered["resourceProperties"],
                        views=[discovered["view"]],
                    )
                    binding = self.repository.resolve_resource_view(directory)
        selected_project = (
            project.strip() if project and project.strip() else self._explicit_project_id
        )
        active_binding = (
            binding
            if binding is not None
            and (selected_project is None or selected_project == binding["namespaceId"])
            else None
        )
        fallback = (
            str(active_binding["namespaceId"])
            if active_binding is not None
            else selected_project or self._default_project_id
        )
        resources = self.repository.project_resource_selection(
            fallback,
            active_view_id=(str(active_binding["viewId"]) if active_binding else None),
        )
        graph_projects = list(
            dict.fromkeys(
                resolve_project_id(str(view["locator"]))
                for resource in resources["resources"]
                if isinstance((view := resource.get("selectedView")), dict)
            )
        )
        if not graph_projects:
            graph_projects = [fallback]
        primary_graph = (
            resolve_project_id(str(active_binding["locator"]))
            if active_binding is not None
            else str(graph_projects[0])
        )
        return {
            "project": fallback,
            "graphProject": primary_graph,
            "graphProjects": graph_projects,
            "resourceNodeIds": list(resources["nodeIds"]),
            "resources": list(resources["resources"]),
            "root": (
                Path(str(active_binding["locator"])).resolve()
                if active_binding is not None
                else self.root
            ),
            "binding": active_binding,
        }

    def _selection_graph_path(self, selection: dict[str, Any]) -> Path:
        if self._graph_path_explicit:
            return self.graph_path
        return Path(selection["root"]) / self.output_directory / "graph.json"

    def _selection_graph_paths(self, selection: dict[str, Any]) -> list[tuple[str, Path]]:
        if self._graph_path_explicit:
            return [(str(selection["graphProject"]), self.graph_path)]
        paths: list[tuple[str, Path]] = []
        for resource in selection.get("resources", []):
            view = resource.get("selectedView")
            if not isinstance(view, dict):
                continue
            paths.append(
                (
                    resolve_project_id(str(view["locator"])),
                    Path(str(view["locator"])) / self.output_directory / "graph.json",
                )
            )
        if not paths:
            paths.append(
                (str(selection["graphProject"]), self._selection_graph_path(selection))
            )
        return paths

    def sync_graph(self) -> dict[str, Any]:
        """Explicitly import selected graph artifacts into canonical storage."""
        selection = self._context_selection(refresh_git=True)
        graph_paths = self._selection_graph_paths(selection)
        available = [(view_id, path) for view_id, path in graph_paths if path.is_file()]
        if not available:
            return {
                "imported": False,
                "missing": True,
                "project": selection["project"],
                "graphProject": selection["graphProject"],
                "graphProjects": selection["graphProjects"],
                "nodes": 0,
                "edges": 0,
            }
        results = [
            self.repository.import_graph(path, project=view_id)
            for view_id, path in available
        ]
        primary = next(
            (
                result
                for result in results
                if result["project"] == str(selection["graphProject"])
            ),
            results[0],
        )
        return {
            **primary,
            "namespace": selection["project"],
            "graphProjects": list(selection["graphProjects"]),
            "graphs": results,
            "missing": False,
        }

    def _provisioner(
        self,
        project: str | None = None,
        *,
        working_directory: str | Path | None = None,
    ) -> "ContextProvisioningService":
        from purpory.supervise.provisioning import ContextProvisioningService

        selection = self._context_selection(
            project=project,
            working_directory=working_directory,
            refresh_git=True,
        )
        return ContextProvisioningService(
            repository=self.repository,
            root=selection["root"],
            graph_project=str(selection["graphProject"]),
            graph_projects=selection["graphProjects"],
            project=str(selection["project"]),
            resource_node_ids=selection["resourceNodeIds"],
            selected_views=[
                view
                for resource in selection["resources"]
                if isinstance((view := resource.get("selectedView")), dict)
            ],
            stale_after_days=self.stale_after_days,
        )

    def projects(self) -> list[dict[str, Any]]:
        return self.repository.list_project_namespaces()

    def create_project(
        self,
        name: str,
        *,
        description: str = "",
    ) -> dict[str, Any]:
        return self.repository.create_project_namespace(name, description=description)

    def attach_git_resource(
        self,
        project_id: str,
        path: str | Path,
        *,
        alias: str | None = None,
    ) -> dict[str, Any]:
        discovered = discover_git_resource(path)
        return self.repository.attach_resource(
            project_id,
            provider=str(discovered["provider"]),
            resource_kind=str(discovered["resourceKind"]),
            external_identity=str(discovered["externalIdentity"]),
            label=str(discovered["resourceLabel"]),
            properties=discovered["resourceProperties"],
            views=discovered["views"],
            home_view_locator=discovered.get("primaryViewLocator"),
            alias=alias,
        )

    def view(self, *, session_id: str | None = None, since: int | None = None) -> dict[str, Any]:
        selection = self._context_selection()
        return {
            "project": selection["project"],
            "graphProject": selection["graphProject"],
            "graphProjects": selection["graphProjects"],
            "resourceBinding": selection["binding"],
            "resources": selection["resources"],
            "topics": self.repository.topic_view(
                project=self.project_id, stale_after_days=self.stale_after_days
            ),
            "sessions": self.repository.session_view(session_id=session_id, since=since),
            "awarenessMetrics": self.repository.awareness_metrics(
                project=str(selection["project"])
            ),
            "diagnostics": self.repository.diagnostics(),
        }

    def topic(self, key: str) -> dict[str, Any]:
        selection = self._context_selection(refresh_git=True)
        memory_project = str(selection["project"])
        topic = self.repository.get_topic(key, project=memory_project)
        if topic is None:
            raise KeyError(f"topic not found: {key}")
        resolved = resolve_topic(
            topic,
            root=selection["root"],
            repository=self.repository,
            project=str(selection["graphProject"]),
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
                project=memory_project,
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
        if change["action"] in {"created", "updated", "unchanged"}:
            self._materialize_topic_embedding(key, project=self.project_id)
        return {
            "key": key,
            "action": change["action"],
            "versionId": change.get("versionId"),
        }

    def _materialize_topic_embedding(self, key: str, *, project: str) -> None:
        from purpory.supervise.embeddings import EmbeddingService

        topic = self.repository.get_topic(key, project=project)
        if topic is None:
            return
        try:
            EmbeddingService(self.repository).run(limit=1, node_ids=[str(topic["id"])])
        except (OSError, RuntimeError, ValueError):
            pass

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
            self._materialize_topic_embedding(key, project=self.project_id)
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
        result = self.repository.decide_global_memory_request(
            request_id,
            decision=decision,
        )
        if result is not None and result.get("memoryAction") in {
            "created",
            "updated",
            "unchanged",
        }:
            proposal = result.get("proposal")
            if isinstance(proposal, dict):
                self._materialize_topic_embedding(str(proposal["key"]), project="")
        return result

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
        selection = self._context_selection(refresh_git=True)
        return self.repository.graph_payload(
            project=str(selection["graphProject"]),
            scope=scope,
            node_limit=node_limit,
            edge_limit=edge_limit,
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
        selection = self._context_selection(
            project=project,
            working_directory=working_directory,
            refresh_git=True,
        )
        selected_project = str(selection["project"])
        graph_project = str(selection["graphProject"])
        selected_root = Path(selection["root"])
        gateway = GatewayService(
            repository=self.repository,
            root=selected_root,
            graph_project=graph_project,
            graph_projects=selection["graphProjects"],
            resource_node_ids=selection["resourceNodeIds"],
            selected_views=[
                view
                for resource in selection["resources"]
                if isinstance((view := resource.get("selectedView")), dict)
            ],
            provider=provider,
        )
        return gateway.prepare(
            message=message,
            session_id=session,
            project=selected_project,
            working_directory=working_directory or selected_root,
            active_paths=active_paths,
            token_budget=token_budget,
            retain_input=retain_input,
        )

    def context_decisions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_gate_decisions(limit=limit)

    def select_model(
        self,
        model: str,
        *,
        role: str = "gate",
        provider: str = "ollama",
    ) -> dict[str, Any]:
        from purpory.supervise.gate.runtime import GateModelManager

        manager = GateModelManager()
        manager.select_model(model, role=role, provider=provider)
        if role == "gate" and self.gate_provider is not None:
            from purpory.supervise.gate.qwen import QwenGateProvider
            if isinstance(self.gate_provider, QwenGateProvider):
                self.gate_provider.model = model
        return self.model_status()

    def install_model(self, model: str) -> dict[str, Any]:
        from purpory.supervise.gate.runtime import GateModelManager

        result = GateModelManager().install(model_id=model)
        return {**self.model_status(), "action": result["action"]}

    def model_status(self) -> dict[str, Any]:
        from purpory.supervise.gate.provider import UnavailableGateProvider
        from purpory.llm.helpers import BACKENDS, _default_model_for_backend
        from purpory.supervise.gate.runtime import (
            RECONCILE_PROVIDERS,
            GateModelManager,
            configured_model,
            configured_provider,
        )

        manager = GateModelManager()
        managed = manager.status()
        reconcile = manager.role_status("reconcile")
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
            "selectedModels": {
                "gate": configured_model("gate"),
                "reconcile": configured_model("reconcile"),
            },
            "selectedProviders": {
                "gate": "ollama",
                "reconcile": configured_provider("reconcile"),
            },
            "reconcileProviders": list(RECONCILE_PROVIDERS),
            "reconcileProviderDefaults": {
                name: _default_model_for_backend(name) for name in BACKENDS
            },
            "models": {"gate": managed, "reconcile": reconcile},
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
