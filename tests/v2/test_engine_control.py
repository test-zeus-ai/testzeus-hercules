"""RunControl: pause, resume, step, skip and retry at step and action boundaries; approvals with timeouts."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from testzeus_hercules.engine.control import DEFAULT_DESTRUCTIVE_TERMS, DestructiveGuard, HumanPolicy, RetryStep, RunControl, SkipStep, StepBlocked
from testzeus_hercules.kernel.run_context import CancelToken, ListEventSink, RunCancelledError


def kinds(sink: ListEventSink) -> list[str]:
    return [event.kind for event in sink.events]


async def until(predicate: Any, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


def make(policy: HumanPolicy | None = None) -> tuple[RunControl, ListEventSink]:
    sink = ListEventSink()
    return RunControl(events=sink, policy=policy or HumanPolicy()), sink


# --------------------------------------------------------------------------- destructive guard


@pytest.mark.parametrize(
    ("name", "role", "hit"),
    [
        ("Delete account", "button", "delete"),
        ("Remove", "button", "remove"),
        ("Cancel order #12", "link", "cancel order"),
        ("Pay $42.00", "button", "pay"),
        ("Submit payment", "button", "submit payment"),
        ("Transfer funds", "button", "transfer"),
        ("PayPal", "button", None),
        ("Payment details", "link", None),
        ("Add to cart", "button", None),
        ("Cancel", "button", None),
        ("Delete", "textbox", None),
    ],
)
def test_destructive_guard_matches_whole_words_on_clickable_roles(name: str, role: str, hit: str | None) -> None:
    assert DestructiveGuard().matches(name, role) == hit


def test_destructive_guard_terms_are_configurable() -> None:
    guard = DestructiveGuard(("archive",))
    assert guard.matches("Archive project", "button") == "archive"
    assert guard.matches("Delete", "button") is None
    assert "delete" in DEFAULT_DESTRUCTIVE_TERMS


# --------------------------------------------------------------------------- pause, resume, step


def test_no_commands_never_blocks() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        assert (await control.before_step(1, "Open /")).kind == "run"
        await control.before_action(1, "click")

    asyncio.run(main())
    assert sink.events == []


def test_pause_stops_before_the_next_step_until_resumed() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "pause"})
        task = asyncio.create_task(control.before_step(2, "Click Sign in"))
        await until(lambda: "paused" in kinds(sink))
        assert control.paused and not task.done()
        control.submit({"type": "control", "action": "resume"})
        assert (await asyncio.wait_for(task, 1)).kind == "run"

    asyncio.run(main())
    assert kinds(sink) == ["paused", "resumed"]
    assert sink.events[0].data["step"] == 2 and sink.events[0].data["where"] == "step"
    assert sink.events[1].data["state"] == "running"


def test_pause_also_stops_before_an_action_inside_a_step() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "pause"})
        task = asyncio.create_task(control.before_action(3, 'click "Remove"'))
        await until(lambda: "paused" in kinds(sink))
        control.submit({"type": "control", "action": "resume"})
        await asyncio.wait_for(task, 1)

    asyncio.run(main())
    assert sink.events[0].data == {"step": 3, "where": "action", "action": 'click "Remove"', "state": "paused"}


def test_step_runs_exactly_one_step_then_pauses_again() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "pause"})
        first = asyncio.create_task(control.before_step(2, "two"))
        await until(lambda: kinds(sink).count("paused") == 1)
        control.submit({"type": "control", "action": "step"})
        assert (await asyncio.wait_for(first, 1)).kind == "run"
        await control.before_action(2, "click")  # stepping: actions of the stepped step never pause
        second = asyncio.create_task(control.before_step(3, "three"))
        await until(lambda: kinds(sink).count("paused") == 2)
        assert not second.done()
        control.submit({"type": "control", "action": "resume"})
        await asyncio.wait_for(second, 1)

    asyncio.run(main())
    assert [event.data.get("step") for event in sink.events if event.kind == "paused"] == [2, 3]


def test_step_while_running_pauses_at_the_next_step_boundary() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        assert (await control.before_step(1, "one")).kind == "run"
        control.submit({"type": "control", "action": "step"})
        await control.before_action(1, "click")
        task = asyncio.create_task(control.before_step(2, "two"))
        await until(lambda: "paused" in kinds(sink))
        control.submit({"type": "control", "action": "resume"})
        await asyncio.wait_for(task, 1)

    asyncio.run(main())


def test_skip_while_paused_skips_the_step_and_stays_paused() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "pause"})
        task = asyncio.create_task(control.before_step(2, "two"))
        await until(lambda: "paused" in kinds(sink))
        control.submit({"type": "control", "action": "skip"})
        assert (await asyncio.wait_for(task, 1)).kind == "skip"
        assert control.paused

    asyncio.run(main())


def test_retry_while_paused_reruns_the_previous_step_then_pauses() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "pause"})
        task = asyncio.create_task(control.before_step(3, "three"))
        await until(lambda: "paused" in kinds(sink))
        control.submit({"type": "control", "action": "retry"})
        assert (await asyncio.wait_for(task, 1)).kind == "retry"
        assert (await asyncio.wait_for(control.before_step(2, "two again"), 1)).kind == "run"
        again = asyncio.create_task(control.before_step(3, "three"))
        await until(lambda: kinds(sink).count("paused") == 2)
        control.submit({"type": "control", "action": "resume"})
        await asyncio.wait_for(again, 1)

    asyncio.run(main())


def test_retry_before_the_first_step_is_ignored() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "pause"})
        task = asyncio.create_task(control.before_step(1, "one", can_retry=False))
        await until(lambda: "paused" in kinds(sink))
        control.submit({"type": "control", "action": "retry"})
        await until(lambda: "control_ignored" in kinds(sink))
        control.submit({"type": "control", "action": "resume"})
        assert (await asyncio.wait_for(task, 1)).kind == "run"

    asyncio.run(main())


def test_skip_and_retry_at_an_action_raise_into_the_step() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "pause"})
        task = asyncio.create_task(control.before_action(2, "click"))
        await until(lambda: "paused" in kinds(sink))
        control.submit({"type": "control", "action": "skip"})
        with pytest.raises(SkipStep):
            await asyncio.wait_for(task, 1)
        task = asyncio.create_task(control.before_action(3, "click"))
        await until(lambda: kinds(sink).count("paused") == 2)
        control.submit({"type": "control", "action": "retry"})
        with pytest.raises(RetryStep):
            await asyncio.wait_for(task, 1)

    asyncio.run(main())


def test_commands_from_another_thread_and_before_binding() -> None:
    control, sink = make()
    control.submit({"type": "control", "action": "pause"})  # before the run started

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        task = asyncio.create_task(control.before_step(1, "one"))
        await until(lambda: "paused" in kinds(sink))
        threading.Thread(target=control.submit, args=({"type": "control", "action": "resume"},)).start()
        await asyncio.wait_for(task, 1)

    asyncio.run(main())


def test_cancel_while_paused_raises_run_cancelled() -> None:
    control, sink = make()
    token = CancelToken()

    async def main() -> None:
        control.bind(asyncio.get_running_loop(), cancel=token)
        control.submit({"type": "control", "action": "pause"})
        task = asyncio.create_task(control.before_step(1, "one"))
        await until(lambda: "paused" in kinds(sink))
        token.cancel("stop")
        with pytest.raises(RunCancelledError):
            await asyncio.wait_for(task, 1)

    asyncio.run(main())


def test_unknown_messages_are_reported_not_raised() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "explode"})
        control.submit({"type": "nonsense"})
        control.submit("not a dict")  # type: ignore[arg-type]
        control.submit({"type": "control", "action": "skip"})  # not paused

    asyncio.run(main())
    assert kinds(sink) == ["control_ignored"] * 4


def test_human_wait_time_is_measured() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "pause"})
        task = asyncio.create_task(control.before_step(1, "one"))
        await asyncio.sleep(0.2)
        assert control.human_wait_s >= 0.15  # counted while still waiting
        control.submit({"type": "control", "action": "resume"})
        await task

    asyncio.run(main())
    assert control.human_wait_s >= 0.2


# --------------------------------------------------------------------------- approvals


def test_destructive_click_needs_approval_when_enabled() -> None:
    control, sink = make(HumanPolicy(confirm_destructive=True))

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        task = asyncio.create_task(control.confirm_destructive(4, 'click the "Remove" button', name="Remove", role="button", screenshot="hercules://runs/r/steps/4/plain.jpg"))
        await until(lambda: "needs_approval" in kinds(sink))
        request = sink.events[-1].data
        control.submit({"type": "approval", "id": request["id"], "decision": "approve"})
        await asyncio.wait_for(task, 1)
        # approved once for this element in this step: not asked again
        await asyncio.wait_for(control.confirm_destructive(4, "again", name="Remove", role="button"), 1)

    asyncio.run(main())
    request = next(event.data for event in sink.events if event.kind == "needs_approval")
    assert request["kind"] == "destructive" and request["step"] == 4
    assert "Remove" in request["reason"] and request["screenshot"].endswith("plain.jpg")
    assert kinds(sink).count("needs_approval") == 1
    assert sink.events[-1].kind == "approval_result" and sink.events[-1].data["decision"] == "approve"


def test_destructive_check_is_off_by_default() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        await asyncio.wait_for(control.confirm_destructive(1, "click", name="Delete", role="button"), 1)

    asyncio.run(main())
    assert sink.events == []


def test_reject_blocks_and_edit_retries_with_new_text() -> None:
    control, sink = make(HumanPolicy(confirm_destructive=True))

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        task = asyncio.create_task(control.confirm_destructive(2, "click", name="Delete", role="button"))
        await until(lambda: "needs_approval" in kinds(sink))
        control.submit({"type": "approval", "decision": "reject"})  # the only pending approval
        with pytest.raises(StepBlocked, match="rejected"):
            await asyncio.wait_for(task, 1)
        task = asyncio.create_task(control.confirm_destructive(3, "click", name="Delete", role="button"))
        await until(lambda: kinds(sink).count("needs_approval") == 2)
        control.submit({"type": "approval", "decision": "edit", "step_text": 'Click "Keep"'})
        with pytest.raises(RetryStep) as raised:
            await asyncio.wait_for(task, 1)
        assert raised.value.text == 'Click "Keep"'

    asyncio.run(main())


def test_approval_times_out_as_a_rejection_with_a_reason() -> None:
    control, sink = make(HumanPolicy(confirm_destructive=True, approval_timeout_s=0.1))

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        with pytest.raises(StepBlocked, match="no answer within"):
            await asyncio.wait_for(control.confirm_destructive(2, "click", name="Delete", role="button"), 2)

    asyncio.run(main())
    result = sink.events[-1]
    assert result.kind == "approval_result" and result.data["decision"] == "reject" and result.data["timed_out"] is True


def test_failure_hold_only_when_asked_or_debugging() -> None:
    control, sink = make()

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        assert await control.after_failure(2, "click", "TargetNotFound", "no element") is None
        control.submit({"type": "control", "action": "step"})  # the tester is debugging this run
        task = asyncio.create_task(control.after_failure(2, "click", "AssertionFailed", "expected text"))
        await until(lambda: "needs_approval" in kinds(sink))
        control.submit({"type": "approval", "decision": "approve"})
        answer = await asyncio.wait_for(task, 1)
        assert answer is not None and answer.decision == "approve"

    asyncio.run(main())
    assert next(event for event in sink.events if event.kind == "needs_approval").data["kind"] == "recovery_exhausted"


def test_ask_on_failure_policy_only_for_interaction_failures() -> None:
    control, sink = make(HumanPolicy(ask_on_failure=True))

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        assert await control.after_failure(2, "verify", "AssertionFailed", "expected text") is None
        task = asyncio.create_task(control.after_failure(2, "click", "TargetNotFound", "nothing matched"))
        await until(lambda: "needs_approval" in kinds(sink))
        control.submit({"type": "approval", "decision": "edit", "step_text": "Click Save"})
        answer = await asyncio.wait_for(task, 1)
        assert answer is not None and answer.decision == "edit" and answer.step_text == "Click Save"

    asyncio.run(main())


def test_draft_approval() -> None:
    control, sink = make()

    async def main() -> bool:
        control.bind(asyncio.get_running_loop())
        task = asyncio.create_task(control.approve_draft(5))
        await until(lambda: "needs_approval" in kinds(sink))
        control.submit({"type": "approval", "decision": "approve"})
        return await asyncio.wait_for(task, 1)

    assert asyncio.run(main()) is True
    assert sink.events[0].data["kind"] == "draft"


def test_edit_without_text_and_answers_without_a_pending_request_are_ignored() -> None:
    control, sink = make(HumanPolicy(confirm_destructive=True))

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "approval", "decision": "approve"})
        assert kinds(sink) == ["control_ignored"]
        task = asyncio.create_task(control.confirm_destructive(2, "click", name="Delete", role="button"))
        await until(lambda: "needs_approval" in kinds(sink))
        control.submit({"type": "approval", "decision": "edit", "step_text": "  "})
        await until(lambda: kinds(sink).count("control_ignored") == 2)
        assert not task.done()
        control.submit({"type": "approval", "decision": "approve"})
        await asyncio.wait_for(task, 1)

    asyncio.run(main())


def test_detach_resumes_and_rejects_so_the_run_never_waits_for_nobody() -> None:
    control, sink = make(HumanPolicy(confirm_destructive=True))

    async def main() -> None:
        control.bind(asyncio.get_running_loop())
        control.submit({"type": "control", "action": "pause"})
        paused = asyncio.create_task(control.before_step(1, "one"))
        await until(lambda: "paused" in kinds(sink))
        asking = asyncio.create_task(control.confirm_destructive(1, "click", name="Delete", role="button"))
        await until(lambda: "needs_approval" in kinds(sink))
        control.submit({"type": "detach"})
        assert (await asyncio.wait_for(paused, 1)).kind == "run"
        with pytest.raises(StepBlocked):
            await asyncio.wait_for(asking, 1)
        with pytest.raises(StepBlocked):  # later questions are not asked at all
            await asyncio.wait_for(control.confirm_destructive(2, "click", name="Delete", role="button"), 1)

    asyncio.run(main())
