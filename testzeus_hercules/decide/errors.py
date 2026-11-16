"""Exceptions raised by decision engines."""

from __future__ import annotations


class DecisionError(Exception):
    """Base class for every decision-engine failure."""


class DecisionUnavailable(DecisionError):
    """The backend cannot answer right now (offline, timed out, circuit open, server error)."""


class DecisionRejected(DecisionError):
    """The backend refused the request as invalid; retrying the same request will not help."""
