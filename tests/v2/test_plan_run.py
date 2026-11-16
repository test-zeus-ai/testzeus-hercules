"""Running against the plan: criteria checked after each action (author vs inferred), recovery driven by an unmet criterion,
strict mode, plan review (approve, edit, reject), amendments, plan.json and the result's plan summary."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

from testzeus_hercules.cases.models import Step, TestCase
from testzeus_hercules.cases.plan import Criterion, Plan
from testzeus_hercules.engine.control import RunControl
from testzeus_hercules.engine.criteria import evaluate
from testzeus_hercules.engine.run_graph import RunPlanOptions
from testzeus_hercules.kernel.run_context import ListEventSink

from tests.v2.engine_fixtures.fakes import FakeActions, FakeElement, FakeSnapshot, LabelDecider, ScriptedInterpreter, clicks, fills, login_elements
from tests.v2.test_engine_run_graph import READINGS, Harness

WELCOME = {"after": 1, "origin": "inferred", "check": "text_visible", "expected": "Welcome", "target": None, "value": None, "state": None, "count": None}


def case(*steps: Step) -> TestCase:
    return TestCase(id="signin", title="Sign in", environment={"base_url": "https://app.test"}, steps=list(steps) or [Step(action="Open /login"), Step(action='click "Sign in"')])


class SlowStartActions(FakeActions):
    """The first click is ignored by the page (it is still loading); the second one signs in."""

    def __init__(self) -> None:
        super().__init__(text="Sign in to Acme")
        self.clicks = 0

    async def click(self, locator: Any, *, timeout_ms: int = 0) -> Any:
        result = await super().click(locator, timeout_ms=timeout_ms)
        self.clicks += 1
        if self.clicks >= 2:
            self.text = "Welcome, alice"
        return result


def _plan(h: Harness) -> dict[str, Any]:
    return json.loads((h.run_dir / "plan.json").read_text())


def _results(h: Harness) -> dict[str, dict[str, Any]]:
    return {result["criterion"]: result for result in _plan(h)["results"]}


def _harness(
    tmp_path: Path, *, extras: dict[str, Any] | None = None, remaining: tuple[Any, ...] = (), actions: FakeActions | None = None, plan: RunPlanOptions | None = None, **kwargs: Any
) -> Harness:
    interpreter = ScriptedInterpreter(READINGS, extras=extras or {}, remaining=remaining)
    h = Harness(tmp_path, actions=actions, interpreter=interpreter, **kwargs)
    if plan is not None:
        h.deps.plan = plan
    return h


# --------------------------------------------------------------------------- criteria during the run


def test_an_unmet_inferred_criterion_drives_a_recovery_with_the_expectation(tmp_path: Path) -> None:
    actions = SlowStartActions()
    h = _harness(tmp_path, extras={'click "Sign in"': {"criteria": [WELCOME]}}, remaining=([clicks("Sign in")],), actions=actions)

    result = h.run(case())

    assert result.status == "passed", result.summary_text()
    assert actions.clicks == 2, "the click the page ignored was done again"
    context = h.interpreter.remaining_context[0]
    assert context["expected"] == ('the page shows "Welcome"',)
    criterion = _results(h)["2.1.c1"]
    assert (criterion["origin"], criterion["status"], criterion["recovered"]) == ("inferred", "passed", True)
    (amendment,) = [item for item in _plan(h)["amendments"] if item["kind"] == "recovery"]
    assert amendment["step"] == 2 and amendment["source"] == "model" and 'expected: the page shows "Welcome"' in amendment["reason"]
    assert result.plan is not None and (result.plan.recoveries, result.plan.amendments, result.plan.inferred) == (1, 1, 1)
    stages = [json.loads(line)["stage"] for line in (h.run_dir / "steps" / "2" / "plan.jsonl").read_text().splitlines()]
    assert stages.count("criterion") == 2 and "recover" in stages and "amend" in stages


def test_an_inferred_criterion_still_unmet_is_a_warning_and_fails_only_in_strict_mode(tmp_path: Path) -> None:
    extras = {'click "Sign in"': {"criteria": [WELCOME]}}
    lenient = _harness(tmp_path / "a", extras=extras, actions=FakeActions(text="Sign in to Acme"))
    passed = lenient.run(case())
    strict = _harness(tmp_path / "b", extras=extras, actions=FakeActions(text="Sign in to Acme"), plan=RunPlanOptions(strict=True))
    failed = strict.run(case())

    assert passed.status == "passed", passed.summary_text()
    assert any('plan criterion not met (inferred): the page shows "Welcome"' in note for note in passed.steps[1].notes)
    assert any(hint.startswith("plan: 1 inferred criterion(s) not met") for hint in passed.hints)
    assert _results(lenient)["2.1.c1"]["status"] == "unverified" and passed.plan is not None and passed.plan.unverified == 1
    assert failed.status == "failed" and failed.steps[1].status == "unverified"
    assert failed.plan is not None and failed.plan.strict


def test_a_failing_author_criterion_fails_the_step(tmp_path: Path) -> None:
    author = {**WELCOME, "origin": "author", "expected": "Sign in"}  # the step's own words: an author criterion
    h = _harness(tmp_path, extras={'click "Sign in"': {"criteria": [{**author, "check": "text_absent"}]}}, actions=FakeActions(text="Sign in to Acme"))

    result = h.run(case())

    assert result.status == "failed" and result.steps[1].status == "failed"
    assert 'expected (plan) the page does not show "Sign in"' in (result.steps[1].actual or "")
    assert _results(h)["2.1.c1"]["status"] == "failed"
    assert result.assertions.failed == 1


def test_a_risky_action_is_never_repeated_to_meet_an_inferred_criterion(tmp_path: Path) -> None:
    extras = {'click "Sign in"': {"criteria": [WELCOME], "risk": {"flags": ["irreversible"], "reason": "submits the form"}}}
    actions = SlowStartActions()
    h = _harness(tmp_path, extras=extras, remaining=([clicks("Sign in")],), actions=actions)

    result = h.run(case())

    assert result.status == "passed" and actions.clicks == 1
    assert h.interpreter.remaining_calls == []
    assert "never repeated" in (_results(h)["2.1.c1"]["actual"] or "")


def test_outcome_checks_are_the_plans_author_criteria(tmp_path: Path) -> None:
    h = _harness(tmp_path, actions=FakeActions(text="Welcome, alice"))
    result = h.run(case(Step(action="Open /login"), Step(action='click "Sign in"'), Step(action="verify", expected='I should see "Welcome"')))

    assert result.status == "passed"
    assert _results(h)["3.1.c1"] == {**_results(h)["3.1.c1"], "origin": "author", "check": "text_visible", "status": "passed"}
    spec = result.automation
    assert spec is not None


def test_the_export_asserts_author_criteria_and_notes_inferred_ones(tmp_path: Path) -> None:
    extras = {'click "Sign in"': {"criteria": [WELCOME, {**WELCOME, "origin": "author", "check": "text_absent", "expected": "Sign in"}]}}
    h = _harness(tmp_path, extras=extras, actions=FakeActions(text="Welcome, alice"))
    result = h.run(case())
    assert result.status == "passed", result.summary_text()
    step = result.automation.steps[1] if result.automation else None
    assert step is not None
    assert any(check.kind == "not_text" and check.expected == "Sign in" for check in step.checks)
    assert any(note.startswith('plan (inferred, not asserted): the page shows "Welcome"') for note in step.notes)
    from testzeus_hercules.export import export_run

    code = export_run(result, "playwright-py").code
    assert "plan (inferred, not asserted)" in code and 'not_to_contain_text("Sign in"' in code


# --------------------------------------------------------------------------- strict mode, review, given plans


def test_making_the_plan_does_not_count_against_the_case_timeout(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    reader = h.interpreter.read_case

    async def slow_read(items: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(1.2)  # a local model reading the case for the first time
        return await reader(items, **kwargs)

    h.interpreter.read_case = slow_read  # type: ignore[method-assign]
    quick = case().model_copy(update={"timeout_s": 1})  # shorter than the planning (below the minimum a case file may set)

    result = h.run(quick)

    assert result.status == "passed", result.summary_text()


def test_strict_mode_stops_a_run_with_lint_errors_before_the_browser_starts(tmp_path: Path) -> None:
    lint = {"lint": [{"severity": "error", "kind": "suspicious_value", "message": "the value is a Markdown link", "suggestion": "write the address"}]}
    h = _harness(tmp_path, extras={'click "Sign in"': lint}, plan=RunPlanOptions(strict=True))

    result = h.run(case())

    assert result.status == "error" and h.launcher.opened == []
    assert result.hints[0].startswith("strict mode: the plan has 1 lint error(s); step 2: the value is a Markdown link")
    assert result.plan is not None and result.plan.lint_errors == 1


class AnsweringSink(ListEventSink):
    """Answers the run's plan approval as soon as it is asked (as a person in the UI would)."""

    def __init__(self, answer: dict[str, Any], before: Any = None) -> None:
        super().__init__()
        self.answer = answer
        self.before = before
        self.control: RunControl | None = None

    def emit(self, kind: str, /, **data: Any) -> None:
        super().emit(kind, **data)
        if kind == "needs_approval" and data.get("kind") == "plan" and self.control is not None:
            if self.before is not None:
                self.before()
            self.control.submit({"type": "approval", "id": data["id"], **self.answer})


def _review(tmp_path: Path, answer: dict[str, Any], before: Any = None, *, review: str = "ask", extras: dict[str, Any] | None = None) -> tuple[Harness, Any]:
    h = _harness(tmp_path, extras=extras, plan=RunPlanOptions(review=review))  # type: ignore[arg-type]
    sink = AnsweringSink(answer, before)
    control = RunControl(events=sink)
    sink.control = control
    h.deps.control = control
    return h, sink


def test_a_plan_under_review_starts_once_approved(tmp_path: Path) -> None:
    h, sink = _review(tmp_path, {"decision": "approve"})
    result = h.run(case())
    assert result.status == "passed"
    asked = [event.data for event in sink.events if event.kind == "needs_approval"]
    assert asked and asked[0]["kind"] == "plan" and "review the run's plan" in asked[0]["reason"]
    assert _plan(h)["review"]["state"] == "approved" and result.plan is not None and result.plan.review == "approved"


def test_by_default_only_a_plan_with_lint_errors_waits_for_review(tmp_path: Path) -> None:
    lint = {"lint": [{"severity": "error", "kind": "suspicious_value", "message": "the value is a Markdown link", "suggestion": "write the address"}]}
    clean, quiet = _review(tmp_path / "clean", {"decision": "approve"}, review="errors")
    assert clean.run(case()).status == "passed"
    assert not [event for event in quiet.events if event.kind == "needs_approval"], "a clean plan runs as made"

    flagged, asked = _review(tmp_path / "flagged", {"decision": "reject"}, review="errors", extras={'click "Sign in"': lint})
    result = flagged.run(case())
    assert [event.data["kind"] for event in asked.events if event.kind == "needs_approval"] == ["plan"]
    assert result.status == "cancelled" and flagged.launcher.opened == []
    assert RunPlanOptions().review == "errors"


def test_a_strict_run_refuses_a_plan_with_lint_errors_without_asking_for_review(tmp_path: Path) -> None:
    lint = {"lint": [{"severity": "error", "kind": "suspicious_value", "message": "the value is a Markdown link", "suggestion": "write the address"}]}
    h, sink = _review(tmp_path, {"decision": "approve"}, review="errors", extras={'click "Sign in"': lint})
    h.deps.plan = RunPlanOptions(review="errors", strict=True)
    result = h.run(case())
    assert not [event for event in sink.events if event.kind == "needs_approval"], "strict mode refuses it; there is nothing to approve"
    assert result.status == "error" and h.launcher.opened == []
    assert result.hints[0].startswith("strict mode: the plan has 1 lint error(s)")


def test_a_rejected_plan_never_opens_the_browser(tmp_path: Path) -> None:
    h, _ = _review(tmp_path, {"decision": "reject"})
    result = h.run(case())
    assert result.status == "cancelled" and h.launcher.opened == []
    assert "the plan was not approved" in result.hints
    assert _plan(h)["review"]["state"] == "rejected"


def test_an_edited_plan_is_checked_and_run(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}

    def write_edit() -> None:
        plan = json.loads((holder["h"].run_dir / "plan.json").read_text())
        plan["steps"][1]["criteria"] = [{"id": "2.1.c1", "text": "the welcome text", "origin": "author", "check": "text_visible", "expected": "Welcome"}]
        (holder["h"].run_dir / "plan.edited.json").write_text(json.dumps(plan))

    h, _ = _review(tmp_path, {"decision": "edit"}, before=write_edit)
    holder["h"] = h
    result = h.run(case())

    assert result.status == "passed", result.summary_text()
    plan = _plan(h)
    assert plan["review"]["state"] == "edited" and plan["source"] == "edited"
    assert [(item["kind"], item["source"], item["plan_step"]) for item in plan["amendments"]] == [("edit", "user", "2.1")]
    assert _results(h)["2.1.c1"]["status"] == "passed" and _results(h)["2.1.c1"]["origin"] == "author"


def test_a_given_plan_is_run_and_a_plan_of_another_case_is_refused(tmp_path: Path) -> None:
    first = _harness(tmp_path / "a")
    first.run(case())
    plan = Plan.model_validate(_plan(first))
    second = _harness(tmp_path / "b", plan=RunPlanOptions(given=plan))
    second.interpreter.readings.clear()  # nothing is read: the given plan is run
    result = second.run(case())
    assert result.status == "passed" and second.interpreter.case_calls == []
    assert _plan(second)["source"] == "approved"

    other = _harness(tmp_path / "c", plan=RunPlanOptions(given=plan))
    refused = other.run(case(Step(action="Open /login"), Step(action='click "Sign in"'), Step(action='fill "alice" in "Username"')))
    assert refused.status == "error" and "the given plan cannot run" in refused.hints[0]


# --------------------------------------------------------------------------- amendments


def test_a_reorder_by_the_steps_needs_is_an_amendment(tmp_path: Path) -> None:
    readings = {**READINGS, 'fill "Canada" in "Country"': fills("Country", "Canada"), 'fill "Ontario" in "Province"': fills("Province", "Ontario")}
    interpreter = ScriptedInterpreter(readings, needs={'fill "Ontario" in "Province"': [3]})
    h = Harness(tmp_path, interpreter=interpreter)
    snapshot = FakeSnapshot(seq=0, elements=login_elements() + (FakeElement(md=20, role="combobox", name="Country"), FakeElement(md=21, role="combobox", name="Province")))
    from tests.v2.engine_fixtures.fakes import FakePerceiver

    h.deps.perceiver = FakePerceiver(snapshot)
    result = h.run(case(Step(action="Open /login"), Step(action='fill "Ontario" in "Province"'), Step(action='fill "Canada" in "Country"')))

    assert result.status == "passed", result.summary_text()
    (amendment,) = _plan(h)["amendments"]
    assert (amendment["kind"], amendment["before"], amendment["after"], amendment["source"]) == ("reorder", "1,2,3", "1,3,2", "model")


def test_a_reread_on_the_page_is_an_amendment(tmp_path: Path) -> None:
    readings = {**READINGS, 'click "Log in"': clicks("Log in")}
    interpreter = ScriptedInterpreter(readings, remaining=([clicks("Sign in")],))
    h = Harness(tmp_path, interpreter=interpreter)
    result = h.run(case(Step(action="Open /login"), Step(action='click "Log in"')))

    assert result.status == "passed", result.summary_text()
    (amendment,) = [item for item in _plan(h)["amendments"] if item["kind"] == "reread"]
    assert amendment["before"] == 'click "Log in"' and amendment["after"] == 'click "Sign in"' and amendment["plan_step"] == "2.1"


# --------------------------------------------------------------------------- the check forms


@dataclasses.dataclass(frozen=True)
class DialogSnapshot(FakeSnapshot):
    modal: str | None = None


def _check(criterion: Criterion, snapshot: Any, *, text: str = "", decider: Any = None, images: bool = False) -> Any:
    async def view() -> Any:
        return snapshot

    return asyncio.run(
        evaluate(
            criterion,
            page=object(),
            actions=FakeActions(text=text),
            snapshot=view,
            decider=decider or LabelDecider(judge=0.1),
            resolve=lambda value: value,
            redact=lambda value: value,
            images=images,
            step=1,
        )
    )


def test_a_dialog_is_open_when_the_page_shows_it_as_a_dialog() -> None:
    open_ = Criterion(id="2.1.c1", text="New Record open", origin="inferred", check="element_state", target="New Record", state="open")
    closed = open_.model_copy(update={"state": "closed"})
    shown = DialogSnapshot(seq=1, elements=login_elements(), modal="New Record")
    gone = DialogSnapshot(seq=1, elements=login_elements())
    assert (_check(open_, shown).status, _check(open_, gone).status, _check(closed, gone).status, _check(closed, shown).status) == ("passed", "failed", "passed", "failed")
    assert "open dialog(s): 'New Record'" in _check(open_, shown).actual


def test_field_values_and_states_are_read_from_the_named_element() -> None:
    elements = login_elements() + (FakeElement(md=30, role="textbox", name="Phone", value="555-0100"), FakeElement(md=31, role="radio", name="Express shipping ($15.00)", checked=True))
    snapshot = FakeSnapshot(seq=1, elements=elements)
    holds = Criterion(id="1.1.c1", text="phone", origin="inferred", check="field_value", target="Phone", value="555-0100")
    other = holds.model_copy(update={"value": "555-0199"})
    checked = Criterion(id="1.1.c2", text="express", origin="inferred", check="element_state", target="Express shipping", state="checked")
    unchecked = Criterion(id="1.1.c3", text="remember", origin="inferred", check="element_state", target="Remember me", state="checked")
    assert _check(holds, snapshot).status == "passed" and _check(other, snapshot).status == "failed"
    assert _check(checked, snapshot).status == "passed", "the one radio whose name holds the target"
    assert _check(unchecked, snapshot).status == "failed"


def test_an_element_that_is_not_named_exactly_once_is_judged_by_meaning() -> None:
    elements = login_elements() + (FakeElement(md=40, role="checkbox", name="Express A", checked=True), FakeElement(md=41, role="checkbox", name="Express B", checked=False))
    criterion = Criterion(id="1.1.c1", text="Express is checked", origin="inferred", check="element_state", target="Express", state="checked")
    decider = LabelDecider(judge=0.95)
    found = _check(criterion, FakeSnapshot(seq=1, elements=elements), text="Express A Express B", decider=decider)
    assert found.status == "passed" and found.decision is not None and found.decision["purpose"] == "criterion"
    assert decider.calls[0][1] == "criterion" and "Express A" in decider.calls[0][0].state["page_text"]
    looks = Criterion(id="1.1.c2", text="looks", origin="author", check="judged", expected="the logo is blue", visual=True)
    assert _check(looks, FakeSnapshot(seq=1, elements=elements)).status == "unverified", "how a page looks needs a decider that can see"


def test_text_and_address_criteria_are_checked_in_code() -> None:
    shown = Criterion(id="1.1.c1", text="t", origin="author", check="text_visible", expected="Welcome")
    absent = Criterion(id="1.1.c2", text="t", origin="author", check="text_absent", expected="Error")
    snapshot = FakeSnapshot(seq=1, elements=login_elements())
    assert _check(shown, snapshot, text="Welcome, alice").status == "passed"
    assert _check(absent, snapshot, text="Welcome, alice").status == "passed"
    assert _check(shown, snapshot, text="Sign in").status == "failed"


def test_steps_read_from_a_case_without_a_plan_still_run(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    h.interpreter.case_reading = False
    result = h.run(case(Step(action="Open /login"), Step(action='click "Sign in"'), Step(action="verify", expected='I should see "Welcome"')))
    assert result.status == "passed"
    plan = _plan(h)
    assert [step["read"] for step in plan["steps"]] == ["page", "page", "page"]
    assert {finding["code"] for finding in plan["lint"]} == {"other"}
