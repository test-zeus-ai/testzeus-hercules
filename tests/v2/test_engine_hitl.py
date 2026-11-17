"""The run and step graphs under a RunControl: pause/step/skip/retry, needs_human interrupts and checkpoint resume."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import pytest

from testzeus_hercules.cases.models import RunResult, Step, TestCase
from testzeus_hercules.engine.checkpoint import SqliteCheckpointer
from testzeus_hercules.engine.control import HumanPolicy, RunControl
from testzeus_hercules.engine.run_graph import ResumeError, run_case
from testzeus_hercules.engine.step_graph import StepLimits
from testzeus_hercules.kernel.run_context import ListEventSink, RecordedEvent

from tests.v2.engine_fixtures.fakes import FakeActions, ScriptedInterpreter, fills, intent
from tests.v2.test_engine_run_graph import READINGS, Harness, login_case

Reaction = Callable[[RecordedEvent, RunControl], None]


class DrivenSink(ListEventSink):
    """Records events and lets a test answer them (as the tester would) the moment they are emitted."""

    def __init__(self) -> None:
        super().__init__()
        self.control: RunControl | None = None
        self.reactions: list[Reaction] = []

    def emit(self, kind: str, /, **data: Any) -> None:
        super().emit(kind, **data)
        event = self.events[-1]
        for react in list(self.reactions):
            assert self.control is not None
            react(event, self.control)


def harness(tmp_path: Path, policy: HumanPolicy | None = None, **kwargs: Any) -> tuple[Harness, DrivenSink, RunControl]:
    h = Harness(tmp_path, **kwargs)
    sink = DrivenSink()
    control = RunControl(events=sink, policy=policy)
    sink.control = control
    h.events = sink
    h.ctx = type(h.ctx)(run_id=h.ctx.run_id, settings=h.ctx.settings, paths=h.ctx.paths, telemetry=h.ctx.telemetry, cancel=h.ctx.cancel, events=sink)
    h.deps.control = control
    return h, sink, control


def on(kind: str, reply: Callable[[RecordedEvent], dict[str, Any] | None], *, times: int = 1) -> Reaction:
    left = [times]

    def react(event: RecordedEvent, control: RunControl) -> None:
        if event.kind != kind or left[0] <= 0:
            return
        message = reply(event)
        if message is None:
            return
        left[0] -= 1
        control.submit(message)

    return react


def command(action: str) -> dict[str, Any]:
    return {"type": "control", "action": action}


def started(sink: ListEventSink) -> list[int]:
    return [event.data["step"] for event in sink.events if event.kind == "step_started"]


def test_pause_before_step_two_then_step_then_resume(tmp_path: Path) -> None:
    h, sink, control = harness(tmp_path)
    control.submit(command("step"))  # before the run: stops before step 1
    sink.reactions.append(on("paused", lambda event: command("step") if event.data["step"] == 1 else None))
    sink.reactions.append(on("paused", lambda event: command("resume") if event.data["step"] == 2 else None))

    result = h.run(login_case())

    assert result.status == "passed", result.summary_text()
    paused = [event.data["step"] for event in sink.events if event.kind == "paused"]
    assert paused == [1, 2]
    assert [step.status for step in result.steps] == ["passed"] * 4


def test_skip_marks_the_step_skipped_and_the_run_continues(tmp_path: Path) -> None:
    h, sink, control = harness(tmp_path)
    control.submit(command("pause"))
    answers = iter([command("step"), command("skip"), command("resume")])
    sink.reactions.append(on("paused", lambda event: next(answers), times=3))

    result = h.run(login_case())

    assert [step.status for step in result.steps] == ["passed", "skipped", "passed", "passed"]
    assert result.steps[1].actual == "skipped by the tester"
    assert result.status == "failed"  # a skipped step is not a pass
    assert 2 not in started(sink)


def test_retry_runs_the_previous_step_again(tmp_path: Path) -> None:
    h, sink, control = harness(tmp_path)
    control.submit(command("pause"))
    answers = iter([command("step"), command("step"), command("retry"), command("resume")])
    sink.reactions.append(on("paused", lambda event: next(answers), times=4))

    result = h.run(login_case())

    assert result.status == "passed", result.summary_text()
    assert started(sink) == [1, 2, 2, 3, 4]
    assert [step.index for step in result.steps] == [1, 2, 3, 4]
    fills = [call for call in h.actions.calls if call[0] == "fill"]
    assert len(fills) == 2


def test_destructive_click_waits_for_approval(tmp_path: Path) -> None:
    h, sink, _ = harness(tmp_path, HumanPolicy(confirm_destructive=True, destructive_terms=("sign in",)))
    sink.reactions.append(on("needs_approval", lambda event: {"type": "approval", "id": event.data["id"], "decision": "approve"}))

    result = h.run(login_case())

    assert result.status == "passed", result.summary_text()
    request = next(event.data for event in sink.events if event.kind == "needs_approval")
    assert request["kind"] == "destructive" and request["step"] == 3 and "Sign in" in request["reason"]
    assert request["screenshot"].endswith(("plain.jpg", "som.jpg"))
    clicks = [call for call in h.actions.calls if call[0] == "click"]
    assert len(clicks) == 1


def test_rejected_destructive_click_blocks_the_step(tmp_path: Path) -> None:
    h, sink, _ = harness(tmp_path, HumanPolicy(confirm_destructive=True, destructive_terms=("sign in",)))
    sink.reactions.append(on("needs_approval", lambda event: {"type": "approval", "decision": "reject"}))

    result = h.run(login_case())

    assert result.status == "failed"
    assert [step.status for step in result.steps] == ["passed", "passed", "blocked", "skipped"]
    assert "rejected" in (result.steps[2].actual or "")
    assert not [call for call in h.actions.calls if call[0] == "click"]


def test_edit_replaces_the_step_text_and_runs_it_again(tmp_path: Path) -> None:
    h, sink, _ = harness(tmp_path, HumanPolicy(confirm_destructive=True, destructive_terms=("sign in",)))
    sink.reactions.append(on("needs_approval", lambda event: {"type": "approval", "decision": "edit", "step_text": 'check "Remember me"'}))

    result = h.run(login_case())

    assert result.steps[2].action == 'check "Remember me"'
    assert result.steps[2].status == "passed"
    assert [call for call in h.actions.calls if call[0] == "check"] == [("check", 5)]
    assert started(sink).count(3) == 2
    assert [text for text, page in h.interpreter.calls] == ['check "Remember me"'], "an edited step is read again, on the page"
    assert h.interpreter.calls[0][1], "with the page's controls"


def test_unanswered_approval_times_out_and_fails_with_the_reason(tmp_path: Path) -> None:
    h, sink, _ = harness(tmp_path, HumanPolicy(confirm_destructive=True, destructive_terms=("sign in",), approval_timeout_s=0.2))

    result = h.run(login_case())

    assert result.steps[2].status == "blocked"
    assert "no answer within 0.2 s" in (result.steps[2].actual or "")


def test_waiting_for_a_person_does_not_count_against_the_step_timeout(tmp_path: Path) -> None:
    h, sink, _ = harness(tmp_path, HumanPolicy(confirm_destructive=True, destructive_terms=("sign in",)), limits=StepLimits(step_timeout_s=0.5, assert_timeout_ms=10))

    def approve_later(event: RecordedEvent, control: RunControl) -> None:
        if event.kind == "needs_approval":
            asyncio.get_running_loop().call_later(1.0, control.submit, {"type": "approval", "decision": "approve"})

    sink.reactions.append(approve_later)

    result = h.run(login_case())

    assert result.status == "passed", result.summary_text()


def test_failed_step_asks_while_the_tester_is_debugging(tmp_path: Path) -> None:
    h, sink, control = harness(tmp_path, actions=FakeActions(text="Welcome, alice", fail_mds=[6]), limits=StepLimits(assert_timeout_ms=10, max_recoveries=0))
    control.submit(command("step"))
    sink.reactions.append(on("paused", lambda event: command("resume")))
    answers = iter([{"type": "approval", "decision": "approve"}, {"type": "approval", "decision": "reject"}])
    sink.reactions.append(on("needs_approval", lambda event: next(answers), times=2))

    result = h.run(login_case())

    requests = [event.data for event in sink.events if event.kind == "needs_approval"]
    assert [request["kind"] for request in requests] == ["recovery_exhausted", "recovery_exhausted"]
    assert started(sink).count(3) == 2
    assert result.steps[2].status == "failed"
    assert result.status == "failed"


def test_draft_case_waits_for_approval_and_a_rejection_cancels(tmp_path: Path) -> None:
    h, sink, _ = harness(tmp_path)
    sink.reactions.append(on("needs_approval", lambda event: {"type": "approval", "decision": "reject"}))
    case = login_case().model_copy(update={"status": "draft"})

    result = h.run(case)

    assert result.status == "cancelled"
    assert any("draft" in hint for hint in result.hints)
    assert started(sink) == []


def test_draft_case_runs_after_approval(tmp_path: Path) -> None:
    h, sink, _ = harness(tmp_path)
    sink.reactions.append(on("needs_approval", lambda event: {"type": "approval", "decision": "approve"}))

    result = h.run(login_case().model_copy(update={"status": "draft"}))

    assert result.status == "passed"


# --------------------------------------------------------------------------- checkpoint resume


class WorkerDied(BaseException):
    """Stands in for the worker process dying mid-step (not an Exception: nothing may catch it)."""


def test_resume_continues_from_the_last_completed_step(tmp_path: Path) -> None:
    db = tmp_path / "checkpoints" / "r_test_1.db"

    class DyingActions(FakeActions):
        async def click(self, locator: Any, *, timeout_ms: int = 0) -> Any:
            raise WorkerDied()

    first = Harness(tmp_path, actions=DyingActions(text="Welcome, alice"))
    first.deps.checkpointer = SqliteCheckpointer(db)
    with pytest.raises(WorkerDied):
        first.run(login_case())
    first.deps.checkpointer.close()
    assert not (first.run_dir / "run.json").exists()

    second = Harness(tmp_path)
    second.deps.checkpointer = SqliteCheckpointer(db)
    result: RunResult = asyncio.run(run_case(login_case(), second.ctx, second.deps, resume=True))
    second.deps.checkpointer.close()

    assert result.status == "passed", result.summary_text()
    assert [step.index for step in result.steps] == [1, 2, 3, 4]
    assert [step.status for step in result.steps] == ["passed"] * 4
    assert [event.data["step"] for event in second.events.events if event.kind == "step_started"] == [3, 4]
    resumed = next(event for event in second.events.events if event.kind == "run_resumed")
    assert resumed.data["from_step"] == 3
    assert not [call for call in second.actions.calls if call[0] in ("open_url", "fill")]  # steps 1-2 were not repeated
    assert len(second.launcher.opened) == 1


def test_resume_keeps_the_download_and_the_values_earlier_steps_saved(tmp_path: Path) -> None:
    db = tmp_path / "checkpoints" / "r_test_1.db"
    readings = {
        **READINGS,
        'I download "orders.csv" from "/reports/orders.csv"': intent("download", url="/reports/orders.csv", value="orders.csv"),
        'fill "{{downloaded_file_name}}" in "Username"': fills("Username", "{{downloaded_file_name}}"),
        "the downloaded file has 2 rows": intent("assert_file_rows", expected="2"),
    }
    case = TestCase(
        id="report",
        title="Report",
        environment={"base_url": "https://app.test", "viewport": "1024x700"},
        steps=[
            Step(action="Open /login"),
            Step(action='I download "orders.csv" from "/reports/orders.csv"'),
            Step(action='click "Sign in"'),
            Step(action='fill "{{downloaded_file_name}}" in "Username"'),
            Step(action="verify", expected="the downloaded file has 2 rows"),
        ],
    )

    class DyingActions(FakeActions):
        async def click(self, locator: Any, *, timeout_ms: int = 0) -> Any:
            raise WorkerDied()

    first = Harness(tmp_path, actions=DyingActions(text="Welcome, alice"), interpreter=ScriptedInterpreter(readings))
    first.deps.checkpointer = SqliteCheckpointer(db)
    with pytest.raises(WorkerDied):
        first.run(case)
    first.deps.checkpointer.close()

    second = Harness(tmp_path, interpreter=ScriptedInterpreter(readings))
    second.deps.checkpointer = SqliteCheckpointer(db)
    result: RunResult = asyncio.run(run_case(case, second.ctx, second.deps, resume=True))
    second.deps.checkpointer.close()

    assert result.status == "passed", result.summary_text()
    assert [call[2] for call in second.actions.calls if call[0] == "fill"] == ["orders.csv"]
    assert not [call for call in second.actions.calls if call[0] == "download_from_url"]


def test_resume_without_a_checkpoint_is_an_error(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.deps.checkpointer = SqliteCheckpointer(tmp_path / "empty.db")
    with pytest.raises(ResumeError):
        asyncio.run(run_case(login_case(), h.ctx, h.deps, resume=True))
    h.deps.checkpointer.close()


def test_a_finished_run_drops_its_checkpoint(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.deps.checkpointer = SqliteCheckpointer(tmp_path / "c.db")
    h.run(login_case())
    assert h.deps.checkpointer.get_tuple({"configurable": {"thread_id": h.ctx.run_id}}) is None
    h.deps.checkpointer.close()


def test_no_control_and_no_checkpointer_changes_nothing(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    case = TestCase(id="x", title="x", environment={"base_url": "https://app.test"}, steps=[Step(action="Open /login")])
    assert h.run(case).status == "passed"
