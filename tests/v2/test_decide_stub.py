"""Offline tests for the deterministic stub decision backend."""

from __future__ import annotations

import asyncio

import pytest
from testzeus_hercules.decide.backends import StubDecider
from testzeus_hercules.decide.contract import (
    BoxAnswer,
    BoxQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    Decision,
    DecisionRequest,
    NoulAnswer,
    NoulQuestion,
    ScoreQuestion,
)
from testzeus_hercules.decide.engine import DecisionEngine, DecisionError, DecisionUnavailable


def _request() -> DecisionRequest:
    return DecisionRequest(
        state={"step": "Click Continue"},
        questions={
            "tool": ChoiceQuestion(instructions="Which tool?", criteria={"click": "Activate", "hover": "Reveal", "scroll": None}),
            "target_absent": NoulQuestion(instructions="Missing?"),
            "urgency": ScoreQuestion(criteria=["low", "mid", "high"]),
            "region": BoxQuestion(instructions="Locate the cart"),
        },
    )


def _decide(stub: StubDecider, request: DecisionRequest, purpose: str = "test") -> Decision:
    return asyncio.run(stub.decide(request, purpose=purpose))


def test_stub_satisfies_engine_protocol() -> None:
    assert isinstance(StubDecider(), DecisionEngine)


def test_default_policy() -> None:
    decision = _decide(StubDecider(), _request())
    assert decision.engine == "stub"
    assert decision.cached is False
    tool = decision.choice("tool")
    assert (tool.choice, tool.confidence) == ("click", 1.0)
    assert tool.probabilities == {"click": 1.0, "hover": 0.0, "scroll": 0.0}
    assert decision.noul("target_absent").noul == 0.0
    score = decision.score("urgency")
    assert score.score == 0
    assert score.legend == {"0": "low", "1": "mid", "2": "high"}
    assert score.probabilities == {"0": 1.0, "1": 0.0, "2": 0.0}
    box = decision.box("region")
    assert box.box.model_dump() == {"top": 0.0, "bottom": 1.0, "left": 0.0, "right": 1.0}
    assert box.confidence is None


def test_fixed_answers_override_defaults() -> None:
    stub = StubDecider({"tool": ChoiceAnswer(choice="hover", confidence=0.8, probabilities={"click": 0.2, "hover": 0.8, "scroll": 0.0})})
    decision = _decide(stub, _request())
    assert decision.choice("tool").choice == "hover"
    assert decision.noul("target_absent").noul == 0.0


def test_fixed_answers_accept_wire_dicts() -> None:
    stub = StubDecider({"target_absent": {"type": "noul", "noul": 0.9}})
    assert _decide(stub, _request()).noul("target_absent").noul == 0.9


def test_callable_answers_receive_request_and_qid() -> None:
    seen: list[str] = []

    def answer(request: DecisionRequest, qid: str) -> NoulAnswer:
        seen.append(qid)
        return NoulAnswer(noul=0.75 if "step" in request.state else 0.0)

    decision = _decide(StubDecider({"target_absent": answer}), _request())
    assert decision.noul("target_absent").noul == 0.75
    assert seen == ["target_absent"]


def test_mismatched_fixture_fails_loudly() -> None:
    stub = StubDecider({"tool": NoulAnswer(noul=0.5)})
    with pytest.raises(DecisionError, match="tool"):
        _decide(stub, _request())


def test_choice_fixture_outside_criteria_fails() -> None:
    stub = StubDecider({"tool": ChoiceAnswer(choice="drag", confidence=1.0, probabilities={"drag": 1.0})})
    with pytest.raises(DecisionError, match="drag"):
        _decide(stub, _request())


def test_records_calls_in_order() -> None:
    stub = StubDecider()
    first, second = _request(), DecisionRequest(state={}, questions={"q": BoxQuestion()})
    _decide(stub, first, purpose="target")
    _decide(stub, second, purpose="verify")
    assert [(c.purpose, c.request) for c in stub.calls] == [("target", first), ("verify", second)]


def test_is_deterministic() -> None:
    stub = StubDecider()
    assert _decide(stub, _request()).answers == _decide(stub, _request()).answers


def test_configured_error_is_raised_and_call_recorded() -> None:
    stub = StubDecider(error=DecisionUnavailable("offline"))
    with pytest.raises(DecisionUnavailable, match="offline"):
        _decide(stub, _request())
    assert len(stub.calls) == 1


def test_box_fixture_is_returned() -> None:
    region = BoxAnswer(box={"top": 0.1, "bottom": 0.5, "left": 0.2, "right": 0.6}, confidence=0.9)
    assert _decide(StubDecider({"region": region}), _request()).box("region") == region


def test_decision_errors_hierarchy() -> None:
    from testzeus_hercules.decide.engine import DecisionRejected

    assert issubclass(DecisionUnavailable, DecisionError)
    assert issubclass(DecisionRejected, DecisionError)
