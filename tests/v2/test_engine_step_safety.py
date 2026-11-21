"""Step graph safety: an assertion before other actions does not drop them, a step that loops or fails outside its
nodes ends as an error (never a crash), an unread action is never guessed as a click, and stopping a step stops its work."""

from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any

import pytest
from langgraph.errors import GraphRecursionError

from testzeus_hercules.cases.models import Step
from testzeus_hercules.engine import step_graph
from testzeus_hercules.engine.step_graph import StepLimits, _within

from tests.v2.engine_fixtures.fakes import FakeActions, FakePerceiver, LabelDecider, clicks, intent
from tests.v2.test_engine_step_graph import Harness, on_page


def test_an_assertion_before_a_click_checks_it_and_still_clicks(tmp_path: Path) -> None:
    text = "the sign in button shows, then remember me"
    h = Harness(tmp_path, interpreter=on_page(text, intent("assert_visible", "Sign in", role="button"), clicks("Remember me")))

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert ("click", 5) in h.actions.calls
    assert [(entry.check, entry.verdict) for entry in outcome.ledger][:1] == [("visible", "passed")]


def test_a_failed_assertion_before_a_click_does_not_click(tmp_path: Path) -> None:
    text = "the sign in button shows, then remember me"
    h = Harness(tmp_path, actions=FakeActions(invisible_mds=[6]), interpreter=on_page(text, intent("assert_visible", "Sign in", role="button"), clicks("Remember me")))

    outcome = h.run(Step(action=text))

    assert outcome.status != "passed"
    assert ("click", 5) not in h.actions.calls


def test_an_unread_action_is_not_guessed_as_a_click(tmp_path: Path) -> None:
    text = "do something with sign in"
    # The decider picks the element but chooses no action for it (no confidence).
    h = Harness(tmp_path, decider=LabelDecider(tool="click", tool_confidence=0.0), interpreter=on_page(text, intent("unknown", "Sign in")))

    outcome = h.run(Step(action=text))

    assert outcome.status != "passed"
    assert not [call for call in h.actions.calls if call[0] == "click"]


def test_the_move_budget_covers_the_default_limits() -> None:
    limits = StepLimits()
    assert limits.recursion_limit >= 8 * limits.max_step_actions * (1 + limits.max_recoveries)
    assert StepLimits(max_step_actions=20).recursion_limit > limits.recursion_limit


@pytest.mark.parametrize(("error", "error_class"), [(GraphRecursionError("too many moves"), "StepLoop"), (KeyError("boom"), "KeyError")])
def test_a_graph_failure_ends_the_step_as_an_error(tmp_path: Path, error: Exception, error_class: str) -> None:
    h = Harness(tmp_path)

    async def broken(*_: Any, **__: Any) -> Any:
        raise error

    h.runner._graph = types.SimpleNamespace(ainvoke=broken)
    outcome = asyncio.run(h.runner.run(1, Step(action='click "Sign in"')))

    assert outcome.status == "error"
    assert outcome.error_class == error_class
    assert outcome.evidence, "failure evidence is still taken"


def test_failure_evidence_gives_up_on_a_hung_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Hung(FakePerceiver):
        async def snapshot(self, page: Any, seq: int, focus_terms: Any = ()) -> Any:
            await asyncio.sleep(60)

    class HungActions(FakeActions):
        async def screenshot_jpeg(self, page: Any) -> bytes | None:
            await asyncio.sleep(60)
            return None

    monkeypatch.setattr(step_graph, "_EVIDENCE_TIMEOUT_S", 0.05)
    h = Harness(tmp_path, perceiver=Hung(), actions=HungActions())

    async def go() -> tuple[str, ...]:
        return await asyncio.wait_for(h.runner._failure_evidence(1, ()), timeout=5)

    assert asyncio.run(go()) == ()


def test_a_drop_down_that_cannot_be_read_leaves_the_rest_of_the_page_listed(tmp_path: Path) -> None:
    class Broken(FakePerceiver):
        async def resolve(self, page: Any, snapshot: Any, md: int) -> Any:
            if md == 7:
                raise RuntimeError("detached")
            return await super().resolve(page, snapshot, md)

    h = Harness(tmp_path, perceiver=Broken(), actions=FakeActions(options={7: ["English", "French"]}))

    labels, _ = asyncio.run(h.runner._page_view())

    assert 'combobox "Language"' in labels and 'button "Sign in"' in labels


def test_cancelling_the_awaiter_stops_the_step_work() -> None:
    async def go() -> bool:
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def work() -> Any:
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()

        waiter = asyncio.ensure_future(_within(work(), 30.0, types.SimpleNamespace(human_wait_s=0.0)))  # type: ignore[arg-type]
        await started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        return stopped.is_set()

    assert asyncio.run(go())
