"""Hybrid-mode decider: JEV makes every decision; the local model answers only when JEV cannot.

Each request goes to JEV text only first (the screenshot stripped). When images may
leave the machine (egress ``hybrid``) and the ``target`` answer's confidence is below
``image_gate``, the same request is asked again with the screenshot and that answer
wins; a failed re-ask keeps the text answer. When JEV cannot answer at all (network
down, circuit open, timeout, a refused or malformed exchange), that single decision
falls back to the local decider with the full request, and the route records why.
A ``visual`` judgement (does the screenshot show what the step expects?) always carries
the screenshot: it goes to JEV when images may leave, else to the local model. A
``semantic`` or ``precondition`` judgement (does the page show, by meaning, that an
expectation holds?) goes to JEV with the screenshot when images may leave, else with
the page text only.

:meth:`JevFallbackDecider.route_of` returns every call made for a decision (engine,
model, latency, cost, confidence, images, error) plus the fallback reason;
:meth:`JevFallbackDecider.usage` totals calls and cost for the run.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from testzeus_hercules.decide.contract import ChoiceAnswer, ChoiceQuestion, Decision, DecisionRequest
from testzeus_hercules.decide.engine import DecisionEngine
from testzeus_hercules.decide.errors import DecisionError
from testzeus_hercules.decide.payload import is_image_part

__all__ = ["DEFAULT_IMAGE_GATE", "JUDGE_PURPOSES", "VISUAL_PURPOSE", "DecisionUsage", "JevFallbackDecider", "without_images"]

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_GATE = 0.5
# Decisions about how the page looks (see the step graph's visual checks): only answerable with the screenshot.
VISUAL_PURPOSE = "visual"
# Judgements of an expectation by meaning: JEV sees the screenshot from the start when images may leave.
JUDGE_PURPOSES = frozenset({"semantic", "precondition"})
_MAX_ROUTES = 256
_MAX_ERROR_CHARS = 300


@dataclass(frozen=True)
class DecisionUsage:
    """Call counts and cost of the decisions made so far."""

    jev_calls: int = 0
    jev_failures: int = 0
    image_calls: int = 0
    local_calls: int = 0
    fallbacks: int = 0
    cost_usd: float = 0.0
    # Estimated prompt tokens sent to the hosted (or Clef) engine: it reports cost, not token counts.
    prompt_tokens_est: int = 0


def without_images(value: Any) -> Any:
    """Copy of ``value`` with every inline image part removed (dict entries dropped, list items skipped)."""
    if isinstance(value, dict):
        return {key: without_images(item) for key, item in value.items() if not is_image_part(item)}
    if isinstance(value, list):
        return [without_images(item) for item in value if not is_image_part(item)]
    return value


def _text_only(request: DecisionRequest) -> DecisionRequest:
    if not request.has_images():
        return request
    return DecisionRequest.model_validate(without_images(request.to_wire()))


def _target_confidence(decision: Decision) -> float | None:
    answer = decision.answers.get("target")
    return round(answer.confidence, 4) if isinstance(answer, ChoiceAnswer) else None


def _engine_name(engine: DecisionEngine) -> str:
    return str(getattr(engine, "engine", type(engine).__name__))


class JevFallbackDecider:
    """:class:`~testzeus_hercules.decide.engine.DecisionEngine` for hybrid mode: ``jev`` decides, ``fallback`` covers outages."""

    def __init__(self, jev: DecisionEngine, fallback: DecisionEngine, *, images: bool, image_gate: float = DEFAULT_IMAGE_GATE) -> None:
        if not 0.0 <= image_gate <= 1.0:
            raise ValueError("image_gate must be between 0 and 1")
        self.jev = jev
        self.fallback = fallback
        self.images = images
        self.image_gate = image_gate
        self._usage = DecisionUsage()
        self._routes: OrderedDict[int, dict[str, Any]] = OrderedDict()

    @property
    def engine(self) -> str:
        return f"{_engine_name(self.jev)}+{_engine_name(self.fallback)}"

    def usage(self) -> DecisionUsage:
        return self._usage

    async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision:
        calls: list[dict[str, Any]] = []
        if purpose == VISUAL_PURPOSE and request.has_images():
            return await self._judge_visual(request, purpose, calls)
        with_images = purpose in JUDGE_PURPOSES and self.images and request.has_images()
        try:
            decision = await self._ask(self.jev, request if with_images else _text_only(request), purpose, calls, images=with_images)
        except DecisionError as exc:
            reason = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
            logger.warning("JEV could not answer %r (%s); the local model decides instead", purpose, reason)
            decision = await self._ask(self.fallback, request, purpose, calls, images=request.has_images())
            self._count(fallbacks=1)
            return self._finish(decision, calls, fallback=reason)
        confidence = _target_confidence(decision)
        unsure = confidence is not None and confidence < self.image_gate
        if self.images and request.has_images() and unsure and isinstance(request.questions.get("target"), ChoiceQuestion):
            logger.info("JEV text-only confidence %.2f < %.2f for %r; asking again with the screenshot", confidence, self.image_gate, purpose)
            try:
                decision = await self._ask(self.jev, request, purpose, calls, images=True)
            except DecisionError as exc:
                logger.warning("JEV image re-ask failed for %r (%s); keeping the text-only answer", purpose, type(exc).__name__)
        return self._finish(decision, calls, fallback=None)

    async def _judge_visual(self, request: DecisionRequest, purpose: str, calls: list[dict[str, Any]]) -> Decision:
        """A judgement about how the page looks needs the screenshot: JEV when images may leave the machine, else the local model."""
        if not self.images:
            return self._finish(await self._ask(self.fallback, request, purpose, calls, images=True), calls, fallback="images stay local")
        try:
            return self._finish(await self._ask(self.jev, request, purpose, calls, images=True), calls, fallback=None)
        except DecisionError as exc:
            reason = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
            logger.warning("JEV could not judge %r (%s); the local model judges instead", purpose, reason)
            decision = await self._ask(self.fallback, request, purpose, calls, images=True)
            self._count(fallbacks=1)
            return self._finish(decision, calls, fallback=reason)

    def route_of(self, decision: Decision) -> dict[str, Any] | None:
        """The calls made for ``decision`` and the fallback reason, if this decider made it."""
        route = self._routes.get(id(decision))
        if route is None or route["decision"] is not decision:
            return None
        return {key: value for key, value in route.items() if key != "decision"}

    async def _ask(self, engine: DecisionEngine, request: DecisionRequest, purpose: str, calls: list[dict[str, Any]], *, images: bool) -> Decision:
        is_jev = engine is self.jev
        started = time.perf_counter()
        record: dict[str, Any] = {"engine": _engine_name(engine), "images": images}
        calls.append(record)
        if is_jev:
            self._count(jev_calls=1, image_calls=int(images), prompt_tokens_est=request.text_tokens_estimate())
        else:
            self._count(local_calls=1)
        try:
            decision = await engine.decide(request, purpose=purpose)
        except DecisionError as exc:
            record.update(error=f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS], latency_ms=int((time.perf_counter() - started) * 1000))
            if is_jev:
                self._count(jev_failures=1)
            raise
        record.update(model=decision.model_version, latency_ms=decision.latency_ms, cost_usd=decision.cost_usd, confidence=_target_confidence(decision))
        if decision.cost_usd:
            self._count(cost_usd=decision.cost_usd)
        return decision

    def _count(self, **deltas: float) -> None:
        current = self._usage
        self._usage = DecisionUsage(**{name: getattr(current, name) + deltas.get(name, 0) for name in DecisionUsage.__dataclass_fields__})

    def _finish(self, decision: Decision, calls: list[dict[str, Any]], *, fallback: str | None) -> Decision:
        self._routes[id(decision)] = {"answered_by": decision.engine, "calls": calls, "fallback": fallback, "decision": decision}
        while len(self._routes) > _MAX_ROUTES:
            self._routes.popitem(last=False)
        return decision
