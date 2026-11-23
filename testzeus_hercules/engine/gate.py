"""Confidence gate: accept a decider's target pick only above the engine's own threshold.

Thresholds are per engine because confidences are not comparable across
engines. These are conservative defaults until calibration produces real ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from testzeus_hercules.decide.contract import Decision
from testzeus_hercules.decide.questions import md_from_option

# JEV's probabilities are calibrated, so its threshold matches the screenshot re-ask gate.
# "clef" (a local Clef server) uses the default until it is calibrated: untuned.
# "stub" is the test double: it accepts whatever the test scripted.
THRESHOLDS: Mapping[str, float] = MappingProxyType({"stub": 0.0, "jev-openrouter": 0.5, "jev-typesafe": 0.5, "clef": 0.6})
DEFAULT_THRESHOLD = 0.6


def threshold_for(engine: str) -> float:
    return THRESHOLDS.get(engine, DEFAULT_THRESHOLD)


@dataclass(frozen=True)
class GateResult:
    md: int | None
    confidence: float
    accepted: bool
    reason: str


def gate_target(decision: Decision, threshold: float) -> GateResult:
    """Accept the ``target`` answer when it names an element with enough confidence."""
    answer = decision.choice("target")
    md = md_from_option(answer.choice)
    if md is None:
        return GateResult(md=None, confidence=answer.confidence, accepted=False, reason="no listed element matched")
    if answer.confidence < threshold:
        return GateResult(md=md, confidence=answer.confidence, accepted=False, reason=f"low confidence {answer.confidence:.2f} (threshold {threshold:.2f})")
    return GateResult(md=md, confidence=answer.confidence, accepted=True, reason="accepted")
