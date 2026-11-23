"""The decision-engine protocol every backend implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from testzeus_hercules.decide.contract import Decision, DecisionRequest
from testzeus_hercules.decide.errors import DecisionError, DecisionRejected, DecisionUnavailable

__all__ = ["DecisionEngine", "DecisionError", "DecisionRejected", "DecisionUnavailable"]


@runtime_checkable
class DecisionEngine(Protocol):
    """Answers a :class:`DecisionRequest` with a :class:`Decision`.

    ``purpose`` names the call site (for example ``"target"`` or ``"verdict"``) for
    logging, caching and per-question thresholds. Implementations raise
    :class:`DecisionUnavailable` when the backend cannot be reached and
    :class:`DecisionRejected` when it refuses the request as invalid.
    """

    async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision: ...
