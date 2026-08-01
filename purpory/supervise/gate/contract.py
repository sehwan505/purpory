"""Strict request and response contracts for the context gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

GATE_SCHEMA_VERSION = 1
PROMPT_VERSION = "purpory-gate-v3"

MODEL_ACTIONS = frozenset({"skip", "search", "ask"})
FINAL_ACTIONS = frozenset({"skip", "retrieve", "ask"})
GATE_SCOPES = frozenset({"human", "resource", "code", "session"})
REASON_CODES = frozenset(
    {
        "SELF_CONTAINED",
        "CONTEXT_SEARCH_REQUIRED",
        "PRIOR_DECISION_REFERENCED",
        "PROJECT_CONTEXT_REQUIRED",
        "SESSION_HISTORY_REQUIRED",
        "CODE_CONTEXT_REQUIRED",
        "USER_INPUT_REQUIRED",
        "AMBIGUOUS_REQUEST",
        "GATE_UNAVAILABLE",
    }
)

MAX_MESSAGE_CHARS = 1_048_576
MAX_QUERY_CHARS = 4_096
MAX_PATHS = 32
MAX_PATH_CHARS = 1_024
MAX_NAMESPACES = 128
MAX_KEYWORDS = 8
MIN_TOKEN_BUDGET = 128
MAX_TOKEN_BUDGET = 32_768


def _clean_string(value: object, *, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = value.strip()
    if required and not cleaned:
        raise ValueError(f"{field} cannot be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return cleaned


def _string_tuple(
    values: Sequence[object],
    *,
    field: str,
    limit: int,
    item_maximum: int,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be an array of strings")
    if len(values) > limit:
        raise ValueError(f"{field} cannot contain more than {limit} items")
    cleaned = {
        _clean_string(value, field=field, maximum=item_maximum)
        for value in values
    }
    return tuple(sorted(cleaned))


@dataclass(frozen=True)
class GateRequest:
    message: str
    session_id: str
    project: str
    working_directory: str
    active_paths: tuple[str, ...]
    previous_deliveries: tuple[str, ...]
    available_namespaces: tuple[str, ...]
    token_budget: int
    context_catalog: Mapping[str, Any] | None = None

    @classmethod
    def create(
        cls,
        *,
        message: str,
        session_id: str,
        project: str,
        working_directory: str,
        active_paths: Sequence[str] = (),
        previous_deliveries: Sequence[str] = (),
        available_namespaces: Sequence[str] = (),
        token_budget: int = 2_000,
        context_catalog: Mapping[str, Any] | None = None,
    ) -> "GateRequest":
        if isinstance(token_budget, bool):
            raise ValueError("token_budget must be an integer")
        parsed_budget = int(token_budget)
        if parsed_budget < MIN_TOKEN_BUDGET or parsed_budget > MAX_TOKEN_BUDGET:
            raise ValueError(
                f"token_budget must be between {MIN_TOKEN_BUDGET} and {MAX_TOKEN_BUDGET}"
            )
        return cls(
            message=_clean_string(
                message, field="message", maximum=MAX_MESSAGE_CHARS
            ),
            session_id=_clean_string(
                session_id, field="session_id", maximum=255
            ),
            project=_clean_string(project, field="project", maximum=255),
            working_directory=_clean_string(
                working_directory,
                field="working_directory",
                maximum=MAX_PATH_CHARS,
            ),
            active_paths=_string_tuple(
                active_paths,
                field="active_paths",
                limit=MAX_PATHS,
                item_maximum=MAX_PATH_CHARS,
            ),
            previous_deliveries=_string_tuple(
                previous_deliveries,
                field="previous_deliveries",
                limit=1_000,
                item_maximum=255,
            ),
            available_namespaces=_string_tuple(
                available_namespaces,
                field="available_namespaces",
                limit=MAX_NAMESPACES,
                item_maximum=255,
            ),
            token_budget=parsed_budget,
            context_catalog=dict(context_catalog or {}),
        )

    def model_payload(self) -> dict[str, Any]:
        return {
            "request": self.message,
            "project": self.project,
            "workingDirectory": self.working_directory,
            "activePaths": list(self.active_paths),
            "previouslyDelivered": list(self.previous_deliveries),
            "availableNamespaces": list(self.available_namespaces),
            "contextCatalog": dict(self.context_catalog or {}),
            "tokenBudget": self.token_budget,
        }


@dataclass(frozen=True)
class GateProposal:
    action: str
    query: str | None
    scopes: tuple[str, ...]
    keywords: tuple[str, ...]
    reason_code: str
    clarification: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GateProposal":
        allowed = {
            "action",
            "query",
            "scopes",
            "keywords",
            "reasonCode",
            "clarification",
        }
        extras = sorted(set(value) - allowed)
        if extras:
            raise ValueError(f"gate output contains unsupported fields: {', '.join(extras)}")

        action = str(value.get("action", "")).strip().lower()
        if action not in MODEL_ACTIONS:
            raise ValueError(f"unsupported gate action: {action or '<empty>'}")

        raw_query = value.get("query")
        query = None if raw_query is None else _clean_string(
            raw_query, field="query", maximum=MAX_QUERY_CHARS
        )
        raw_clarification = value.get("clarification")
        clarification = None if raw_clarification is None else _clean_string(
            raw_clarification, field="clarification", maximum=2_048
        )

        raw_scopes = value.get("scopes", [])
        if not isinstance(raw_scopes, list):
            raise ValueError("scopes must be an array")
        scopes = _string_tuple(
            raw_scopes, field="scopes", limit=len(GATE_SCOPES), item_maximum=16
        )
        unsupported_scopes = sorted(set(scopes) - GATE_SCOPES)
        if unsupported_scopes:
            raise ValueError(f"unsupported gate scopes: {', '.join(unsupported_scopes)}")

        raw_keywords = value.get("keywords", [])
        if not isinstance(raw_keywords, list):
            raise ValueError("keywords must be an array")
        keywords = _string_tuple(
            raw_keywords,
            field="keywords",
            limit=MAX_KEYWORDS,
            item_maximum=128,
        )

        reason_code = str(value.get("reasonCode", "")).strip().upper()
        if reason_code not in REASON_CODES:
            raise ValueError(f"unsupported reasonCode: {reason_code or '<empty>'}")
        if action == "search" and query is None:
            raise ValueError("search decisions require query")
        if action == "ask" and clarification is None:
            raise ValueError("ask decisions require clarification")
        if action == "skip" and query is not None:
            raise ValueError("skip decisions cannot include query")

        return cls(
            action=action,
            query=query,
            scopes=scopes,
            keywords=keywords,
            reason_code=reason_code,
            clarification=clarification,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "query": self.query,
            "scopes": list(self.scopes),
            "keywords": list(self.keywords),
            "reasonCode": self.reason_code,
            "clarification": self.clarification,
        }


@dataclass(frozen=True)
class ProviderResult:
    proposal: GateProposal
    model_id: str
    model_revision: str | None
    latency_ms: int


@dataclass(frozen=True)
class GateDecision:
    action: str
    proposal: GateProposal
    delivery: tuple[dict[str, Any], ...]
    omitted: tuple[dict[str, Any], ...]
    request_id: int | None
    clarification: str | None
    model_id: str | None
    model_revision: str | None
    latency_ms: int | None
    fallback_reason: str | None

    def __post_init__(self) -> None:
        if self.action not in FINAL_ACTIONS:
            raise ValueError(f"unsupported final gate action: {self.action}")

    def as_dict(self, *, decision_id: int | None = None) -> dict[str, Any]:
        return {
            "schemaVersion": GATE_SCHEMA_VERSION,
            "decisionId": decision_id,
            "action": self.action,
            "proposal": self.proposal.as_dict(),
            "delivery": list(self.delivery),
            "omitted": list(self.omitted),
            "requestId": self.request_id,
            "clarification": self.clarification,
            "model": {
                "id": self.model_id,
                "revision": self.model_revision,
                "latencyMs": self.latency_ms,
            },
            "fallback": self.fallback_reason,
        }


GATE_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "purpory_memory_gate",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": sorted(MODEL_ACTIONS)},
            "query": {"type": ["string", "null"]},
            "scopes": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(GATE_SCOPES)},
                "uniqueItems": True,
                "maxItems": len(GATE_SCOPES),
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string", "maxLength": 128},
                "uniqueItems": True,
                "maxItems": MAX_KEYWORDS,
            },
            "reasonCode": {
                "type": "string",
                "enum": sorted(REASON_CODES - {"GATE_UNAVAILABLE"}),
            },
            "clarification": {"type": ["string", "null"]},
        },
        "required": [
            "action",
            "query",
            "scopes",
            "keywords",
            "reasonCode",
            "clarification",
        ],
        "additionalProperties": False,
    },
}
