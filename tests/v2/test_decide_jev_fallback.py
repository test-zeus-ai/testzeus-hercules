"""Tests for the hybrid-mode decider: JEV decides (text first, the screenshot only when unsure), the local model covers outages."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from testzeus_hercules.decide.backends.jev_fallback import JevFallbackDecider
from testzeus_hercules.decide.contract import ChoiceAnswer, Decision, DecisionRequest, NoulAnswer
from testzeus_hercules.decide.errors import DecisionError, DecisionRejected, DecisionUnavailable
from testzeus_hercules.decide.questions import TargetSpec, build_step_request

from tests.v2.engine_fixtures.fakes import FakeSnapshot, login_elements

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


class ScriptedEngine:
    """Answers ``md_6`` with the next scripted confidence, or raises the next scripted error."""

    def __init__(self, engine: str, *script: float | DecisionError, cost: float | None = 0.002, model: str = "m-1") -> None:
        self.engine = engine
        self.script = list(script)
        self.cost = cost
        self.model = model
        self.requests: list[DecisionRequest] = []

    async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else 0.9
        if isinstance(step, DecisionError):
            raise step
        options = request.questions["target"].criteria
        rest = (1.0 - step) / (len(options) - 1)
        probabilities = {name: (step if name == "md_6" else rest) for name in options}
        answers: dict[str, Any] = {"target": ChoiceAnswer(choice="md_6", confidence=step, probabilities=probabilities), "target_absent": NoulAnswer(noul=0.05)}
        return Decision(answers=answers, engine=self.engine, model_version=self.model, latency_ms=120, cost_usd=self.cost)


def _request(images: bool = True) -> DecisionRequest:
    snapshot = FakeSnapshot(seq=1, elements=login_elements(), som_jpeg=JPEG)
    return build_step_request(TargetSpec(kind="click", target="Sign in", text='click "Sign in"'), snapshot, images=images).request


def _decider(jev: ScriptedEngine, local: ScriptedEngine | None = None, *, images: bool = True, gate: float = 0.5) -> JevFallbackDecider:
    return JevFallbackDecider(jev, local or ScriptedEngine("local-llm", cost=0.0, model="gemma"), images=images, image_gate=gate)


def test_confident_text_answer_needs_one_text_only_call() -> None:
    jev = ScriptedEngine("jev-openrouter", 0.93)
    decider = _decider(jev)

    decision = asyncio.run(decider.decide(_request(), purpose="target"))

    assert decision.engine == "jev-openrouter"
    assert len(jev.requests) == 1
    assert not jev.requests[0].has_images(), "the first JEV call is text only"
    route = decider.route_of(decision)
    assert route is not None
    assert [call["images"] for call in route["calls"]] == [False]
    assert route["fallback"] is None
    assert route["answered_by"] == "jev-openrouter"


def test_unsure_text_answer_is_asked_again_with_the_screenshot() -> None:
    jev = ScriptedEngine("jev-openrouter", 0.31, 0.88)
    decider = _decider(jev, gate=0.5)

    decision = asyncio.run(decider.decide(_request(), purpose="target"))

    assert len(jev.requests) == 2
    assert not jev.requests[0].has_images() and jev.requests[1].has_images()
    assert decision.choice("target").confidence == pytest.approx(0.88)
    calls = decider.route_of(decision)["calls"]
    assert [(call["images"], call["confidence"]) for call in calls] == [(False, 0.31), (True, 0.88)]
    assert decider.usage().jev_calls == 2


def test_text_only_egress_never_sends_the_screenshot() -> None:
    jev = ScriptedEngine("jev-openrouter", 0.31)
    decider = _decider(jev, images=False)

    asyncio.run(decider.decide(_request(), purpose="target"))

    assert len(jev.requests) == 1
    assert not jev.requests[0].has_images()


@pytest.mark.parametrize("error", [DecisionUnavailable("JEV circuit open after 3 consecutive failures"), DecisionError("JEV response has no answers object"), DecisionRejected("HTTP 400")])
def test_jev_failure_falls_back_to_the_local_model_for_that_decision(error: DecisionError) -> None:
    jev = ScriptedEngine("jev-openrouter", error, 0.9)
    local = ScriptedEngine("local-llm", 0.8, cost=0.0, model="gemma")
    decider = _decider(jev, local)

    first = asyncio.run(decider.decide(_request(), purpose="target"))
    second = asyncio.run(decider.decide(_request(), purpose="target"))

    assert first.engine == "local-llm"
    assert local.requests[0].has_images(), "the local model gets the full request"
    route = decider.route_of(first)
    assert str(error) in route["fallback"]
    assert route["answered_by"] == "local-llm"
    assert [call["engine"] for call in route["calls"]] == ["jev-openrouter", "local-llm"]
    assert route["calls"][0]["error"]
    assert second.engine == "jev-openrouter", "the next decision tries JEV again"


def test_failed_image_reask_keeps_the_text_answer() -> None:
    jev = ScriptedEngine("jev-openrouter", 0.31, DecisionRejected("image #1 is 100x100 px"))
    decider = _decider(jev)

    decision = asyncio.run(decider.decide(_request(), purpose="target"))

    assert decision.engine == "jev-openrouter"
    assert decision.choice("target").confidence == pytest.approx(0.31)
    calls = decider.route_of(decision)["calls"]
    assert calls[1]["images"] is True and "100x100" in calls[1]["error"]


def test_usage_totals_cost_and_calls_per_engine() -> None:
    jev = ScriptedEngine("jev-openrouter", 0.31, 0.9, DecisionUnavailable("down"), cost=0.0015)
    decider = _decider(jev)

    asyncio.run(decider.decide(_request(), purpose="target"))
    asyncio.run(decider.decide(_request(), purpose="target"))

    usage = decider.usage()
    assert usage.jev_calls == 3
    assert usage.jev_failures == 1
    assert usage.fallbacks == 1
    assert usage.local_calls == 1
    assert usage.cost_usd == pytest.approx(0.003)
    assert decider.engine == "jev-openrouter+local-llm"


def test_route_is_json_serialisable_and_names_the_model() -> None:
    decider = _decider(ScriptedEngine("jev-openrouter", 0.9, model="typesafe/jev-1.14"))
    decision = asyncio.run(decider.decide(_request(), purpose="target"))

    route = decider.route_of(decision)
    json.dumps(route)
    assert route["calls"][0]["model"] == "typesafe/jev-1.14"
    assert route["calls"][0]["cost_usd"] == pytest.approx(0.002)
    assert isinstance(route["calls"][0]["latency_ms"], int)


class NoulEngine:
    """Answers every yes/no question with a fixed probability and records what it was asked."""

    def __init__(self, engine: str, noul: float = 0.1) -> None:
        self.engine = engine
        self.noul = noul
        self.requests: list[DecisionRequest] = []

    async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision:
        self.requests.append(request)
        return Decision(answers={"holds": NoulAnswer(noul=self.noul)}, engine=self.engine, model_version="m", latency_ms=5, cost_usd=0.001)


def _visual_request() -> DecisionRequest:
    from testzeus_hercules.decide.contract import NoulQuestion
    from testzeus_hercules.decide.payload import ImagePart

    return DecisionRequest(
        state={"expectation": "a photo of the product is shown", "screenshot": ImagePart.from_bytes(JPEG, "image/jpeg")}, questions={"holds": NoulQuestion(instructions="Does it hold?")}
    )


def test_a_visual_judgement_goes_to_jev_with_the_screenshot_when_images_may_leave() -> None:
    jev, local = NoulEngine("jev-openrouter"), NoulEngine("local-llm")
    decider = JevFallbackDecider(jev, local, images=True)

    decision = asyncio.run(decider.decide(_visual_request(), purpose="visual"))

    assert decision.engine == "jev-openrouter"
    assert len(jev.requests) == 1 and jev.requests[0].has_images(), "a text-only judgement of a screenshot is meaningless"
    assert local.requests == []


def test_a_visual_judgement_stays_local_when_images_must_not_leave() -> None:
    jev, local = NoulEngine("jev-openrouter"), NoulEngine("local-llm")
    decider = JevFallbackDecider(jev, local, images=False)

    decision = asyncio.run(decider.decide(_visual_request(), purpose="visual"))

    assert decision.engine == "local-llm"
    assert jev.requests == []
    assert local.requests[0].has_images()


@pytest.mark.parametrize("purpose", ["semantic", "precondition"])
def test_a_judgement_by_meaning_goes_to_jev_with_the_screenshot_only_when_images_may_leave(purpose: str) -> None:
    jev, local = NoulEngine("jev-openrouter"), NoulEngine("local-llm")
    asyncio.run(JevFallbackDecider(jev, local, images=True).decide(_visual_request(), purpose=purpose))
    text_jev = NoulEngine("jev-openrouter")
    asyncio.run(JevFallbackDecider(text_jev, NoulEngine("local-llm"), images=False).decide(_visual_request(), purpose=purpose))

    assert jev.requests[0].has_images() and local.requests == []
    assert not text_jev.requests[0].has_images(), "egress hybrid-text: page text only"
