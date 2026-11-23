"""Long picks: rows split into parts under the size limit, in page order, then a final pick among the parts' winners."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from testzeus_hercules.decide.backends.jev import JevBackend
from testzeus_hercules.decide.chunking import decide_in_chunks, plan_chunks
from testzeus_hercules.decide.contract import ChoiceAnswer, ChoiceQuestion, Decision, DecisionRequest, NoulAnswer
from testzeus_hercules.decide.questions import MAX_TEXT_BYTES, NO_MATCH, TargetSpec, build_step_request

from tests.v2.engine_fixtures.fakes import FakeBBox, FakeElement, FakeSnapshot

FILLER = "a long descriptive product title that pads the row " * 4


def many(count: int, wanted: frozenset[int] = frozenset()) -> FakeSnapshot:
    elements = tuple(FakeElement(md=md, role="link", name=("Wanted item " if md in wanted else "") + f"{FILLER}{md}", bbox=FakeBBox(x=10, y=30 * md)) for md in range(1, count + 1))
    return FakeSnapshot(seq=1, elements=elements)


def pick(count: int, wanted: frozenset[int] = frozenset()) -> DecisionRequest:
    return build_step_request(TargetSpec(kind="click", target="item"), many(count, wanted)).request


class Picker:
    """A decider that picks the first row named "Wanted …", else no_match; records every request."""

    def __init__(self, *, confidence: float = 0.9, cost: float = 0.001) -> None:
        self.requests: list[DecisionRequest] = []
        self.confidence = confidence
        self.cost = cost

    async def __call__(self, request: DecisionRequest) -> Decision:
        self.requests.append(request)
        options = list(request.questions["target"].criteria)  # type: ignore[union-attr]
        wanted = [row["id"] for row in request.state["rows"] if row["name"].startswith("Wanted")]
        choice = wanted[0] if wanted else NO_MATCH
        rest = (1 - self.confidence) / max(len(options) - 1, 1)
        confidence = self.confidence if wanted else self.confidence - 0.01 * len(self.requests)
        probabilities = {name: (confidence if name == choice else rest) for name in options}
        answers: dict[str, Any] = {"target": ChoiceAnswer(choice=choice, confidence=confidence, probabilities=probabilities), "target_absent": NoulAnswer(noul=0.1 if wanted else 0.8)}
        return Decision(answers=answers, engine="fake", model_version="fake-1", latency_ms=5, cost_usd=self.cost)


def ids(request: DecisionRequest) -> list[str]:
    return [row["id"] for row in request.state["rows"]]


def test_a_small_pick_is_one_call() -> None:
    picker = Picker()
    request = pick(10, frozenset({3}))

    decision = asyncio.run(decide_in_chunks(picker, request, max_text_bytes=MAX_TEXT_BYTES))

    assert len(picker.requests) == 1 and picker.requests[0] is request
    assert decision.choice("target").choice == "md_3"


def test_a_pick_just_over_32_kb_is_split_in_page_order() -> None:
    request = pick(120)
    assert request.text_size_bytes() > MAX_TEXT_BYTES, "fixture must cross the limit"

    plan = plan_chunks(request, max_text_bytes=MAX_TEXT_BYTES)

    assert plan is not None and plan.row_questions == ("target",)
    assert len(plan.parts) >= 2
    flat = [row["id"] for part in plan.parts for row in part]
    assert flat == [f"md_{md}" for md in range(1, 121)], "parts follow page (DOM) order and lose no row"


def test_just_over_the_limit_makes_two_parts_each_under_it() -> None:
    size = next(count for count in range(40, 200) if pick(count).text_size_bytes() > MAX_TEXT_BYTES)
    request = pick(size)
    picker = Picker()

    asyncio.run(decide_in_chunks(picker, request, max_text_bytes=MAX_TEXT_BYTES))

    parts = picker.requests
    assert len(parts) == 2, "no winner: two part calls and no final call"
    assert all(part.text_size_bytes() <= MAX_TEXT_BYTES for part in parts)
    assert ids(parts[0]) + ids(parts[1]) == [f"md_{md}" for md in range(1, size + 1)]
    for part in parts:
        options = set(part.questions["target"].criteria)  # type: ignore[union-attr]
        assert options == set(ids(part)) | {NO_MATCH}, "each part offers its own rows and no_match"


def test_winners_of_each_part_are_decided_in_a_final_call() -> None:
    request = pick(160, frozenset({7, 150}))
    picker = Picker()

    decision = asyncio.run(decide_in_chunks(picker, request, max_text_bytes=MAX_TEXT_BYTES))

    *parts, final = picker.requests
    assert len(parts) >= 2
    assert ids(final) == ["md_7", "md_150"], "the final call sees only the winners, in page order"
    assert set(final.questions["target"].criteria) == {"md_7", "md_150", NO_MATCH}  # type: ignore[union-attr]
    assert final.state["rows"][0]["where"].startswith("top-left"), "winners keep their location text"
    assert decision.choice("target").choice == "md_7"
    assert decision.cost_usd == pytest.approx(0.001 * len(picker.requests))
    assert decision.engine == "fake"


def test_a_single_winner_is_the_answer_of_the_part_that_chose_it_without_asking_again() -> None:
    request = pick(160, frozenset({150}))
    picker = Picker()

    decision = asyncio.run(decide_in_chunks(picker, request, max_text_bytes=MAX_TEXT_BYTES))

    assert all(len(ids(part)) > 1 for part in picker.requests), "no call asks about the one winner alone"
    chosen_by = [part for part in picker.requests if "md_150" in ids(part)]
    assert len(chosen_by) == 1
    assert decision.choice("target").choice == "md_150"
    assert decision.cost_usd == pytest.approx(0.001 * len(picker.requests))


def test_no_winner_returns_the_least_sure_part_so_abstaining_stays_honest() -> None:
    request = pick(160)
    picker = Picker()

    decision = asyncio.run(decide_in_chunks(picker, request, max_text_bytes=MAX_TEXT_BYTES))

    answer = decision.choice("target")
    assert answer.choice == NO_MATCH
    least_sure = min(0.9 - 0.01 * (index + 1) for index in range(len(picker.requests)))
    assert answer.confidence == pytest.approx(least_sure)
    assert decision.noul("target_absent").noul == pytest.approx(0.8)


def test_a_decider_with_fewer_options_per_call_gets_smaller_parts() -> None:
    elements = tuple(FakeElement(md=md, role="link", name=f"L{md}") for md in range(1, 121))
    request = build_step_request(TargetSpec(kind="click", target="L99"), FakeSnapshot(seq=1, elements=elements)).request

    plan = plan_chunks(request, max_text_bytes=10**9, max_options=50)

    assert plan is not None
    assert [len(part) for part in plan.parts] == [49, 49, 22], "each part leaves room for no_match"


def test_the_builder_never_offers_more_options_than_one_question_takes() -> None:
    elements = tuple(FakeElement(md=md, role="link", name=f"L{md}") for md in range(1, 301))
    built = build_step_request(TargetSpec(kind="click", target="L1"), FakeSnapshot(seq=1, elements=elements))

    assert len(built.request.questions["target"].criteria) == 255  # type: ignore[union-attr]


def test_requests_without_rows_are_never_split() -> None:
    request = DecisionRequest(state={"text": "x" * (MAX_TEXT_BYTES + 10)}, questions={"q": ChoiceQuestion(criteria={"a": "A", "b": "B"})})

    assert plan_chunks(request, max_text_bytes=MAX_TEXT_BYTES) is None


def test_parts_run_at_most_max_parallel_at_a_time() -> None:
    request = pick(250)
    running = 0
    peak = 0

    async def slow(part: DecisionRequest) -> Decision:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return await Picker()(part)

    asyncio.run(decide_in_chunks(slow, request, max_text_bytes=MAX_TEXT_BYTES, max_parallel=2))

    assert peak == 2


def test_jev_backend_splits_a_long_pick_into_several_http_calls() -> None:
    request = pick(160, frozenset({140}))
    bodies: list[dict[str, Any]] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        bodies.append(body)
        rows = [row for row in body["state"]["rows"] if row["name"].startswith("Wanted")]
        choice = rows[0]["id"] if rows else NO_MATCH
        options = list(body["questions"]["target"]["criteria"])
        answers = {
            "target": {"type": "choice", "choice": choice, "confidence": 0.9, "probabilities": {name: (0.9 if name == choice else 0.1 / (len(options) - 1)) for name in options}},
            "target_absent": {"type": "noul", "noul": 0.1},
        }
        return httpx.Response(200, json={"model": "typesafe/jev-1.14-20261014", "answers": answers, "usage": {"cost": 0.00002}})

    backend = JevBackend(api_key_env="K", egress_policy="hybrid-text", env={"K": "secret"}, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    decision = asyncio.run(backend.decide(request, purpose="target"))

    assert len(bodies) >= 2, "at least two parts; one part chose a row, so no final call"
    assert all(DecisionRequest.model_validate({"state": body["state"], "questions": body["questions"]}).text_size_bytes() <= MAX_TEXT_BYTES for body in bodies)
    rows = [[row["id"] for row in body["state"]["rows"]] for body in bodies]
    assert sum("md_140" in part for part in rows) == 1 and ["md_140"] not in rows, "the answer is the part's that chose it; md_140 is not asked about alone"
    assert all(body["provider"] == {"zdr": True, "data_collection": "deny"} for body in bodies)
    assert decision.choice("target").choice == "md_140"
    assert decision.cost_usd == pytest.approx(0.00002 * len(bodies))
    assert decision.engine == "jev-openrouter"
