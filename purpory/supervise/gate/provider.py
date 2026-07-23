"""Provider boundary for situation-aware gate models."""

from __future__ import annotations

from typing import Protocol

from purpory.supervise.gate.contract import GateRequest, ProviderResult


class GateProviderError(RuntimeError):
    """A gate provider could not return a valid constrained decision."""


class UnavailableGateProvider:
    """Preserve a runtime startup failure in the normal audited fallback path."""

    def __init__(self, reason: str) -> None:
        self.reason = reason.strip() or "gate provider is unavailable"

    def propose(self, request: GateRequest) -> ProviderResult:
        raise GateProviderError(self.reason)


class GateProvider(Protocol):
    def propose(self, request: GateRequest) -> ProviderResult:
        """Return one schema-validated model proposal."""
        ...
