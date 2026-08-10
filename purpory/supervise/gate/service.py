"""Gateway orchestration: model proposal, deterministic evidence, and delivery."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Sequence

from purpory.supervise.gate.contract import (
    PROMPT_VERSION,
    GateDecision,
    GateProposal,
    GateRequest,
    MAX_NAMESPACES,
    MAX_QUERY_CHARS,
)
from purpory.supervise.gate.provider import GateProvider, GateProviderError
from purpory.supervise.provisioning import ContextProvisioningService
from purpory.supervise.repository import ContextGraphRepository


def _fallback_proposal(request: GateRequest) -> GateProposal:
    normalized = request.message.strip().lower().rstrip("!?. ")
    greetings = {"hi", "hello", "hey", "안녕", "안녕하세요", "반가워"}
    if normalized in greetings:
        return GateProposal.from_mapping(
            {
                "action": "skip",
                "query": None,
                "scopes": [],
                "keywords": [],
                "reasonCode": "SELF_CONTAINED",
                "clarification": None,
            }
        )
    if len(request.message) > MAX_QUERY_CHARS:
        return GateProposal.from_mapping(
            {
                "action": "skip",
                "query": None,
                "scopes": [],
                "keywords": [],
                "reasonCode": "GATE_UNAVAILABLE",
                "clarification": None,
            }
        )
    return GateProposal.from_mapping(
        {
            "action": "search",
            "query": request.message,
            "scopes": ["human", "resource", "material", "session"],
            "keywords": [],
            "reasonCode": "GATE_UNAVAILABLE",
            "clarification": None,
        }
    )


class GatewayService:
    def __init__(
        self,
        *,
        repository: ContextGraphRepository,
        root: str | Path,
        provider: GateProvider | None,
        graph_project: str | None = None,
        graph_projects: Sequence[str] = (),
        resource_node_ids: Sequence[str] = (),
        selected_views: Sequence[dict[str, Any]] = (),
    ) -> None:
        self.repository = repository
        self.root = Path(root).expanduser().resolve()
        self.graph_project = graph_project
        self.graph_projects = tuple(graph_projects)
        self.resource_node_ids = tuple(resource_node_ids)
        self.selected_views = tuple(selected_views)
        self.provider = provider

    def prepare(
        self,
        *,
        message: str,
        session_id: str,
        project: str,
        working_directory: str | Path,
        active_paths: Sequence[str | Path] = (),
        token_budget: int = 2_000,
        retain_input: bool = False,
    ) -> dict[str, Any]:
        provisioner = ContextProvisioningService(
            repository=self.repository,
            root=self.root,
            graph_project=self.graph_project or project,
            graph_projects=self.graph_projects,
            project=project,
            resource_node_ids=self.resource_node_ids,
            selected_views=self.selected_views,
        )
        previous = self.repository.session_topic_keys(
            session_id, project=project
        )[:1_000]
        catalog = provisioner.catalog(session_id=session_id)
        namespaces = [str(item["name"]) for item in catalog["topicNamespaces"]][:MAX_NAMESPACES]
        request = GateRequest.create(
            message=message,
            session_id=session_id,
            project=project,
            working_directory=str(Path(working_directory).expanduser().resolve()),
            active_paths=[str(Path(path)) for path in active_paths],
            previous_deliveries=previous,
            available_namespaces=namespaces,
            token_budget=token_budget,
            context_catalog=catalog,
        )

        provider_result = None
        fallback_reason = None
        if self.provider is not None:
            limit_check = getattr(self.provider, "input_limit_reason", None)
            try:
                limit_reason = limit_check(request) if callable(limit_check) else None
            except (GateProviderError, OSError, ValueError) as exc:
                limit_reason = f"gate input validation failed: {exc}"
            if limit_reason is None:
                try:
                    provider_result = self.provider.propose(request)
                except (GateProviderError, OSError, ValueError) as exc:
                    fallback_reason = str(exc)
            else:
                fallback_reason = f"{limit_reason}; model invocation skipped"
        else:
            fallback_reason = "gate provider is not configured"
        proposal = provider_result.proposal if provider_result else _fallback_proposal(request)

        delivery: list[dict[str, Any]] = []
        omitted: list[dict[str, Any]] = []
        request_id = None
        clarification = proposal.clarification
        search_result: dict[str, Any] | None = None
        delivery_result: dict[str, Any] = {
            "delivery": [],
            "omitted": [],
            "rendered": "",
            "estimatedTokens": 0,
            "valueHash": None,
            "remainingTokens": token_budget,
        }
        if proposal.action == "skip":
            final_action = "skip"
        elif proposal.action == "ask":
            final_action = "ask"
        else:
            search_result = provisioner.search(
                proposal.query or message,
                session_id=session_id,
                scopes=proposal.scopes,
                keywords=proposal.keywords,
                active_paths=active_paths,
                previous_deliveries=previous,
            )
            if search_result["candidates"]:
                delivery_candidates = list(search_result["candidates"])
                seen_ids = {item["nodeId"] for item in delivery_candidates}
                previously_delivered_ids = self.repository.session_delivered_node_ids(
                    session_id, project=project
                )
                graph_candidates = [
                    (
                        node,
                        ["graph-bridge"],
                    )
                    for connection in search_result["connections"]
                    for node in connection["nodes"]
                ] + [
                    (
                        lead["node"],
                        [
                            "graph-lead",
                            f"relation:{lead['via']['relation']}",
                        ],
                    )
                    for lead in search_result["exploration"]["frontier"]
                ]
                for node, signals in graph_candidates:
                    node_id = str(node["id"])
                    if node_id in seen_ids or node_id in previously_delivered_ids:
                        continue
                    delivery_candidates.append(
                        {
                            "nodeId": node_id,
                            "score": None,
                            "signals": signals,
                        }
                    )
                    seen_ids.add(node_id)
                    if len(delivery_candidates) >= 32:
                        break
                delivery_result = provisioner.deliver(
                    [candidate["nodeId"] for candidate in delivery_candidates],
                    session_id=session_id,
                    token_budget=token_budget,
                    candidates=delivery_candidates,
                )
            delivery = delivery_result["delivery"]
            omitted = delivery_result["omitted"]
            if delivery:
                final_action = "retrieve"
            elif any(item.get("reason") == "already-delivered" for item in omitted):
                final_action = "skip"
            elif proposal.reason_code == "GATE_UNAVAILABLE":
                # The prompt hook must remain useful before the optional model
                # is installed. Retrieval and auditing still run, but a fallback
                # miss is not enough evidence to manufacture a clarification.
                final_action = "skip"
            else:
                final_action = "ask"
                clarification = (
                    "Purpory에서 충분한 근거를 찾지 못했습니다. "
                    "필요한 결정이나 프로젝트 정보를 알려주세요."
                )
                request_id = self.repository.ensure_request(
                    session_id,
                    proposal.query or message,
                    project=project,
                )

        if final_action == "ask" and request_id is None:
            request_id = self.repository.ensure_request(
                session_id,
                proposal.query or message,
                project=project,
            )
        if final_action != "ask":
            clarification = None

        decision = GateDecision(
            action=final_action,
            proposal=proposal,
            delivery=tuple(delivery),
            omitted=tuple(omitted),
            request_id=request_id,
            clarification=clarification,
            model_id=provider_result.model_id if provider_result else None,
            model_revision=provider_result.model_revision if provider_result else None,
            latency_ms=provider_result.latency_ms if provider_result else None,
            fallback_reason=fallback_reason,
        )
        decision_payload = decision.as_dict()
        decision_id = self.repository.record_gate_decision(
            session_id=session_id,
            project=project,
            input_hash=hashlib.sha256(message.encode("utf-8")).hexdigest(),
            input_text=message if retain_input else None,
            proposal=proposal.as_dict(),
            final_action=final_action,
            delivery=delivery,
            request_id=request_id,
            model_id=decision.model_id,
            model_revision=decision.model_revision,
            prompt_version=PROMPT_VERSION,
            latency_ms=decision.latency_ms,
            fallback_reason=fallback_reason,
        )
        return {
            **decision_payload,
            "decisionId": decision_id,
            "context": {
                "manifest": catalog,
                "search": search_result,
                "rendered": delivery_result["rendered"],
                "estimatedTokens": delivery_result["estimatedTokens"],
                "valueHash": delivery_result["valueHash"],
            },
        }
