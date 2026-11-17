"""Tests for the per-step graph: interpret, perceive, decide, gate, act, verify, dispose, recover, reread.

Steps are read by a scripted stand-in for the model (:class:`ScriptedInterpreter`) and
decided by test doubles (:class:`LabelDecider` picks the row labelled exactly as the
step's target; :class:`StubDecider` gives scripted answers): the engine has no coded
reader or decider of its own.
"""

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any, Sequence

import pytest

from testzeus_hercules.cases.models import SecretRef, Step, TestCase
from testzeus_hercules.decide.backends.stub import StubDecider
from testzeus_hercules.decide.contract import ChoiceAnswer
from testzeus_hercules.decide.errors import DecisionUnavailable
from testzeus_hercules.engine.data import CaseData
from testzeus_hercules.engine.evidence import EvidenceStore
from testzeus_hercules.engine.intent import StepIntent, case_items
from testzeus_hercules.engine.step_graph import StepDeps, StepLimits, StepOutcome, StepRunner
from testzeus_hercules.kernel.run_context import ListEventSink

from tests.v2.engine_fixtures.fakes import (
    FakeActions,
    FakeElement,
    FakePage,
    FakePerceiver,
    FakeSnapshot,
    LabelDecider,
    ScriptedInterpreter,
    clicks,
    fills,
    intent,
    login_elements,
    opens,
    sees,
)

SECRET = "pa55-w0rd!"
VISUAL = "the product card for the daypack shows a photo of the daypack rather than a placeholder image"

# How a model reads the step texts used below.
READINGS: dict[str, Any] = {
    'I fill "{{password}}" in "Password"': fills("Password", "{{password}}"),
    'fill "{{password}}" in "Password"': fills("Password", "{{password}}"),
    "Open /dashboard": opens("/dashboard"),
    "go to javascript:alert(1)": opens("javascript:alert(1)"),
    'I should see "Welcome, {{user}}"': sees("Welcome, {{user}}"),
    'I should see "Welcome"': sees("Welcome"),
    'click "Sign in"': clicks("Sign in"),
    '"Welcome back"': sees("Welcome back"),
    'click "Checkout now"': clicks("Checkout now"),
    'fill "x" in "Password"': fills("Password", "x"),
    'the "Log out" link should be visible': intent("assert_visible", "Log out", role="link"),
    'press key "Enter"': intent("press", value="Enter"),
    "wait 1 second": intent("wait", value="1"),
    'wait until "Saved" appears': intent("wait", expected="Saved"),
    'fill "2026-12-01" in "Username"': fills("Username", "2026-12-01"),
    'I fill "Byron" in "Find client"': fills("Find client", "Byron"),
    VISUAL: intent("assert_looks", expected=VISUAL),
    "the logo is shown in the header": intent("assert_looks", expected="the logo is shown in the header"),
    'I should see "Photo gallery"': sees("Photo gallery"),
    'I click the "Peter Pan" option': clicks("Peter Pan", role="option"),
    'I click "View Fleece Jacket"': clicks("View Fleece Jacket"),
    'I click "Remove"': clicks("Remove"),
    'I click "profile picture Jo"': clicks("profile picture Jo"),
    'I click "Show menu"': clicks("Show menu"),
}


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        actions: FakeActions | None = None,
        perceiver: FakePerceiver | None = None,
        decider: Any = None,
        threshold: float = 0.5,
        limits: StepLimits | None = None,
        env: dict[str, str] | None = None,
        interpreter: ScriptedInterpreter | None = None,
    ) -> None:
        case = TestCase(id="c", title="t", steps=[Step(action="open /")], test_data={"user": "alice", "password": SecretRef(secret="APP_PW")})
        self.data = CaseData.from_case(case, env={"APP_PW": SECRET} if env is None else env, run_id="r_1")
        self.page = FakePage(url="https://app.test/login")
        self.actions = actions or FakeActions()
        self.perceiver = perceiver or FakePerceiver()
        self.events = ListEventSink()
        self.run_dir = tmp_path / "run"
        self.deps = StepDeps(
            page=self.page,
            perceiver=self.perceiver,
            decider=decider or LabelDecider(),
            interpreter=interpreter or ScriptedInterpreter(READINGS),
            data=self.data,
            evidence=EvidenceStore(self.run_dir, "r_1", redact=self.data.redact),
            actions=self.actions,
            base_url="https://app.test",
            threshold=threshold,
            limits=limits or StepLimits(assert_timeout_ms=10),
            events=self.events,
        )
        self.runner = StepRunner(self.deps)

    def run(self, step: Step, index: int = 1) -> StepOutcome:
        """Read the step as the run graph does before the run (one case reading), then run it."""

        async def go() -> StepOutcome:
            verify_only = step.action == "verify" and step.expected is not None
            triple = (index, None, None if step.semantic else step.expected) if verify_only else (index, step.action, None if step.semantic else step.expected)
            self.deps.reading = await self.deps.interpreter.read_case(case_items([triple]))
            return await self.runner.run(index, step)

        return asyncio.run(go())


def test_fill_resolves_secret_and_records_evidence(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    outcome = h.run(Step(action='I fill "{{password}}" in "Password"'), index=2)

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("fill", 3, SECRET)]
    assert outcome.decisions == 1
    assert outcome.actions == 1
    step_dir = h.run_dir / "steps" / "2"
    assert {p.name for p in step_dir.iterdir()} == {"plain.jpg", "som.jpg", "observation.txt", "decisions.jsonl", "plan.jsonl"}
    plan = json.loads((step_dir / "plan.jsonl").read_text().splitlines()[0])
    assert (plan["stage"], plan["reading"], plan["source"]) == ("interpret", "model", "case")
    assert plan["actions"] == [{"kind": "fill", "target": "Password", "value": "{{password}}"}], "secrets stay placeholders in the plan"
    record = json.loads((step_dir / "decisions.jsonl").read_text().splitlines()[0])
    assert record["purpose"] == "target"
    assert record["decision"]["answers"]["target"]["choice"] == "md_3"
    assert SECRET not in (step_dir / "decisions.jsonl").read_text()
    assert "hercules://runs/r_1/steps/2/som.jpg" in outcome.evidence
    assert h.perceiver.calls[0][1] == ("password",)


def test_navigate_is_direct_and_uses_base_url(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    outcome = h.run(Step(action="Open /dashboard"))

    assert outcome.status == "passed"
    assert h.actions.calls == [("open_url", "/dashboard", "https://app.test")]
    assert h.perceiver.calls == []
    assert outcome.decisions == 0


def test_navigate_failure_fails_without_recovery(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    outcome = h.run(Step(action="go to javascript:alert(1)"))

    assert outcome.status == "failed"
    assert outcome.error_class == "NavigationFailed"
    assert len(h.perceiver.calls) <= 1  # only the failure-evidence snapshot


def test_then_step_text_check_passes_and_writes_ledger(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(text="Welcome, alice"))

    outcome = h.run(Step(action="verify", expected='I should see "Welcome, {{user}}"'))

    assert outcome.status == "passed"
    assert [(e.check, e.expected, e.verdict) for e in outcome.ledger] == [("text_contains", "Welcome, alice", "passed")]


def test_then_step_failure_has_actual_and_evidence(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(text="Sign in to Acme"))

    outcome = h.run(Step(action="verify", expected='I should see "Welcome"'), index=4)

    assert outcome.status == "failed"
    assert outcome.ledger[0].verdict == "failed"
    assert "Sign in to Acme" in (outcome.actual or "")
    assert outcome.error_class == "AssertionFailed"
    assert (h.run_dir / "steps" / "4" / "som.jpg").exists()
    assert outcome.evidence


def test_action_with_expected_checks_after_acting(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(text="Sign in", text_after_click="Welcome back"))

    outcome = h.run(Step(action='click "Sign in"', expected='"Welcome back"'))

    assert outcome.status == "passed"
    assert h.actions.calls[0] == ("click", 6)
    assert outcome.ledger[0].verdict == "passed"


def test_no_match_waits_then_scrolls_and_recovers_at_most_twice_then_rereads_the_step_on_the_page(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    outcome = h.run(Step(action='click "Checkout now"'))

    assert outcome.status == "failed"
    assert len(h.perceiver.calls) == 4  # three attempts, then the page's controls for the re-reading
    ((text, done, page),) = h.deps.interpreter.remaining_calls
    assert (text, done) == ('click "Checkout now"', ())
    assert 'button "Sign in"' in (page or "")
    # A page that is still rendering gets a moment first; only then is the target looked for further down.
    assert [call[0] for call in h.actions.calls if call[0] in ("scroll", "wait_seconds")] == ["wait_seconds", "scroll"]
    assert "Checkout now" in (outcome.actual or "")
    assert outcome.error_class == "TargetNotFound"
    assert outcome.decisions == 3


def _md6_else_first(request: Any, qid: str) -> dict[str, Any]:
    options = [name for name in request.questions[qid].criteria if name != "no_match"]
    choice = "md_6" if "md_6" in options else options[0]
    return {"type": "choice", "choice": choice, "confidence": 0.9, "probabilities": {choice: 0.9}}


def test_failed_action_is_decided_again_without_the_element_that_failed(tmp_path: Path) -> None:
    decider = StubDecider({"target": _md6_else_first})
    h = Harness(tmp_path, actions=FakeActions(fail_mds=[6]), decider=decider)

    outcome = h.run(Step(action='click "Sign in"'))

    assert outcome.status == "passed", outcome.actual
    clicked = [call[1] for call in h.actions.calls if call[0] == "click"]
    assert clicked[0] == 6 and clicked[1] != 6
    assert outcome.actions == 2
    assert "md_6" not in decider.calls[1].request.questions["target"].criteria


def test_target_gone_triggers_recovery(tmp_path: Path) -> None:
    perceiver = FakePerceiver(gone=[3])
    h = Harness(tmp_path, perceiver=perceiver)

    outcome = h.run(Step(action='fill "x" in "Password"'))

    assert outcome.status == "failed"  # only one password box, and it keeps vanishing
    assert len(perceiver.calls) == 4  # three attempts and the re-reading on the page


def test_action_budget_is_bounded(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(fail_mds=list(range(1, 10))), limits=StepLimits(max_actions=1, assert_timeout_ms=10))

    outcome = h.run(Step(action='click "Sign in"'))

    assert outcome.status == "failed"
    assert outcome.actions == 1
    assert outcome.error_class == "ActionFailed"


def test_low_confidence_recovers_then_fails(tmp_path: Path) -> None:
    def answer(request: Any, qid: str) -> Any:
        options = request.questions["target"].criteria
        return ChoiceAnswer(choice="md_6", confidence=0.3, probabilities={name: (0.3 if name == "md_6" else 0.0) for name in options})

    decider = StubDecider({"target": answer})
    h = Harness(tmp_path, decider=decider, threshold=0.5)

    outcome = h.run(Step(action='click "Sign in"'))

    assert outcome.status == "failed"
    assert "low confidence" in (outcome.actual or "")
    assert len(decider.calls) == 3
    assert h.actions.calls == []  # re-perceived without scrolling or clicking


def test_rows_are_offered_in_page_order_without_ranking(tmp_path: Path) -> None:
    decider = StubDecider()
    h = Harness(tmp_path, decider=decider, threshold=0.0)

    outcome = h.run(Step(action='click "Sign in"'))

    assert outcome.status == "passed"
    assert h.actions.calls == [("click", 1)]  # the stub takes the first option: the first element in page order
    rows = [row["id"] for row in decider.calls[0].request.state["rows"]]
    assert rows == [f"md_{element.md}" for element in login_elements()]


def test_unknown_kind_asks_the_decider_for_the_tool(tmp_path: Path) -> None:
    decider = StubDecider(
        {
            "target": {"type": "choice", "choice": "md_5", "confidence": 0.9, "probabilities": {"md_5": 0.9}},
            "tool": {"type": "choice", "choice": "check", "confidence": 0.9, "probabilities": {"check": 0.9}},
        }
    )
    h = Harness(tmp_path, decider=decider)

    outcome = h.run(Step(action="Remember me"))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("check", 5)]
    assert "tool" in decider.calls[0].request.questions


def test_decider_failure_is_an_error(tmp_path: Path) -> None:
    h = Harness(tmp_path, decider=StubDecider(error=DecisionUnavailable("offline")))

    outcome = h.run(Step(action='click "Sign in"'))

    assert outcome.status == "error"
    assert outcome.error_class == "DecisionUnavailable"
    assert "offline" in (outcome.actual or "")


def test_missing_secret_is_an_error_without_leaking(tmp_path: Path) -> None:
    h = Harness(tmp_path, env={})

    outcome = h.run(Step(action='fill "{{password}}" in "Password"'))

    assert outcome.status == "error"
    assert "APP_PW" in (outcome.actual or "")
    assert h.actions.calls == []


def test_assert_visible_uses_decider_and_visibility(tmp_path: Path) -> None:
    perceiver = FakePerceiver(FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="link", name="Log out"), FakeElement(md=2, role="heading", name="Welcome"))))
    h = Harness(tmp_path, perceiver=perceiver)

    outcome = h.run(Step(action="verify", expected='the "Log out" link should be visible'))

    assert outcome.status == "passed", outcome.actual
    assert outcome.ledger[0].check == "visible"


def test_a_semantic_expectation_is_judged_by_meaning_on_the_page(tmp_path: Path) -> None:
    decider = LabelDecider(judge=0.92)
    h = Harness(tmp_path, actions=FakeActions(text="Your dashboard: 3 open orders"), decider=decider)

    outcome = h.run(Step(action='click "Sign in"', expected="The dashboard shows the open orders", semantic=True), index=3)

    assert outcome.status == "passed", outcome.actual
    assert [(entry.check, entry.verdict, entry.source) for entry in outcome.ledger] == [("semantic", "passed", "llm")]
    request, purpose = decider.calls[-1]
    assert purpose == "semantic"
    assert request.state["page_text"] == "Your dashboard: 3 open orders"
    assert not request.has_images(), "no screenshot unless images may be sent"
    assert "The dashboard shows the open orders" in str(request.questions)
    assert '"purpose": "semantic"' in (h.run_dir / "steps" / "3" / "decisions.jsonl").read_text()


def test_an_unsure_semantic_judgement_is_unverified_never_passed_and_a_clear_no_fails(tmp_path: Path) -> None:
    step = Step(action="verify", expected="The order was placed", semantic=True)

    unsure = Harness(tmp_path / "unsure", decider=LabelDecider(judge=0.6)).run(step)
    assert unsure.status == "unverified"
    assert "not confident" in unsure.ledger[0].actual

    no = Harness(tmp_path / "no", decider=LabelDecider(judge=0.1)).run(step)
    assert no.status == "failed" and no.error_class == "AssertionFailed"


def test_a_semantic_judgement_sees_the_screenshot_when_images_may_be_sent(tmp_path: Path) -> None:
    decider = LabelDecider(judge=0.95)
    h = Harness(tmp_path, decider=decider)
    h.deps.images = True

    h.run(Step(action="verify", expected="The sign-in form is shown", semantic=True))

    request, purpose = decider.calls[-1]
    assert purpose == "semantic" and request.has_images()


def test_a_meaning_judgement_weighs_only_the_steps_own_words_against_the_page(tmp_path: Path) -> None:
    decider = LabelDecider(judge=0.95)
    h = Harness(tmp_path, decider=decider)

    h.run(Step(action="verify", expected="The sign-in form is shown", semantic=True))

    request, _ = decider.calls[-1]
    assert "Judge only the step's own words against this page" in request.state["task"]
    assert "other steps are not shown and do not matter" in request.state["task"]


def test_a_check_only_step_is_read_as_an_outcome_to_check(tmp_path: Path) -> None:
    interpreter = ScriptedInterpreter({'I should see "Price (high to low) Baby Onesie"': sees("Price (high to low) Baby Onesie")})
    h = Harness(tmp_path, actions=FakeActions(text="Sort Price (high to low) Baby Onesie $7.99"), interpreter=interpreter)

    outcome = h.run(Step(action="verify", expected='I should see "Price (high to low) Baby Onesie"'))

    assert outcome.status == "passed", outcome.actual
    assert [item.role for item in interpreter.case_calls[0]] == ["expected"]
    assert [entry.check for entry in outcome.ledger] == ["text_contains"], "a quoted text is checked literally, never judged by meaning"


def test_an_expected_outcome_without_a_literal_check_is_judged_by_meaning(tmp_path: Path) -> None:
    readings = {**READINGS, "the account is now active": intent("unknown")}
    decider = LabelDecider(judge=0.9)
    h = Harness(tmp_path, decider=decider, interpreter=ScriptedInterpreter(readings))

    outcome = h.run(Step(action='click "Sign in"', expected="the account is now active"))

    assert outcome.status == "passed", outcome.actual
    assert [entry.check for entry in outcome.ledger] == ["semantic"]
    items = [(item.role, item.text) for item in h.deps.interpreter.case_calls[0]]
    assert items == [("action", 'click "Sign in"'), ("expected", "the account is now active")]


def test_press_without_target_and_wait(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(text="Saved"))

    assert h.run(Step(action='press key "Enter"')).status == "passed"
    assert h.run(Step(action="wait 1 second")).status == "passed"
    assert h.run(Step(action='wait until "Saved" appears')).status == "passed"
    assert ("press", None, "Enter") in h.actions.calls
    assert ("wait_seconds", 1.0) in h.actions.calls


def test_emits_decision_events(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    h.run(Step(action='click "Sign in"'))

    kinds = [event.kind for event in h.events.events]
    assert "decision" in kinds
    decision = next(event for event in h.events.events if event.kind == "decision")
    assert decision.data["choice"] == "md_6"


def test_fill_that_does_not_stick_fails_without_trying_other_fields(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(mismatch_mds=[2]))

    outcome = h.run(Step(action='fill "2026-12-01" in "Username"'))

    assert outcome.status == "failed"
    assert [call[:2] for call in h.actions.calls] == [("fill", 2)]
    assert "61201-02-20" in (outcome.actual or "")
    assert outcome.error_class == "ActionFailed"


def test_recovery_keeps_the_disabled_reason_instead_of_no_match(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(disabled_mds=[6]))

    outcome = h.run(Step(action='click "Sign in"'))

    assert outcome.status == "failed"
    assert (outcome.actual or "").startswith("element 'Sign in' is disabled"), outcome.actual
    assert "no listed element matched" not in (outcome.actual or "")
    assert outcome.error_class == "ElementDisabled"
    assert {call[1] for call in h.actions.calls if call[0] == "click"} == {6}, "a disabled target is retried, never swapped for another element"


def test_disabled_reason_survives_a_later_no_match(tmp_path: Path) -> None:
    calls: list[int] = []

    def answer(request: Any, qid: str) -> Any:
        calls.append(1)
        options = request.questions["target"].criteria
        choice = "md_6" if len(calls) == 1 else "no_match"
        return ChoiceAnswer(choice=choice, confidence=0.9, probabilities={name: (0.9 if name == choice else 0.0) for name in options})

    h = Harness(tmp_path, actions=FakeActions(disabled_mds=[6]), decider=StubDecider({"target": answer}))

    outcome = h.run(Step(action='click "Sign in"'))

    assert outcome.status == "failed"
    assert (outcome.actual or "").startswith("element 'Sign in' is disabled"), outcome.actual


def on_page(text: str, *parts: StepIntent, remaining: Any = ()) -> ScriptedInterpreter:
    """A reader that reads ``text`` as ``parts`` only on the page (no case reading), and continues with ``remaining``."""
    return ScriptedInterpreter({**READINGS, text: list(parts)}, remaining=remaining, case_reading=False)


def test_compound_step_runs_every_action_in_order(tmp_path: Path) -> None:
    text = "they sign in as alice"
    h = Harness(tmp_path, interpreter=on_page(text, fills("Username", "{{user}}"), fills("Password", "{{password}}"), clicks("Sign in", role="button")))

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("fill", 2, "alice"), ("fill", 3, SECRET), ("click", 6)]
    assert outcome.decisions == 3
    assert outcome.actions == 3


def test_compound_step_mixes_direct_and_targeted_actions(tmp_path: Path) -> None:
    h = Harness(tmp_path, interpreter=on_page("go sign in", opens("/login"), clicks("Sign in")))

    outcome = h.run(Step(action="go sign in"))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("open_url", "/login", "https://app.test"), ("click", 6)]


def test_compound_step_fails_on_the_part_that_fails_without_repeating_earlier_parts(tmp_path: Path) -> None:
    h = Harness(tmp_path, interpreter=on_page("do two things", fills("Username", "alice"), clicks("Checkout now")))

    outcome = h.run(Step(action="do two things"))

    assert outcome.status == "failed"
    assert "Checkout now" in (outcome.actual or "")
    assert [call for call in h.actions.calls if call[0] == "fill"] == [("fill", 2, "alice")]


def test_compound_step_checks_its_assertions_after_the_actions(tmp_path: Path) -> None:
    text = "sign in and land on the welcome page"
    h = Harness(tmp_path, actions=FakeActions(text="Welcome, alice"), interpreter=on_page(text, clicks("Sign in"), sees("Welcome")))

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert [entry.check for entry in outcome.ledger] == ["text_contains"]
    assert h.actions.calls == [("click", 6)]


def test_interpreter_gets_the_page_controls_with_drop_down_options(tmp_path: Path) -> None:
    interpreter = on_page("switch the site to German", intent("select", "Language", value="Deutsch"))
    h = Harness(tmp_path, actions=FakeActions(options={7: ["English", "Deutsch"]}), interpreter=interpreter)

    outcome = h.run(Step(action="switch the site to German"))

    assert outcome.status == "passed", outcome.actual
    ((_, context),) = interpreter.calls
    assert 'combobox "Language" (options: English | Deutsch)' in context
    assert 'button "Sign in"' in context
    assert 'heading "Sign in to Acme"' not in context, "only controls are listed"


def test_each_part_of_a_compound_step_is_decided_on_its_own_description(tmp_path: Path) -> None:
    text = "use German and stay signed in"
    decider = LabelDecider()
    h = Harness(tmp_path, decider=decider, interpreter=on_page(text, intent("select", "Language", value="Deutsch"), intent("check", "Remember me")))

    h.run(Step(action=text))

    texts = [request.state["step"]["text"] for request, purpose in decider.calls if purpose == "target"]
    assert [text.split(" (action ")[0] for text in texts] == ['select "Deutsch" in "Language"', 'check "Remember me"']
    assert "(action 1 of 2 of the step: use German" in texts[0] and "(action 2 of 2 of the step:" in texts[1], "each part says where it is in the step"
    assert texts[0].endswith("(action 1 of 2 of the step: use German and stay signed in)")
    assert '(action 2 of 2 of the step: use German and stay signed in; done so far: select combobox "Language" with "Deutsch"' in texts[1]


class _FirstFillFails(FakeActions):
    async def fill(self, locator: Any, value: str, *, timeout_ms: int = 0) -> Any:
        result = await super().fill(locator, value, timeout_ms=timeout_ms)
        if len([call for call in self.calls if call[0] == "fill"]) == 1:
            return type(result)(ok=False, detail="fill failed: the element is not editable (read-only)", kind="not_editable")
        return result


def test_an_element_that_failed_is_excluded_by_identity_not_by_its_number(tmp_path: Path) -> None:
    # The first snapshot was taken while the old view was still shown; md numbers are reused by the new view.
    stale = FakeSnapshot(seq=0, elements=(FakeElement(md=5, role="combobox", name="Find client"),))
    fresh = FakeSnapshot(seq=0, elements=(FakeElement(md=5, role="textbox", name="Find client"),))
    h = Harness(tmp_path, actions=_FirstFillFails(), perceiver=FakePerceiver(stale, fresh))

    outcome = h.run(Step(action='I fill "Byron" in "Find client"'))

    assert outcome.status == "passed", outcome.actual
    assert [call for call in h.actions.calls if call[0] == "fill"] == [("fill", 5, "Byron"), ("fill", 5, "Byron")]


def test_a_select_without_a_value_is_read_again_on_the_page_instead_of_guessed(tmp_path: Path) -> None:
    text = "they show the page in deutsch only"
    interpreter = ScriptedInterpreter({text: intent("unknown")}, remaining=[[intent("select", "Language", value="Deutsch")]])
    decider = StubDecider(
        {
            "target": {"type": "choice", "choice": "md_7", "confidence": 0.9, "probabilities": {"md_7": 0.9}},
            "tool": {"type": "choice", "choice": "select", "confidence": 0.9, "probabilities": {"select": 0.9}},
        }
    )
    h = Harness(tmp_path, actions=FakeActions(options={7: ["English", "Deutsch"]}), interpreter=interpreter, decider=decider)

    outcome = h.run(Step(action=text), index=2)

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls[-1] == ("select", 7, "Deutsch")
    asked, done, page = interpreter.remaining_calls[0]
    assert (asked, done) == (text, ())
    assert "options: English | Deutsch" in (page or "")
    # The re-read plan was made on the page, so once it is done the model is asked whether the step needs more.
    assert len(interpreter.remaining_calls) == 2
    stages = [json.loads(line)["stage"] for line in (h.run_dir / "steps" / "2" / "plan.jsonl").read_text().splitlines()]
    assert stages == ["interpret", "reread", "amend", "continue"], "the re-read is recorded as an amendment of the plan"


def test_a_case_reading_that_misses_on_the_page_is_read_again_once_and_continues(tmp_path: Path) -> None:
    text = "they log in"
    interpreter = ScriptedInterpreter({text: [fills("Username", "{{user}}"), clicks("Log in")]}, remaining=[[clicks("Sign in")]])
    h = Harness(tmp_path, interpreter=interpreter)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert [call for call in h.actions.calls if call[0] in ("fill", "click")] == [("fill", 2, "alice"), ("click", 6)]
    _, done, _ = interpreter.remaining_calls[0]
    assert done == ('fill textbox "Username" with "alice"',), "what was done is never repeated"


def test_a_step_read_on_the_page_is_not_read_again_after_it_fails(tmp_path: Path) -> None:
    interpreter = on_page("they check out", clicks("Checkout now"))
    h = Harness(tmp_path, interpreter=interpreter)

    outcome = h.run(Step(action="they check out"))

    assert outcome.status == "failed" and outcome.error_class == "TargetNotFound"
    assert interpreter.remaining_calls == []


def _menu_pages() -> FakePerceiver:
    closed = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="button", name="Show menu"), FakeElement(md=2, role="button", name="Checkout")))
    opened = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="button", name="Hide menu"), FakeElement(md=3, role="link", name="Everything"), FakeElement(md=2, role="button", name="Checkout")))
    return FakePerceiver(closed, closed, opened)


def test_a_step_read_on_the_page_is_continued_once_the_page_shows_new_controls(tmp_path: Path) -> None:
    text = "they use the side menu to go back to everything"
    interpreter = on_page(text, clicks("Show menu"), remaining=[[clicks("Everything")], ()])
    h = Harness(tmp_path, perceiver=_menu_pages(), interpreter=interpreter)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert [call for call in h.actions.calls if call[0] == "click"] == [("click", 1), ("click", 3)]
    asked, done, page = interpreter.remaining_calls[0]
    assert asked == text
    assert done == ('click button "Show menu"',)
    assert 'link "Everything"' in (page or "")


def test_an_empty_continuation_never_drops_actions_the_plan_still_has(tmp_path: Path) -> None:
    text = "they open the side menu and go back to everything"
    interpreter = on_page(text, clicks("Show menu"), clicks("Everything"), remaining=[(), ()])
    h = Harness(tmp_path, perceiver=_menu_pages(), interpreter=interpreter)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert [call for call in h.actions.calls if call[0] == "click"] == [("click", 1), ("click", 3)], "the planned click is done although the model answered nothing remains"
    assert interpreter.remaining_calls and interpreter.remaining_calls[0][1] == ('click button "Show menu"',)


def test_a_step_is_asked_about_only_once_its_plan_is_done_while_the_page_controls_stay_the_same(tmp_path: Path) -> None:
    text = "they type their name and stay signed in"
    interpreter = on_page(text, fills("Username", "{{user}}"), intent("check", "Remember me"))
    h = Harness(tmp_path, interpreter=interpreter)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert [done for _text, done, _page in interpreter.remaining_calls] == [('fill textbox "Username" with "alice"', 'check checkbox "Remember me"')]


def test_a_filter_chosen_without_its_go_button_is_completed(tmp_path: Path) -> None:
    page = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="combobox", name="Type"), FakeElement(md=2, role="button", name="Go")))
    text = "they filter the activity to show only debits"
    interpreter = on_page(text, intent("select", "Type", value="Debit"), remaining=[[clicks("Go")], ()])
    h = Harness(tmp_path, perceiver=FakePerceiver(page), interpreter=interpreter)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("select", 1, "Debit"), ("click", 2)]


def test_a_case_step_whose_actions_may_not_reach_its_goal_is_continued_on_the_page(tmp_path: Path) -> None:
    text = "they use the side menu to go back to everything"
    reading = clicks("Show menu").model_copy(update={"complete": False})
    interpreter = ScriptedInterpreter({text: reading}, remaining=[[clicks("Everything")], ()])
    closed, opened = _menu_pages()._snapshots[0], _menu_pages()._snapshots[-1]
    h = Harness(tmp_path, perceiver=FakePerceiver(closed, opened), interpreter=interpreter)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert [call for call in h.actions.calls if call[0] == "click"] == [("click", 1), ("click", 3)]
    asked, done, page = interpreter.remaining_calls[0]
    assert asked == text and done == ('click button "Show menu"',)
    assert 'link "Everything"' in (page or "")
    plan = [json.loads(line) for line in (h.run_dir / "steps" / "1" / "plan.jsonl").read_text().splitlines()]
    assert plan[0]["complete"] is False and plan[1]["stage"] == "continue" and "wall_ms" in plan[1]


def test_a_complete_single_action_case_step_is_not_asked_about_again(tmp_path: Path) -> None:
    reading = clicks("Show menu").model_copy(update={"complete": True})
    interpreter = ScriptedInterpreter({'I click "Show menu"': reading}, remaining=[[clicks("Everything")]])
    h = Harness(tmp_path, perceiver=_menu_pages(), interpreter=interpreter)

    outcome = h.run(Step(action='I click "Show menu"'))

    assert outcome.status == "passed", outcome.actual
    assert interpreter.remaining_calls == []


def test_a_case_step_whose_reading_says_it_names_every_control_is_not_asked_about_even_with_several_actions(tmp_path: Path) -> None:
    text = 'I fill "alice" in "Username" and click "Sign in"'
    reading = [fills("Username", "{{user}}").model_copy(update={"complete": True}), clicks("Sign in")]
    interpreter = ScriptedInterpreter({text: reading}, remaining=[[clicks("Sign in")]])
    h = Harness(tmp_path, interpreter=interpreter)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert interpreter.remaining_calls == [], "no model call is spent on a step whose words name every control"


def test_a_guessed_control_the_page_does_not_have_is_read_again_at_once(tmp_path: Path) -> None:
    text = "they open the side menu"
    reading = clicks("Side menu").model_copy(update={"complete": False})
    decider = LabelDecider()
    interpreter = ScriptedInterpreter({text: reading}, remaining=[[clicks("Show menu")], ()])
    closed, opened = _menu_pages()._snapshots[0], _menu_pages()._snapshots[-1]
    h = Harness(tmp_path, perceiver=FakePerceiver(closed, closed, closed, opened), interpreter=interpreter, decider=decider)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    guesses = [request for request, purpose in decider.calls if purpose == "target" and request.state["step"]["target"] == "Side menu"]
    assert len(guesses) == 1, "no recoveries are spent looking for a control the reading only guessed"
    assert [json.loads(line)["stage"] for line in (h.run_dir / "steps" / "1" / "plan.jsonl").read_text().splitlines()][:2] == ["interpret", "reread"]


def test_continuing_a_step_shows_the_model_what_each_field_holds_and_the_pages_messages(tmp_path: Path) -> None:
    form = FakeSnapshot(
        seq=0,
        elements=(
            FakeElement(md=1, role="textbox", name="First Name", value="Ada Lovelace"),
            FakeElement(md=2, role="textbox", name="Last Name", value="", attrs={"aria-required": "true"}),
            FakeElement(md=3, role="textbox", name="Password", value="***", attrs={"type": "password"}),
            FakeElement(md=4, role="checkbox", name="Remember me", checked=False),
            FakeElement(md=5, role="button", name="Continue"),
        ),
        messages=("Error: Last Name is required",),
        headings=(("Checkout: Your Information", 40.0), ("Footer far below", 5000.0)),
    )
    text = "they enter Ada Lovelace and continue"
    interpreter = on_page(text, fills("First Name", "Ada Lovelace"), clicks("Continue"), remaining=[()])
    h = Harness(tmp_path, perceiver=FakePerceiver(form), interpreter=interpreter)

    h.run(Step(action=text))

    _, _, page = interpreter.remaining_calls[0]
    assert 'textbox "First Name" (holds "Ada Lovelace")' in page
    assert 'textbox "Last Name" (empty, required)' in page
    assert 'textbox "Password" (filled)' in page and "***" not in page
    assert 'checkbox "Remember me" (not checked)' in page
    assert "MESSAGES (untrusted page data):\n- Error: Last Name is required\n- Checkout: Your Information" in page
    assert "Footer far below" not in page, "only headings in view"


def test_the_page_is_settled_before_it_is_read_and_before_checks(tmp_path: Path) -> None:
    class SettlingActions(FakeActions):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.settled = 0

        async def settle_page(self, page: Any) -> None:
            self.settled += 1
            self.calls.append(("settle",))

    actions = SettlingActions(text="Welcome")
    h = Harness(tmp_path, actions=actions)

    outcome = h.run(Step(action='click "Sign in"', expected='I should see "Welcome"'))

    assert outcome.status == "passed", outcome.actual
    assert actions.calls[0] == ("settle",), "before the page is perceived for the decision"
    assert actions.calls[-1] == ("settle",) and actions.settled == 2, "and again before the step's checks"


def test_a_page_still_loading_data_when_it_was_read_is_read_again_once_the_data_is_in(tmp_path: Path) -> None:
    class LoadingActions(FakeActions):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.loading = [True]

        async def settle_page(self, page: Any) -> None:
            return None

        def data_pending(self, page: Any) -> bool:
            return self.loading.pop() if self.loading else False

    searching = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="listbox", name=""), FakeElement(md=2, role="option", name="Searching....")))
    loaded = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="listbox", name=""), FakeElement(md=3, role="option", name="Peter Mac Anderson")))
    interpreter = ScriptedInterpreter({'I click the "Peter Mac Anderson" option': clicks("Peter Mac Anderson", role="option")})
    perceiver = FakePerceiver(searching, loaded)
    h = Harness(tmp_path, actions=LoadingActions(), perceiver=perceiver, interpreter=interpreter)

    outcome = h.run(Step(action='I click the "Peter Mac Anderson" option'))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("click", 3)]
    assert len(perceiver.calls) == 2, "read once more after the data arrived, not again after that"


def test_a_choice_read_without_the_page_is_made_the_way_the_decider_sees_the_element_on_the_page(tmp_path: Path) -> None:
    page = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="radio", name="Price: high to low"), FakeElement(md=2, role="radio", name="Price: low to high")))
    text = "sorts them from the most expensive to the cheapest"
    interpreter = ScriptedInterpreter({text: intent("select", "Price: high to low", value="Price: high to low")})
    decider = LabelDecider(tool="check")
    h = Harness(tmp_path, perceiver=FakePerceiver(page), interpreter=interpreter, decider=decider)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("check", 1)], "the radio button is ticked, not treated as a drop-down"
    request, _ = next(call for call in decider.calls if call[1] == "target")
    assert "tool" in request.questions


def test_a_tool_answer_with_no_confidence_leaves_the_planned_action(tmp_path: Path) -> None:
    page = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="combobox", name="Type"),))
    text = 'I select "Customer - Direct" from the "Type" dropdown'
    interpreter = ScriptedInterpreter({text: intent("select", "Type", value="Customer - Direct")})
    decider = LabelDecider(tool="check", tool_confidence=0.0)
    h = Harness(tmp_path, perceiver=FakePerceiver(page), interpreter=interpreter, decider=decider)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("select", 1, "Customer - Direct")], "a model that chose no action does not turn the choice into a tick"


def test_a_choice_read_on_the_page_keeps_its_kind(tmp_path: Path) -> None:
    text = "switch the site to German"
    decider = LabelDecider(tool="check")
    h = Harness(tmp_path, interpreter=on_page(text, intent("select", "Language", value="Deutsch")), decider=decider)

    h.run(Step(action=text))

    request, _ = next(call for call in decider.calls if call[1] == "target")
    assert "tool" not in request.questions


def test_steps_from_the_case_reading_are_never_continued(tmp_path: Path) -> None:
    interpreter = ScriptedInterpreter(READINGS, remaining=[[clicks("Everything")]])
    h = Harness(tmp_path, perceiver=_menu_pages(), interpreter=interpreter)

    outcome = h.run(Step(action='I click "Show menu"'))

    assert outcome.status == "passed", outcome.actual
    assert interpreter.remaining_calls == []


def test_continuations_are_bounded(tmp_path: Path) -> None:
    interpreter = on_page("they keep clicking", clicks("Show menu"), remaining=[[clicks("Checkout")]] * 20)
    perceiver = FakePerceiver(
        *[
            FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="button", name="Show menu"), FakeElement(md=2, role="button", name="Checkout"), FakeElement(md=9, role="link", name=f"Page {n}")))
            for n in range(40)
        ]
    )
    h = Harness(tmp_path, perceiver=perceiver, interpreter=interpreter)

    outcome = h.run(Step(action="they keep clicking"))

    assert outcome.actions <= 8
    assert len(interpreter.remaining_calls) <= 4


def test_a_visual_expectation_is_unverified_without_a_vision_decider(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(text="Trail Daypack"))

    outcome = h.run(Step(action="verify", expected=VISUAL))

    assert outcome.status == "unverified"
    assert [entry.check for entry in outcome.ledger] == ["visual"]
    assert "screenshot" in (outcome.actual or "")


def test_a_visual_expectation_is_judged_on_the_screenshot(tmp_path: Path) -> None:
    decider = StubDecider({"holds": {"type": "noul", "noul": 0.08}})
    h = Harness(tmp_path, actions=FakeActions(text="Trail Daypack"), decider=decider)
    h.deps.images = True

    outcome = h.run(Step(action="verify", expected=VISUAL), index=5)

    assert outcome.status == "failed"
    assert outcome.ledger[0].check == "visual" and outcome.ledger[0].source == "llm"
    (call,) = decider.calls
    assert call.purpose == "visual"
    assert call.request.has_images()
    assert VISUAL in str(call.request.state)
    records = (h.run_dir / "steps" / "5" / "decisions.jsonl").read_text()
    assert '"purpose": "visual"' in records


def test_a_visual_expectation_that_holds_passes(tmp_path: Path) -> None:
    h = Harness(tmp_path, decider=StubDecider({"holds": {"type": "noul", "noul": 0.93}}))
    h.deps.images = True

    outcome = h.run(Step(action="verify", expected="the logo is shown in the header"))

    assert outcome.status == "passed", outcome.actual


def test_quoted_text_expectations_stay_text_checks(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(text="Photo gallery"))

    outcome = h.run(Step(action="verify", expected='I should see "Photo gallery"'))

    assert outcome.status == "passed"
    assert outcome.ledger[0].check == "text_contains"


def test_a_continued_step_is_asked_again_once_its_continuation_is_done(tmp_path: Path) -> None:
    tabs = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="button", name="Deposit"),))
    form = FakeSnapshot(
        seq=0,
        elements=(
            FakeElement(md=1, role="button", name="Deposit"),
            FakeElement(md=2, role="spinbutton", name="amount", attrs={"label": "Amount"}),
            FakeElement(md=3, role="button", name="Deposit", attrs={"type": "submit", "near": "Amount"}),
        ),
    )
    text = "they put 500 into the savings account"
    interpreter = on_page(text, clicks("Deposit"), remaining=[[fills("Amount", "500")], [clicks("Deposit")], ()])
    h = Harness(tmp_path, perceiver=FakePerceiver(tabs, tabs, form), interpreter=interpreter, decider=StubDecider({"target": _pick_last_deposit}), threshold=0.0)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert len(interpreter.remaining_calls) == 3
    assert interpreter.remaining_calls[1][1][-1] == 'fill spinbutton "amount" with "500"'
    assert 'button "Deposit" (submits a form)' in (interpreter.remaining_calls[1][2] or "")


def _pick_last_deposit(request: Any, qid: str) -> dict[str, Any]:
    options = [name for name in request.questions[qid].criteria if name != "no_match"]
    rows = {row["id"]: row for row in request.state["rows"]}
    wanted = request.state["step"]["target"]
    named = [name for name in options if rows[name]["name"] == wanted or rows[name].get("attrs", {}).get("label") == wanted]
    choice = named[-1] if named else options[0]
    return {"type": "choice", "choice": choice, "confidence": 0.9, "probabilities": {choice: 0.9}}


def test_the_role_a_step_names_goes_to_the_decider(tmp_path: Path) -> None:
    loading = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="option", name="Searching...."), FakeElement(md=2, role="generic", name="Peter Pan Profile", tier=3)))
    loaded = FakeSnapshot(seq=0, elements=(FakeElement(md=3, role="option", name="Peter Pan"), FakeElement(md=2, role="generic", name="Peter Pan Profile", tier=3)))
    decider = LabelDecider()
    h = Harness(tmp_path, perceiver=FakePerceiver(loading, loaded), decider=decider)

    outcome = h.run(Step(action='I click the "Peter Pan" option'))

    assert outcome.status == "passed", outcome.actual
    assert [call for call in h.actions.calls if call[0] == "click"] == [("click", 3)]
    assert {request.state["step"]["role"] for request, _ in decider.calls} == {"option"}


def test_clickable_elements_without_a_control_role_are_offered_to_the_interpreter(tmp_path: Path) -> None:
    menu = FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="generic", name="profile picture Jo", tier=3), FakeElement(md=2, role="generic", name="", tier=3)))
    interpreter = on_page("they open the user menu", clicks("profile picture Jo"))
    h = Harness(tmp_path, perceiver=FakePerceiver(menu), interpreter=interpreter)

    h.run(Step(action="they open the user menu"))

    assert '- clickable "profile picture Jo"' in (interpreter.calls[0][1] or "")


def _split(first: str, second: str) -> Any:
    def answer(request: Any, qid: str) -> dict[str, Any]:
        options = list(request.questions[qid].criteria)
        probabilities = {name: (0.5 if name in (first, second) else 0.0) for name in options}
        return {"type": "choice", "choice": first, "confidence": 0.5, "probabilities": probabilities}

    return answer


def test_a_vote_split_between_interchangeable_elements_is_pooled(tmp_path: Path) -> None:
    # A product's picture and its title both link to the same page under the same name.
    twins = FakeSnapshot(
        seq=0,
        elements=(
            FakeElement(md=4, role="link", name="View Fleece Jacket", attrs={"href": "/item/5"}),
            FakeElement(md=5, role="link", name="View Fleece Jacket", attrs={"href": "/item/5"}),
            FakeElement(md=6, role="button", name="Add to cart"),
        ),
    )
    h = Harness(tmp_path, perceiver=FakePerceiver(twins), decider=StubDecider({"target": _split("md_4", "md_5")}), threshold=0.6)

    outcome = h.run(Step(action='I click "View Fleece Jacket"'))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("click", 4)]


def test_a_vote_split_between_different_targets_stays_unsure(tmp_path: Path) -> None:
    rows = FakeSnapshot(
        seq=0,
        elements=(FakeElement(md=4, role="button", name="Remove", attrs={"near": "Lamp"}), FakeElement(md=5, role="button", name="Remove", attrs={"near": "Daypack"})),
    )
    h = Harness(tmp_path, perceiver=FakePerceiver(rows), decider=StubDecider({"target": _split("md_4", "md_5")}), threshold=0.6)

    outcome = h.run(Step(action='I click "Remove"'))

    assert outcome.status == "failed"
    assert outcome.error_class == "LowConfidence"


def test_a_vote_split_between_an_element_and_one_inside_it_is_pooled(tmp_path: Path) -> None:
    from tests.v2.engine_fixtures.fakes import FakeBBox

    menu = FakeSnapshot(
        seq=0,
        elements=(
            FakeElement(md=18, role="generic", name="profile picture Jo", bbox=FakeBBox(1000, 10, 160, 40), tier=3),
            FakeElement(md=20, role="generic", name="profile picture", bbox=FakeBBox(1004, 14, 32, 32), tier=3),
        ),
    )
    h = Harness(tmp_path, perceiver=FakePerceiver(menu), decider=StubDecider({"target": _split("md_18", "md_20")}), threshold=0.6)

    outcome = h.run(Step(action='I click "profile picture Jo"'))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("click", 18)]


def test_the_deciders_pick_among_same_named_elements_is_acted_on_as_chosen(tmp_path: Path) -> None:
    form = FakeSnapshot(
        seq=0,
        elements=(
            FakeElement(md=5, role="button", name="Deposit", attrs={"near": "Transactions Withdraw"}),
            FakeElement(md=7, role="spinbutton", name="amount", attrs={"label": "Amount"}),
            FakeElement(md=8, role="button", name="Deposit", attrs={"type": "submit", "near": "Amount"}),
        ),
    )
    always_tab = {"type": "choice", "choice": "md_5", "confidence": 1.0, "probabilities": {"md_5": 1.0}}
    picks = {"target": lambda request, qid: always_tab if request.state["step"]["kind"] == "click" else {"type": "choice", "choice": "md_7", "confidence": 1.0, "probabilities": {"md_7": 1.0}}}
    text = "they put 500 into the savings account"
    h = Harness(tmp_path, perceiver=FakePerceiver(form), decider=StubDecider(picks), interpreter=on_page(text, fills("Amount", "500"), clicks("Deposit", role="button")))

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("fill", 7, "500"), ("click", 5)], "no coded ranking overrides the model's pick"


def test_a_model_never_rewrites_what_a_step_expects_into_what_the_page_shows(tmp_path: Path) -> None:
    expected = 'the search reports "(1) Record Found"'
    rewritten = intent("assert_text", expected="Info No Records Found")
    decider = LabelDecider(judge=0.05)
    h = Harness(tmp_path, actions=FakeActions(text="Info No Records Found"), decider=decider, interpreter=ScriptedInterpreter({expected: rewritten}))

    outcome = h.run(Step(action="verify", expected=expected))

    assert outcome.status == "failed"
    assert [(entry.check, entry.expected) for entry in outcome.ledger] == [("semantic", expected)], "the rewritten check is dropped; the step's own words are judged"


def test_a_model_assertion_taken_from_the_step_is_kept(tmp_path: Path) -> None:
    expected = 'the balance reads "Balance : 500"'
    h = Harness(tmp_path, actions=FakeActions(text="Account Balance : 500 Dollar"), interpreter=ScriptedInterpreter({expected: intent("assert_text", expected="Balance : 500")}))

    outcome = h.run(Step(action="verify", expected=expected))

    assert outcome.status == "passed"


def _many_items(count: int) -> FakeSnapshot:
    return FakeSnapshot(seq=0, elements=tuple(FakeElement(md=n, role="button", name=f"Item {n}") for n in range(1, count + 1)))


def test_a_page_larger_than_one_question_is_asked_in_page_order_groups(tmp_path: Path) -> None:
    decider = LabelDecider()
    readings = {'click "Item 300"': clicks("Item 300")}
    h = Harness(tmp_path, perceiver=FakePerceiver(_many_items(300)), decider=decider, interpreter=ScriptedInterpreter(readings))

    outcome = h.run(Step(action='click "Item 300"'))

    assert outcome.status == "passed", outcome.actual
    assert h.actions.calls == [("click", 300)]
    sizes = [len(request.state["rows"]) for request, _ in decider.calls]
    assert sizes == [254, 46]
    assert outcome.decisions == 2


def test_picks_from_several_groups_are_compared_in_one_more_request(tmp_path: Path) -> None:
    def first_named(request: Any, qid: str) -> dict[str, Any]:
        named = [row["id"] for row in request.state["rows"] if row["name"] == "Twin"]
        choice = named[0] if named else "no_match"
        return {"type": "choice", "choice": choice, "confidence": 0.9, "probabilities": {choice: 0.9}}

    elements = tuple(FakeElement(md=n, role="button", name="Twin" if n in (10, 280) else f"Item {n}") for n in range(1, 301))
    decider = StubDecider({"target": first_named})
    h = Harness(tmp_path, perceiver=FakePerceiver(FakeSnapshot(seq=0, elements=elements)), decider=decider, interpreter=ScriptedInterpreter({'click "Twin"': clicks("Twin")}))

    outcome = h.run(Step(action='click "Twin"'))

    assert outcome.status == "passed", outcome.actual
    final = decider.calls[-1].request
    assert [row["id"] for row in final.state["rows"]] == ["md_10", "md_280"]
    assert outcome.decisions == 3


def test_a_wait_read_without_a_duration_is_read_again_instead_of_skipped(tmp_path: Path) -> None:
    interpreter = ScriptedInterpreter({"I wait for 4 seconds": intent("wait")}, remaining=[[intent("wait", value="4")]])
    h = Harness(tmp_path, interpreter=interpreter)

    outcome = h.run(Step(action="I wait for 4 seconds"))

    assert outcome.status == "passed", outcome.actual
    assert ("wait_seconds", 4.0) in h.actions.calls
    assert ("wait_seconds", 0.0) not in h.actions.calls, "a missing value is never treated as zero"
    assert interpreter.remaining_calls[0][:2] == ("I wait for 4 seconds", ()), "read again on the page, nothing done yet"


def test_a_navigation_read_without_an_address_fails_when_reading_again_does_not_help(tmp_path: Path) -> None:
    h = Harness(tmp_path, interpreter=ScriptedInterpreter({"go to the start page": intent("navigate")}))

    outcome = h.run(Step(action="go to the start page"))

    assert outcome.status == "failed" and outcome.error_class == "AmbiguousStep"
    assert "no address" in (outcome.actual or "")
    assert not [call for call in h.actions.calls if call[0] == "open_url"]


def test_a_case_read_step_with_several_actions_is_checked_once_when_its_plan_is_done(tmp_path: Path) -> None:
    text = "they sign in with alice"
    interpreter = ScriptedInterpreter({text: [fills("Username", "alice"), fills("Password", "{{password}}")]}, remaining=[[clicks("Sign in")], ()])
    h = Harness(tmp_path, interpreter=interpreter)

    outcome = h.run(Step(action=text))

    assert outcome.status == "passed", outcome.actual
    assert [call[:2] for call in h.actions.calls] == [("fill", 2), ("fill", 3), ("click", 6)], "the missing submit was added"
    assert interpreter.remaining_calls[0][1] == ('fill textbox "Username" with "alice"', 'fill textbox "Password" with "******"')


def test_a_check_the_model_could_not_state_sends_the_whole_expectation_to_a_judge(tmp_path: Path) -> None:
    # A model read the outcome as "the card is visible" plus an assertion with no text to look for: the card
    # being visible must not pass the step on its own; the step's own words are judged by meaning.
    expected = 'the product card for "Sauce Labs Backpack" shows a photo of the backpack rather than a placeholder image'
    reading = [intent("assert_visible", "Sauce Labs Backpack"), intent("assert_not_text", "Sauce Labs Backpack", value="placeholder image")]
    perceiver = FakePerceiver(FakeSnapshot(seq=0, elements=(FakeElement(md=1, role="link", name="Sauce Labs Backpack"),)))
    decider = LabelDecider(judge=0.02)
    h = Harness(tmp_path, perceiver=perceiver, decider=decider, interpreter=ScriptedInterpreter({expected: reading}))

    outcome = h.run(Step(action="verify", expected=expected))

    assert outcome.status == "failed", outcome.actual
    assert ("semantic", expected) in [(entry.check, entry.expected) for entry in outcome.ledger]


def test_an_expected_outcome_with_a_dropped_check_is_also_judged_by_meaning(tmp_path: Path) -> None:
    # "A" is checked literally; the model's second check names text the step never gave, so it is dropped,
    # and the outcome is judged as a whole instead of passing on "A" alone.
    expected = 'I should see "Saved" and "3 items"'
    reading = [intent("assert_text", expected="Saved"), intent("assert_text", expected="2 items")]
    decider = LabelDecider(judge=0.03)
    h = Harness(tmp_path, actions=FakeActions(text="Saved 2 items"), decider=decider, interpreter=ScriptedInterpreter({**READINGS, expected: reading}))

    outcome = h.run(Step(action='click "Sign in"', expected=expected))

    assert outcome.status == "failed", outcome.actual
    assert [(entry.check, entry.verdict) for entry in outcome.ledger] == [("text_contains", "passed"), ("semantic", "failed")]


def test_a_reread_is_told_the_case_reading_and_the_earlier_steps(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.deps.steps = ('I open "/login"', 'I fill "alice" in "Username"', 'click "Checkout now"', "I should see the receipt")

    outcome = h.run(Step(action='click "Checkout now"'), index=3)

    assert outcome.status == "failed"
    assert h.deps.interpreter.remaining_context == [{"reading": ('click "Checkout now"',), "earlier": ('I open "/login"', 'I fill "alice" in "Username"')}]


def test_a_reread_lists_only_the_open_modal_dialogs_controls_with_their_context(tmp_path: Path) -> None:
    @dataclasses.dataclass(frozen=True)
    class ModalElement(FakeElement):
        dialog: str | None = None
        section: str = ""
        scroll: str = ""
        scroll_in_dialog: bool = False
        blocked: str = ""

    @dataclasses.dataclass(frozen=True)
    class ModalSnapshot(FakeSnapshot):
        modal: str | None = None

        @property
        def offered(self) -> tuple[Any, ...]:
            return tuple(e for e in self.elements if not e.blocked)

        @property
        def not_offered(self) -> int:
            return len(self.elements) - len(self.offered)

    page = ModalSnapshot(
        seq=0,
        modal="New Record",
        elements=(
            ModalElement(md=1, role="button", name="New", blocked="behind_modal"),
            ModalElement(md=2, role="textbox", name="Record Name", value="", dialog="New Record", section="Details"),
            ModalElement(md=3, role="textbox", name="Description", value="", dialog="New Record", section="More", scroll="below", scroll_in_dialog=True, in_viewport=False),
            ModalElement(md=4, role="button", name="Save", dialog="New Record"),
        ),
    )
    h = Harness(tmp_path, perceiver=FakePerceiver(page))

    outcome = h.run(Step(action='click "Checkout now"'))

    assert outcome.status == "failed"
    ((_, _, view),) = h.deps.interpreter.remaining_calls
    lines = (view or "").splitlines()
    assert lines[0] == '(a modal dialog "New Record" is open: only its controls are listed)'
    assert '- button "New"' not in lines
    assert '- textbox "Record Name" (empty) [in dialog "New Record", under "Details"]' in lines
    assert '- textbox "Description" (empty) [in dialog "New Record", under "More", in the dialog\'s scroll area, below the visible part: act on it directly]' in lines


# --------------------------------------------------------------------------- fields that check what they get


class FieldActions(FakeActions):
    """Fills answer with scripted field results (by value); ``field_facts`` reads the field as scripted for the step's end."""

    def __init__(self, results: dict[str, Any], *, at_end: Any = None) -> None:
        super().__init__()
        self.results = results
        self.at_end = at_end

    async def fill(self, locator: Any, value: str, *, timeout_ms: int = 0) -> Any:
        self.calls.append(("fill", locator.md, value))
        if value in self.results:
            return self.results[value]
        return await super().fill(locator, value)

    async def field_facts(self, locator: Any, **_: Any) -> Any:
        return self.at_end


def _rejected(value: str, error: str = "Use DD/MM/YYYY.") -> Any:
    from testzeus_hercules.act.browser import ActionResult, FieldFacts

    facts = FieldFacts(value=value, invalid=True, error=error, hints={"placeholder": "DD/MM/YYYY"}, blurred=True)
    return ActionResult(ok=False, detail=f"the field marked the value invalid after it lost focus: {error}", kind="invalid", retry=False, field=facts)


def _accepted() -> Any:
    from testzeus_hercules.act.browser import ActionResult, FieldFacts

    return ActionResult(ok=True, detail="filled", field=FieldFacts(value="31/12/2027", blurred=True))


DATE_READINGS = {**READINGS, 'fill "12/31/2027" in "Username"': fills("Username", "12/31/2027")}


def test_a_field_valid_when_entered_but_invalid_at_the_end_of_the_step_fails_that_step(tmp_path: Path) -> None:
    from testzeus_hercules.act.browser import FieldFacts

    actions = FieldActions({"x": _accepted()}, at_end=FieldFacts(value="x", invalid=True, error="Too short"))
    h = Harness(tmp_path, actions=actions)

    outcome = h.run(Step(action='fill "x" in "Password"'))

    assert outcome.status == "failed"
    assert outcome.actual == "expected field valid 'Password': the page marks the field invalid at the end of the step: Too short"


def test_a_rejection_the_test_expects_passes_with_a_note_and_is_not_checked_again(tmp_path: Path) -> None:
    from testzeus_hercules.act.browser import FieldFacts
    from testzeus_hercules.engine.intent import FieldProposal

    interpreter = ScriptedInterpreter(DATE_READINGS, field_answers=[FieldProposal("expected", because="should see an error")])
    h = Harness(tmp_path, actions=FieldActions({"12/31/2027": _rejected("12/31/2027")}, at_end=FieldFacts(value="12/31/2027", invalid=True)), interpreter=interpreter)

    outcome = h.run(Step(action='fill "12/31/2027" in "Username"'), index=3)

    assert outcome.status == "passed", outcome.actual
    assert outcome.notes == ('the "Username" field rejected "12/31/2027" as the test expects (\'should see an error\'): Use DD/MM/YYYY.',)
    (call,) = interpreter.field_calls
    assert call[1].error == "Use DD/MM/YYYY." and call[1].hints == ("placeholder: DD/MM/YYYY",)


def test_a_rewritten_value_is_entered_only_when_the_decider_judges_it_the_same(tmp_path: Path) -> None:
    from testzeus_hercules.engine.intent import FieldProposal

    interpreter = ScriptedInterpreter(DATE_READINGS, field_answers=[FieldProposal("reformat", value="31/12/2027", because="Use DD/MM/YYYY")])
    actions = FieldActions({"12/31/2027": _rejected("12/31/2027"), "31/12/2027": _accepted()})
    h = Harness(tmp_path, actions=actions, interpreter=interpreter, decider=LabelDecider(judge=0.95))

    outcome = h.run(Step(action='fill "12/31/2027" in "Username"'))

    assert outcome.status == "passed", outcome.actual
    assert [call for call in actions.calls if call[0] == "fill"] == [("fill", 2, "12/31/2027"), ("fill", 2, "31/12/2027")]
    assert outcome.notes == ('entered "31/12/2027" for "12/31/2027" in the "Username" field to match the field\'s format (error: Use DD/MM/YYYY.)',)
    assert outcome.automation is not None and outcome.automation.actions[0].value == "31/12/2027"

    unsure = Harness(
        tmp_path / "unsure",
        actions=FieldActions({"12/31/2027": _rejected("12/31/2027")}),
        interpreter=ScriptedInterpreter(DATE_READINGS, field_answers=[FieldProposal("reformat", value="31/12/2027", because="Use DD/MM/YYYY")]),
        decider=LabelDecider(judge=0.6),
    )
    refused = unsure.run(Step(action='fill "12/31/2027" in "Username"'))
    assert refused.status == "failed" and refused.error_class == "FieldRejected"
    assert refused.actual == 'the "Username" field rejected "12/31/2027": Use DD/MM/YYYY.'


def test_a_secret_never_reaches_the_field_question(tmp_path: Path) -> None:
    from testzeus_hercules.act.browser import ActionResult, FieldFacts

    rejected = ActionResult(ok=False, detail="invalid", kind="invalid", retry=False, field=FieldFacts(value=SECRET, invalid=True, error=f"{SECRET} is too weak"))
    interpreter = ScriptedInterpreter(READINGS)
    h = Harness(tmp_path, actions=FieldActions({SECRET: rejected}), interpreter=interpreter)

    outcome = h.run(Step(action='I fill "{{password}}" in "Password"'))

    assert outcome.status == "failed" and SECRET not in (outcome.actual or "")
    (call,) = interpreter.field_calls
    assert SECRET not in json.dumps(dataclasses.asdict(call[1])) and SECRET not in call[0]


SERIAL = "TAG-2025-001"
SERIAL_READINGS = {**READINGS, f'fill "{SERIAL}" in "Username"': fills("Username", SERIAL)}


def _too_long(value: str, limit: int) -> Any:
    from testzeus_hercules.act.browser import ActionResult, FieldFacts

    detail = f"fill failed: the field accepts at most {limit} characters; the step's value has {len(value)} ({value!r})"
    return ActionResult(ok=False, detail=detail, kind="too_long", retry=False, field=FieldFacts(value="", maxlength=limit))


def test_sample_data_too_long_for_its_field_is_replaced_by_data_that_fits_and_recorded(tmp_path: Path) -> None:
    from testzeus_hercules.act.browser import ActionResult, FieldFacts
    from testzeus_hercules.engine.intent import FieldProposal

    interpreter = ScriptedInterpreter(SERIAL_READINGS, field_answers=[FieldProposal("substitute", value="TAG-25-001", because="at most 10 characters")])
    fits = ActionResult(ok=True, detail="filled", field=FieldFacts(value="TAG-25-001", maxlength=10, blurred=True))
    actions = FieldActions({SERIAL: _too_long(SERIAL, 10), "TAG-25-001": fits})
    h = Harness(tmp_path, actions=actions, interpreter=interpreter)
    h.deps.steps = (f'fill "{SERIAL}" in "Username"', 'click "Save"', 'I should see "Saved"')

    outcome = h.run(Step(action=f'fill "{SERIAL}" in "Username"'), index=1)

    assert outcome.status == "passed", outcome.actual
    assert [call for call in actions.calls if call[0] == "fill"] == [("fill", 2, SERIAL), ("fill", 2, "TAG-25-001")]
    (call,) = interpreter.field_calls
    assert (call[1].limit, call[1].substitute) == (10, "allowed")
    assert outcome.notes == (f'entered "TAG-25-001" instead of "{SERIAL}" in the "Username" field: test data changed, the field cannot take the value (the field\'s limit: at most 10 characters)',)
    (amendment,) = outcome.amendments
    assert (amendment.kind, amendment.before, amendment.after) == ("substitute", f'fill "Username" with "{SERIAL}"', 'fill "Username" with "TAG-25-001"')
    assert outcome.automation is not None and outcome.automation.actions[0].value == "TAG-25-001"


@pytest.mark.parametrize(
    ("later", "strict", "why"),
    [
        (('click "Save"', f'I should see "{SERIAL}"'), False, "step 3 names it"),
        (('click "Save"',), True, "the run is strict"),
    ],
)
def test_a_value_too_long_for_its_field_is_kept_when_a_later_step_checks_it_or_the_run_is_strict(tmp_path: Path, later: tuple[str, ...], strict: bool, why: str) -> None:
    interpreter = ScriptedInterpreter(SERIAL_READINGS)
    h = Harness(tmp_path, actions=FieldActions({SERIAL: _too_long(SERIAL, 10)}), interpreter=interpreter)
    h.deps.steps = (f'fill "{SERIAL}" in "Username"', *later)
    h.deps.strict = strict

    outcome = h.run(Step(action=f'fill "{SERIAL}" in "Username"'), index=1)

    assert (outcome.status, outcome.error_class) == ("failed", "ValueTooLong")
    assert outcome.actual == f"the field accepts at most 10 characters; the step's value has 12 ('{SERIAL}'); the value was not replaced: {why}"
    assert interpreter.field_calls == [] and outcome.amendments == ()


def test_a_secret_too_long_for_its_field_is_never_replaced(tmp_path: Path) -> None:
    interpreter = ScriptedInterpreter(READINGS)
    h = Harness(tmp_path, actions=FieldActions({SECRET: _too_long(SECRET, 4)}), interpreter=interpreter)

    outcome = h.run(Step(action='I fill "{{password}}" in "Password"'))

    assert (outcome.status, outcome.error_class) == ("failed", "ValueTooLong")
    assert interpreter.field_calls == [] and SECRET not in (outcome.actual or "")


class SubmitChecksActions(FieldActions):
    """A page that checks the date's format only when the form is submitted: after a click, the field holding the
    month-first date is invalid until it is entered again."""

    def __init__(self) -> None:
        super().__init__({"12/31/2027": _accepted(), "31/12/2027": _accepted()})
        self.value = ""

    async def fill(self, locator: Any, value: str, *, timeout_ms: int = 0) -> Any:
        self.value = value
        return await super().fill(locator, value)

    async def field_facts(self, locator: Any, **_: Any) -> Any:
        from testzeus_hercules.act.browser import FieldFacts

        submitted = any(call[0] == "click" for call in self.calls)
        if submitted and self.value == "12/31/2027":
            return FieldFacts(value=self.value, invalid=True, error="Your entry does not match the allowed format 31/12/2024.")
        return FieldFacts(value=self.value)


class SubmitChecksPerceiver(FakePerceiver):
    """The login page as read: the Username field is marked invalid while the page rejects its value."""

    def __init__(self, actions: SubmitChecksActions) -> None:
        super().__init__()
        self.actions = actions

    async def snapshot(self, page: Any, seq: int, focus_terms: Sequence[str] = ()) -> Any:
        base = await super().snapshot(page, seq, focus_terms)
        if not any(call[0] == "click" for call in self.actions.calls) or self.actions.value != "12/31/2027":
            return base
        elements = tuple(dataclasses.replace(element, attrs={**element.attrs, "aria-invalid": "true"}) if element.name == "Username" else element for element in base.elements)
        return dataclasses.replace(base, elements=elements)


def test_a_field_the_page_rejects_on_submit_is_entered_again_and_the_form_submitted_again(tmp_path: Path) -> None:
    from testzeus_hercules.engine.intent import FieldProposal

    interpreter = ScriptedInterpreter(DATE_READINGS, field_answers=[FieldProposal("reformat", value="31/12/2027", because="allowed format 31/12/2024")])
    actions = SubmitChecksActions()
    h = Harness(tmp_path, actions=actions, interpreter=interpreter, decider=LabelDecider(judge=0.95), perceiver=SubmitChecksPerceiver(actions))
    h.deps.steps = ('fill "12/31/2027" in "Username"', 'click "Sign in"')

    filled = h.run(Step(action='fill "12/31/2027" in "Username"'), index=1)
    submitted = h.run(Step(action='click "Sign in"'), index=2)

    assert filled.status == "passed" and submitted.status == "passed", submitted.actual
    assert [call[:3] for call in actions.calls if call[0] in ("fill", "click")] == [("fill", 2, "12/31/2027"), ("click", 6), ("fill", 2, "31/12/2027"), ("click", 6)]
    (call,) = interpreter.field_calls
    assert call[0] == 'fill "12/31/2027" in "Username"' and call[1].error == "Your entry does not match the allowed format 31/12/2024."
    (amendment,) = submitted.amendments
    assert (amendment.step, amendment.kind, amendment.after) == (1, "reformat", 'fill "Username" with "31/12/2027"')
    assert "when step 2 submitted the form" in amendment.reason
    assert submitted.automation is not None
    assert [(action.kind, action.value) for action in submitted.automation.actions] == [("click", None), ("fill", "31/12/2027"), ("click", None)], "the export replays the fix"
    assert submitted.notes == (
        'entered "31/12/2027" for "12/31/2027" in the "Username" field (step 1) after the page rejected it on this step (Your entry does not match the allowed format 31/12/2024.): the same value written the field\'s way; clicked again',
    )


def test_a_field_rejected_on_submit_that_cannot_be_fixed_is_a_note_and_nothing_is_clicked_again(tmp_path: Path) -> None:
    interpreter = ScriptedInterpreter(DATE_READINGS)
    actions = SubmitChecksActions()
    h = Harness(tmp_path, actions=actions, interpreter=interpreter, perceiver=SubmitChecksPerceiver(actions))

    h.run(Step(action='fill "12/31/2027" in "Username"'), index=1)
    submitted = h.run(Step(action='click "Sign in"'), index=2)

    assert submitted.status == "passed" and submitted.amendments == ()
    assert [call[0] for call in actions.calls if call[0] == "click"] == ["click"]
    assert submitted.notes == (
        'the "Username" field (step 1) is marked invalid after this step: Your entry does not match the allowed format 31/12/2024.; it was not entered again (no value the field takes means the same)',
    )


def test_a_disabled_field_asks_whether_a_later_field_step_must_run_first(tmp_path: Path) -> None:
    interpreter = ScriptedInterpreter(READINGS, first_answers=[(4, "Username")])
    h = Harness(tmp_path, actions=FakeActions(disabled_mds=[3]), interpreter=interpreter)
    h.deps.later = ((4, 'fill "alice" in "Username"'),)

    outcome = h.run(Step(action='fill "x" in "Password"'))

    assert (outcome.status, outcome.error_class, outcome.run_first) == ("failed", "NeedsEarlierStep", 4)
    (call,) = interpreter.first_calls
    assert "the element is disabled" in call[1] and call[2] == ((4, 'fill "alice" in "Username"'),)


def test_an_upload_passes_every_file_it_names_and_an_upload_without_a_file_is_ambiguous(tmp_path: Path) -> None:
    readings = {
        **READINGS,
        'I upload "a.png" and "b.pdf" to "Password"': intent("upload", target="Password", value="a.png | b.pdf"),
        'I upload to "Password"': intent("upload", target="Password", value=" | "),
    }
    h = Harness(tmp_path, interpreter=ScriptedInterpreter(readings))

    attached = h.run(Step(action='I upload "a.png" and "b.pdf" to "Password"'))
    nothing = h.run(Step(action='I upload to "Password"'), index=2)

    assert attached.status == "passed", attached.actual
    assert h.actions.calls == [("upload", 3, ("a.png", "b.pdf"))]
    assert nothing.status == "failed" and "which file to upload" in (nothing.actual or "")


def test_a_download_is_kept_remembered_and_checked_in_code(tmp_path: Path) -> None:
    readings = {
        **READINGS,
        'I click "Password" to download the report': intent("download", target="Password"),
        'I download "orders.csv" from "/reports/orders.csv"': intent("download", url="/reports/orders.csv", value="orders.csv"),
        "the downloaded file has 2 rows": intent("assert_file_rows", expected="2"),
        'column "Status" is "Paid" in the downloaded file': intent("assert_file_column", expected="Paid", target="Status", match="equals"),
    }
    h = Harness(tmp_path, interpreter=ScriptedInterpreter(readings))

    clicked = h.run(Step(action='I click "Password" to download the report', expected="the downloaded file has 2 rows"))
    opened = h.run(Step(action='I download "orders.csv" from "/reports/orders.csv"', expected='column "Status" is "Paid" in the downloaded file'), index=2)

    assert clicked.status == "passed", clicked.actual
    assert opened.status == "passed", opened.actual
    assert h.actions.calls[0] == ("download", 3, None) and h.actions.calls[1] == ("download_from_url", "/reports/orders.csv", "orders.csv")
    assert [path.name for path in h.deps.downloads] == ["report.csv", "orders.csv"]
    assert h.deps.downloads[0].parent == h.run_dir / "steps" / "1" / "downloads"
    assert "hercules://runs/r_1/steps/1/downloads/report.csv" in clicked.evidence
    assert h.data.lookup("downloaded_file_name") == "orders.csv"
    assert [entry.check for entry in opened.ledger] == ["file_column"] and opened.ledger[0].source == "deterministic"


def test_a_file_check_before_any_download_fails(tmp_path: Path) -> None:
    readings = {**READINGS, "the downloaded file is a PDF": intent("assert_file_type", expected="PDF")}
    h = Harness(tmp_path, interpreter=ScriptedInterpreter(readings))

    outcome = h.run(Step(action="verify", expected="the downloaded file is a PDF"))

    assert outcome.status == "failed" and "no file was downloaded" in (outcome.actual or "")
