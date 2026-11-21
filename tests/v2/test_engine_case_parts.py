"""Reading a long case in parts: sizes, merging, concurrency, per-part caching and per-part fallback.

One call over a long case can fail (an endpoint may refuse a large reply schema or output); each
part is read on its own, with the titles of the other steps as context, and a part that fails is
tried once more, then only its steps are read one at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Sequence, TypeVar

import httpx
import pytest
from pydantic import BaseModel

from testzeus_hercules.engine.intent import (
    MAX_CASE_ITEMS,
    MIN_CASE_ITEMS,
    CaseItem,
    LLMInterpreter,
    StepInterpreter,
    case_items,
    case_part_size,
    case_parts,
)
from testzeus_hercules.llm.client import LLMRequestError, LLMUnavailable
from testzeus_hercules.llm.structured import StructuredOutputError

from tests.v2.test_llm_fakes import FakeServer, completion_response

T = TypeVar("T", bound=BaseModel)
STEPS = 60


def _act(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "target": None, "value": None, "url": None, "expected": None, "match": None, "role": None, **fields}


def _items(count: int = STEPS, *, expected_every: int = 0) -> list[CaseItem]:
    return case_items([(n, f'click "Button {n}"', f'"Done {n}"' if expected_every and n % expected_every == 0 else None) for n in range(1, count + 1)])


def _keys(user: str) -> list[str]:
    return [line.split(":", 1)[0] for line in user.split("ITEMS:\n", 1)[1].splitlines()]


def _answer(keys: Sequence[str]) -> dict[str, Any]:
    reply: dict[str, Any] = {}
    for key in keys:
        number = key.split("_")[1]
        if key.endswith("_expected"):
            reply[key] = [_act("assert_text", expected=f"Done {number}")]
        else:
            reply[f"step_{number}_complete"] = True
            reply[key] = [_act("click", target=f"Button {number}")]
    return reply


class PartLLM:
    """Answers each part from its own ITEMS; fails the parts holding ``failing`` (every time, or ``failures`` times; with
    ``invalid``, only parts of more than two items, as a model whose long replies are not valid)."""

    def __init__(self, failing: int | None = None, failures: int = 99, delay: float = 0.0, refuse: bool = False, invalid: bool = False) -> None:
        self.users: list[str] = []
        self.failing, self.failures, self.delay, self.refuse, self.invalid = failing, failures, delay, refuse, invalid
        self.active = self.peak = 0

    async def complete(self, *, system: str, user: str, schema: type[T], images: Sequence[bytes] = ()) -> T:
        self.users.append(user)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            keys = _keys(user)
            if self.invalid and f"step_{self.failing}_action" in keys and len(keys) <= 2:
                return schema.model_validate(_answer(keys))  # type: ignore[return-value]
            if self.failing is not None and f"step_{self.failing}_action" in keys and self.failures > 0:
                self.failures -= 1
                if self.refuse:
                    raise LLMRequestError(400, "the reply schema is too large")
                if self.invalid:
                    raise StructuredOutputError("model output did not match CaseReadingReply after one repair turn: step_1_action: Field required")
                raise LLMUnavailable("the model server timed out")
            return schema.model_validate(_answer(keys))  # type: ignore[return-value]
        finally:
            self.active -= 1


def test_part_size_follows_the_models_context_and_output_budget() -> None:
    assert case_part_size() == MAX_CASE_ITEMS
    assert case_part_size(context_tokens=131072, max_output_tokens=8192, items=60) == MAX_CASE_ITEMS
    assert case_part_size(context_tokens=6000, max_output_tokens=8192, items=60) == 8, "what fits next to 60 titles"
    assert case_part_size(context_tokens=4096, max_output_tokens=8192, items=60) == MIN_CASE_ITEMS, "never fewer than the minimum"
    assert case_part_size(context_tokens=131072, max_output_tokens=1500) == 6, "the output budget caps a part too"


def test_parts_are_consecutive_and_keep_a_steps_action_and_outcome_together() -> None:
    items = _items(10, expected_every=4)  # 12 items: steps 4 and 8 have an expected outcome

    parts = case_parts(items, 4)

    assert [[number for number, _ in part] for part in parts] == [[1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11], [12]]
    assert [(item.step, item.role) for _, item in parts[0] + parts[1]][2:5] == [(3, "action"), (4, "action"), (4, "expected")]
    assert [number for part in parts for number, _ in part] == list(range(1, 13))


def test_a_60_step_case_is_read_in_parts_and_every_step_keeps_its_own_reading() -> None:
    items = _items()
    llm = PartLLM()

    reading = asyncio.run(LLMInterpreter(llm, part_items=16).read_case(items, names=["user"]))

    assert len(llm.users) == 4
    assert [_keys(user)[0] for user in llm.users] == ["step_1_action", "step_17_action", "step_33_action", "step_49_action"]
    assert [len(_keys(user)) for user in llm.users] == [16, 16, 16, 12]
    second = llm.users[1]
    assert "TEST DATA NAMES (values are given at run time): {{user}}" in second
    assert '  step 16: click "Button 16"' in second and '  step 33: click "Button 33"' in second, "the other steps' titles are context"
    assert '  step 17: click "Button 17"' not in second, "a part's own steps are items, not context"
    for number in range(1, STEPS + 1):
        intent = reading.get(number, "action", f'click "Button {number}"')
        assert intent is not None and intent.target == f"Button {number}", number
        assert intent.complete is True
    assert reading.source == "model"


def test_parts_run_concurrently_up_to_the_limit() -> None:
    llm = PartLLM(delay=0.05)

    asyncio.run(LLMInterpreter(llm, part_items=8, parallel=3).read_case(_items(40)))

    assert len(llm.users) == 5 and llm.peak == 3


def test_a_local_server_reads_parts_one_after_another() -> None:
    llm = PartLLM(delay=0.01)

    asyncio.run(LLMInterpreter(llm, part_items=8).read_case(_items(40)))

    assert llm.peak == 1


def test_a_part_that_fails_once_is_tried_again() -> None:
    llm = PartLLM(failing=20, failures=1)

    reading = asyncio.run(LLMInterpreter(llm, part_items=16).read_case(_items()))

    assert len(llm.users) == 5
    assert all(reading.get(number, "action", f'click "Button {number}"') is not None for number in range(1, STEPS + 1))


def test_a_part_that_keeps_failing_falls_back_only_for_its_own_steps(caplog: pytest.LogCaptureFixture) -> None:
    llm = PartLLM(failing=20)

    with caplog.at_level(logging.WARNING):
        reading = asyncio.run(StepInterpreter(LLMInterpreter(llm, part_items=16)).read_case(_items()))

    unread = [number for number in range(1, STEPS + 1) if reading.get(number, "action", f'click "Button {number}"') is None]
    assert unread == list(range(17, 33)), "only the failing part's steps are read one at a time"
    assert reading.get(16, "action", 'click "Button 16"').target == "Button 16"
    assert reading.get(33, "action", 'click "Button 33"').target == "Button 33"
    messages = [record.getMessage() for record in caplog.records]
    assert "model reading of case part 2 of 4 (steps 17-32) failed (LLMUnavailable); trying it once more" in messages
    assert "model reading of case part 2 of 4 (steps 17-32) failed (LLMUnavailable); its steps are read one at a time" in messages
    assert not any("Button" in message for message in messages), "the log names the error, never the prompt"


def test_a_refused_part_is_read_in_halves_down_to_the_step_the_server_refuses(caplog: pytest.LogCaptureFixture) -> None:
    llm = PartLLM(failing=20, refuse=True)

    with caplog.at_level(logging.WARNING):
        reading = asyncio.run(StepInterpreter(LLMInterpreter(llm, part_items=16)).read_case(_items()))

    unread = [number for number in range(1, STEPS + 1) if reading.get(number, "action", f'click "Button {number}"') is None]
    assert unread == [20], "an endpoint's limit on the request costs only the step it cannot take"
    assert reading.get(21, "action", 'click "Button 21"').target == "Button 21"
    messages = [record.getMessage() for record in caplog.records]
    assert "model reading of case part 2 of 4 (steps 17-32) was refused (LLMRequestError, HTTP 400); reading it in two halves" in messages
    assert "model reading of case part 2 of 4 (step 20) was refused (LLMRequestError, HTTP 400); its steps are read one at a time" in messages
    assert sum("(step 20)" in message for message in messages) == 1, "a refused request is not sent again unchanged"


def test_a_part_whose_reply_stays_invalid_is_read_in_halves(caplog: pytest.LogCaptureFixture) -> None:
    llm = PartLLM(failing=20, invalid=True)

    with caplog.at_level(logging.WARNING):
        reading = asyncio.run(StepInterpreter(LLMInterpreter(llm, part_items=16)).read_case(_items()))

    unread = [number for number in range(1, STEPS + 1) if reading.get(number, "action", f'click "Button {number}"') is None]
    assert unread == [], "a smaller part gets a valid reply"
    messages = [record.getMessage() for record in caplog.records]
    assert "model reading of case part 2 of 4 (steps 17-32) gave no valid reply (StructuredOutputError); reading it in two halves" in messages
    assert not any("trying it once more" in message for message in messages), "the same long reply is not asked for again"


def test_each_part_is_cached_so_editing_one_step_rereads_only_its_part(tmp_path: Path) -> None:
    first = PartLLM()
    asyncio.run(LLMInterpreter(first, model_id="m", cache_dir=tmp_path, part_items=16).read_case(_items()))
    assert len(list(tmp_path.glob("reading-*.json"))) == 4

    edited = [item if item.step != 40 else CaseItem(40, "action", 'click "Button forty"') for item in _items()]
    second = PartLLM()
    reading = asyncio.run(LLMInterpreter(second, model_id="m", cache_dir=tmp_path, part_items=16).read_case(edited))

    assert len(second.users) == 1 and _keys(second.users[0])[0] == "step_33_action"
    assert reading.source == "model"
    assert reading.get(1, "action", 'click "Button 1"').target == "Button 1", "from the cache"
    third = PartLLM()
    cached = asyncio.run(LLMInterpreter(third, model_id="m", cache_dir=tmp_path, part_items=16).read_case(edited))
    assert third.users == [] and cached.source == "cache"


def test_a_long_case_through_the_fake_model_server_falls_back_only_for_the_refused_step(tmp_path: Path) -> None:
    from testzeus_hercules.kernel.settings import LLMSettings
    from testzeus_hercules.llm.capabilities import Capabilities
    from testzeus_hercules.llm.client import ChatClient
    from testzeus_hercules.llm.providers import resolve_endpoint
    from testzeus_hercules.llm.structured import StructuredLLM

    def respond(body: dict[str, Any]) -> httpx.Response:
        keys = _keys(body["messages"][-1]["content"])
        if "step_40_action" in keys:
            return httpx.Response(400, json={"error": {"message": "response_format schema exceeds the limit"}})
        return completion_response({"content": json.dumps(_answer(keys))})

    server = FakeServer(kind="generic", models=["m"], responder=respond)
    capabilities = Capabilities(server_version="1", model_id="m", context_tokens=32768, supports_tools=False, supports_tool_choice=False, supports_json_schema=True, image_mode="none")

    async def run() -> Any:
        async with server.client() as http:
            client = ChatClient(resolve_endpoint(LLMSettings(), env={}), model="m", capabilities=capabilities, timeout_s=5.0, max_retries=0, http=http)
            structured = StructuredLLM(client, capabilities=capabilities)
            return await StepInterpreter(LLMInterpreter(structured, model_id="m", cache_dir=tmp_path, part_items=16)).read_case(_items())

    reading = asyncio.run(run())

    requests = server.chat_requests()
    assert len(requests) == 12, "four parts; the refused one halved down to step 40 (33-40, 41-48, 33-36, 37-40, 37-38, 39-40, 39, 40)"
    assert all(request["response_format"]["json_schema"]["name"] == "CaseReadingReply" for request in requests)
    assert max(len(request["response_format"]["json_schema"]["schema"]["properties"]) for request in requests) == 96, "16 items, their flags, needs, criteria, risk and lint per call"
    unread = [number for number in range(1, STEPS + 1) if reading.get(number, "action", f'click "Button {number}"') is None]
    assert unread == [40]
    assert reading.get(49, "action", 'click "Button 49"').target == "Button 49"
