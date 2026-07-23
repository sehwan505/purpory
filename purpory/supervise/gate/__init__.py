"""Situation-aware context gateway.

The package is intentionally standard-library only. Model runtimes stay behind
the provider protocol so importing ordinary context commands never loads them.
"""

from purpory.supervise.gate.contract import (
    GateDecision,
    GateProposal,
    GateRequest,
    ProviderResult,
)
from purpory.supervise.gate.provider import (
    GateProvider,
    GateProviderError,
    UnavailableGateProvider,
)

__all__ = [
    "GateDecision",
    "GateProposal",
    "GateProvider",
    "GateProviderError",
    "GateRequest",
    "ProviderResult",
    "UnavailableGateProvider",
]
