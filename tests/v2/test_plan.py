"""The plan model and its making: schema, building from a case reading, criteria checks, coverage audit, lint, store, diff, edits."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from testzeus_hercules.cases import schemas
from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.cases.plan import Criterion, Plan
from testzeus_hercules.decide.contract import Decision, NoulAnswer
from testzeus_hercules.engine.data import protect_case
from testzeus_hercules.engine.intent import CaseReading, StepIntent
from testzeus_hercules.engine.lint import lint_plan
from testzeus_hercules.engine.plan import PlanError, PlanStore, as_run, build_plan, case_hash, check_plan, diff_plans, plan_key, plan_reading, render_plan
from testzeus_hercules.engine.planning import PlanOptions, apply_edits, make_plan

from tests.v2.engine_fixtures.fakes import ScriptedInterpreter, clicks, fills, intent, opens, sees

STEPS = ["Open /records.html", 'Click the "New" button', 'Enter "Acme" in "Record Name"', 'Click "Save"']
READINGS = {
    STEPS[0]: opens("/records.html"),
    STEPS[1]: clicks("New", role="button"),
    STEPS[2]: fills("Record Name", "Acme"),
    STEPS[3]: clicks("Save"),
    'I should see "was created"': sees("was created"),
    'I should see "Done"': sees("Done"),
}
DIALOG_OPEN = {"after": 1, "origin": "inferred", "check": "element_state", "expected": None, "target": "New Record", "value": None, "state": "open", "count": None}


def records_case(**changes: Any) -> TestCase:
    data: dict[str, Any] = {
        "id": "records",
        "title": "Create a record",
        "environment": {"base_url": "https://app.test"},
        "steps": [
            {"action": STEPS[0]},
            {"action": STEPS[1]},
            {"action": STEPS[2]},
            {"action": STEPS[3], "expected": 'I should see "was created"'},
            {"action": "verify", "expected": 'I should see "Done"'},
        ],
    }
    data.update(changes)
    return TestCase.model_validate(data)


def _reading(case: TestCase, extras: dict[int, dict[str, Any]] | None = None) -> CaseReading:
    intents: dict[Any, Any] = {}
    for number, step in enumerate(case.steps, start=1):
        if step.action != "verify":
            found = READINGS.get(step.action)
            if found is not None:
                intents[(number, "action")] = (step.action, found.model_copy(update={"source": "case", "complete": True}))
        if step.expected:
            intents[(number, "expected")] = (step.expected, READINGS[step.expected].model_copy(update={"source": "case"}))
    return CaseReading(intents=intents, source="model", extras=extras or {})


class FixedDecider:
    """Answers every yes/no question with ``yes``, except the coverage question of the steps in ``gaps`` (then ``low``)."""

    engine = "fixed"

    def __init__(self, gaps: dict[int, list[float]] | None = None, yes: float = 0.9) -> None:
        self.gaps = {step: list(values) for step, values in (gaps or {}).items()}
        self.yes = yes
        self.calls: list[tuple[Any, str]] = []

    async def decide(self, request: Any, *, purpose: str) -> Decision:
        self.calls.append((request, purpose))
        answers = {}
        for qid in request.questions:
            step = int(qid.split("_")[1]) if purpose == "coverage" and qid.startswith("step_") else None
            values = self.gaps.get(step) if step is not None else None
            answers[qid] = NoulAnswer(noul=values.pop(0) if values else self.yes)
        return Decision(answers=answers, engine=self.engine, model_version="fixed-1", latency_ms=3, cost_usd=0.0001)


# --------------------------------------------------------------------------- the model and its schema


def test_the_plan_schema_is_published_and_validates_a_plan() -> None:
    plan = build_plan(records_case(), _reading(records_case(), {2: {"criteria": [DIALOG_OPEN]}}), model="m")
    schema = json.loads((schemas.SCHEMA_DIR / "plan.schema.json").read_text())
    Draft202012Validator(schema).validate(json.loads(plan.model_dump_json()))
    assert schema["properties"]["steps"]["items"]["$ref"].endswith("PlanStep")


def test_a_criterion_needs_the_fields_of_its_check() -> None:
    with pytest.raises(ValidationError, match="field_value criterion needs target, value"):
        Criterion(id="1.1.c1", text="x", origin="inferred", check="field_value")
    with pytest.raises(ValidationError, match="element_state criterion needs state"):
        Criterion(id="1.1.c1", text="x", origin="author", check="element_state", target="New Record")


def test_a_plan_must_fit_its_source_steps() -> None:
    plan = build_plan(records_case(), _reading(records_case()), model="m")
    data = json.loads(plan.model_dump_json())
    data["steps"][0]["step"] = 9
    with pytest.raises(ValidationError, match="names step 9"):
        Plan.model_validate(data)
    data = json.loads(plan.model_dump_json())
    data["steps"] = [step for step in data["steps"] if step["step"] != 2]
    with pytest.raises(ValidationError, match=r"step\(s\) \[2\] have no plan step"):
        Plan.model_validate(data)
    data = json.loads(plan.model_dump_json())
    data["order"] = [1, 2]
    with pytest.raises(ValidationError, match="order must list every source step"):
        Plan.model_validate(data)


# --------------------------------------------------------------------------- building from the case reading


def test_each_action_becomes_a_plan_step_and_each_outcome_a_verify_step_with_author_criteria() -> None:
    case = records_case()
    extras = {2: {"criteria": [DIALOG_OPEN], "risk": {"flags": [], "reason": None}}, 4: {"risk": {"flags": ["irreversible"], "reason": "saves the record"}}}
    plan = build_plan(case, _reading(case, extras), model="m")

    assert [(step.id, step.kind, step.item) for step in plan.steps] == [
        ("1.1", "navigate", "action"),
        ("2.1", "click", "action"),
        ("3.1", "fill", "action"),
        ("4.1", "click", "action"),
        ("4.2", "verify", "expected"),
        ("5.1", "verify", "expected"),
    ]
    (dialog,) = plan.steps[1].criteria
    assert (dialog.id, dialog.origin, dialog.check, dialog.text) == ("2.1.c1", "inferred", "element_state", 'the "New Record" dialog is open')
    (created,) = plan.steps[4].criteria
    assert (created.origin, created.check, created.expected, created.outcome) == ("author", "text_visible", "was created", True)
    assert plan.steps[3].risk.flags == ["irreversible"] and plan.steps[3].risk.reason == "saves the record"
    assert [listed.kind for listed in plan.source_steps] == ["browser", "browser", "browser", "browser", "verify"]
    assert plan.case_hash == case_hash(case) and plan.prompt_version.startswith("case-")


def test_an_author_criterion_must_be_words_of_the_step_and_a_value_the_step_enters_is_not_repeated() -> None:
    case = records_case()
    criteria = [
        {"after": 1, "origin": "author", "check": "text_visible", "expected": "Acme"},  # words of the step: author
        {"after": 1, "origin": "author", "check": "text_visible", "expected": "Saved successfully"},  # not in the step: inferred
        {"after": 1, "origin": "inferred", "check": "field_value", "target": "Record Name", "value": "Acme"},  # checked as entered
        {"after": 1, "origin": "inferred", "check": "field_value", "target": "Phone"},  # no value: left out
    ]
    plan = build_plan(case, _reading(case, {3: {"criteria": criteria}}), model="m")
    assert [(criterion.origin, criterion.expected) for criterion in plan.steps[2].criteria] == [("author", "Acme"), ("inferred", "Saved successfully")]


def test_a_step_the_model_did_not_read_is_read_on_the_page() -> None:
    case = records_case()
    reading = _reading(case)
    reading = CaseReading(intents={key: value for key, value in reading.intents.items() if key != (2, "action")}, source="model")
    plan = build_plan(case, reading, model="m")
    assert [(step.id, step.read) for step in plan.steps_of(2)] == [("2.1", "page")]
    assert (2, "action") not in plan_reading(plan, case).intents


def test_the_plan_reads_back_as_the_reading_the_run_executes() -> None:
    case = records_case()
    plan = build_plan(case, _reading(case, {2: {"criteria": [DIALOG_OPEN], "risk": {"flags": ["destructive"], "reason": "x"}}}), model="m")
    back = plan_reading(plan, case)

    click = back.get(2, "action", case.steps[1].action)
    assert click is not None and (click.kind, click.target, click.plan_step, click.risk) == ("click", "New", "2.1", ("destructive",))
    assert [criterion.id for criterion in click.criteria] == ["2.1.c1"]
    outcome = back.get(4, "expected", case.steps[3].expected or "")
    assert outcome is not None and (outcome.kind, outcome.expected, outcome.criteria) == ("assert_text", "was created", ())
    assert back.get(5, "expected", case.steps[4].expected or "") is not None


def test_a_reviewed_order_and_a_reviewers_criterion_read_back() -> None:
    case = records_case()
    plan = build_plan(case, _reading(case), model="m")
    data = json.loads(plan.model_dump_json())
    data["steps"][4]["criteria"].append({"id": "4.2.c2", "text": "Name holds Acme", "origin": "author", "check": "field_value", "target": "Record Name", "value": "Acme", "outcome": True})
    edited = Plan.model_validate(data)
    from testzeus_hercules.engine.plan import outcome_extras

    assert [criterion.id for criterion in outcome_extras(edited, 4)] == ["4.2.c2"], "a check no outcome check runs is checked by the plan at the end of the step"


def test_a_plan_of_another_case_version_is_refused() -> None:
    plan = build_plan(records_case(), _reading(records_case()), model="m")
    changed = records_case(title="Create a record (v2)")
    with pytest.raises(PlanError, match="another version"):
        check_plan(plan, changed)


# --------------------------------------------------------------------------- lint


def test_lint_reports_the_models_findings_and_the_facts() -> None:
    case = TestCase.model_validate(
        {
            "id": "lint",
            "title": "Lint",
            "environment": {"base_url": "https://app.test"},
            "test_data": {"password": {"secret": "APP_PASSWORD"}},
            "steps": [
                {"action": "Open https://admin:hunter2@app.test/login"},
                {"action": 'Fill "[Acme" in "Company"'},
                {"action": "Fill the link [docs](https://x.test) in Website"},
                {"action": 'Type "{{password}}" into "Password"'},
                {"action": "Count the rows of the orders table in the database"},
                {"action": 'Click "Delete account"'},
            ],
        }
    )
    protected, hidden = protect_case(case)
    assert list(hidden) == ["url_credential_1"]
    reading = CaseReading(
        intents={
            (1, "action"): (protected.steps[0].action, opens("{{url_credential_1}}").model_copy(update={"source": "case"})),
            (2, "action"): (protected.steps[1].action, fills("Company", "[Acme").model_copy(update={"source": "case"})),
            (3, "action"): (protected.steps[2].action, fills("Website", "[docs](https://x.test)").model_copy(update={"source": "case"})),
            (4, "action"): (protected.steps[3].action, fills("Password", "{{password}}").model_copy(update={"source": "case"})),
            (5, "action"): (protected.steps[4].action, intent("sql").model_copy(update={"source": "case"})),
            (6, "action"): (protected.steps[5].action, clicks("Delete account").model_copy(update={"source": "case"})),
        },
        source="model",
        extras={
            2: {"lint": [{"severity": "warning", "kind": "suspicious_value", "message": 'the value "[Acme" has a stray bracket', "suggestion": 'write "Acme"'}]},
            3: {"lint": [{"severity": "error", "kind": "suspicious_value", "message": "the value is a Markdown link, not a plain address", "suggestion": "write https://x.test"}]},
            6: {"risk": {"flags": ["destructive", "irreversible"], "reason": "deletes the account"}},
        },
    )
    plan = build_plan(protected, reading, model="m")
    plan = lint_plan(protected, plan, env={}, hidden=list(hidden), extras=reading.extras)
    found = {(finding.code, finding.step, finding.severity, finding.source) for finding in plan.lint}

    assert ("suspicious_value", 2, "warning", "model") in found, "a stray bracket"
    assert ("suspicious_value", 3, "error", "model") in found, "a Markdown link where a plain value is expected"
    assert ("credential_in_url", 1, "error", "fact") in found
    assert ("missing_secret", 4, "error", "fact") in found
    assert ("unsupported_step", 5, "error", "fact") in found, "a database query with no connection configured"
    assert ("unconfirmed_risk", 6, "warning", "fact") in found
    assert [finding.severity for finding in plan.lint] == sorted((finding.severity for finding in plan.lint), key=["error", "warning", "info"].index), "errors first"
    assert "hunter2" not in plan.model_dump_json()
    asked = lint_plan(protected, plan.model_copy(update={"lint": []}), env={"APP_PASSWORD": "x"}, hidden=list(hidden), confirm_destructive=True)
    assert not {finding.code for finding in asked.lint} & {"missing_secret", "unconfirmed_risk"}


def test_a_placeholder_no_data_provides_is_a_lint_error() -> None:
    case = records_case()
    reading = _reading(case)
    intents = dict(reading.intents)
    intents[(3, "action")] = (case.steps[2].action, fills("Record Name", "{{company}}").model_copy(update={"source": "case"}))
    plan = lint_plan(case, build_plan(case, CaseReading(intents=intents, source="model"), model="m"), env={})
    assert [(finding.code, finding.step) for finding in plan.lint_errors] == [("unresolved_placeholder", 3)]


# --------------------------------------------------------------------------- making a plan: coverage audit, repair, store


def _make(case: TestCase, interpreter: Any, decider: Any, store: PlanStore | None = None, *, model_id: str = "model-a", **options: Any) -> Any:
    return asyncio.run(make_plan(case, interpreter=interpreter, decider=decider, model_id=model_id, store=store, env={}, options=PlanOptions(**options)))


def test_a_dropped_obligation_is_caught_by_the_audit_and_repaired() -> None:
    case = records_case(steps=[{"action": STEPS[0]}, {"action": 'Enter "Acme" in "Record Name" and click "Save"'}])
    interpreter = ScriptedInterpreter(
        {STEPS[0]: opens("/records.html"), 'Enter "Acme" in "Record Name" and click "Save"': fills("Record Name", "Acme")},
        repairs={'Enter "Acme" in "Record Name" and click "Save"': [fills("Record Name", "Acme"), clicks("Save")]},
    )
    decider = FixedDecider(gaps={2: [0.08, 0.93]})

    outcome = _make(case, interpreter, decider)

    plan = outcome.plan
    assert [call[1] for call in decider.calls] == ["coverage", "coverage"], "the whole plan, then the repaired step"
    assert list(decider.calls[1][0].questions) == ["step_2"]
    assert interpreter.repair_calls and list(interpreter.repair_calls[0]) == [2]
    assert [step.kind for step in plan.steps_of(2)] == ["fill", "click"], "the repair added the dropped click"
    assert plan.coverage.checked and [(gap.step, gap.repaired) for gap in plan.coverage.gaps] == [(2, True)] and plan.coverage.repaired == [2]
    assert not plan.lint_errors
    state = decider.calls[0][0].state
    assert state["steps"][1]["text"].startswith("Enter") and any("fill" in line for line in state["steps"][1]["plan"])


def test_a_gap_the_repair_does_not_close_is_a_lint_error() -> None:
    case = records_case()
    decider = FixedDecider(gaps={3: [0.1, 0.2]})
    interpreter = ScriptedInterpreter(READINGS, repairs={STEPS[2]: fills("Record Name", "Acme")})
    plan = _make(case, interpreter, decider).plan
    assert [(finding.code, finding.step) for finding in plan.lint_errors] == [("coverage_gap", 3)]
    assert plan.coverage.open_gaps[0].probability == 0.1


def test_a_plan_is_reused_until_the_case_or_the_model_changes(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "plans")
    interpreter = ScriptedInterpreter(READINGS)
    decider = FixedDecider()
    first = _make(records_case(), interpreter, decider, store)
    again = _make(records_case(), interpreter, decider, store)
    other_model = _make(records_case(), interpreter, decider, store, model_id="model-b")
    changed = _make(records_case(title="Create a record, again"), interpreter, decider, store)

    assert (first.origin, again.origin, other_model.origin, changed.origin) == ("model", "cache", "model", "model")
    assert len(interpreter.case_calls) == 3, "the cached plan read nothing"
    assert again.plan.timing.cached and again.plan.timing.model_calls == 0
    assert first.key == plan_key(case_hash(records_case()), "model-a") != other_model.key
    store.save_approved(first.key, first.plan.model_copy(update={"source": "approved"}))
    approved = _make(records_case(), interpreter, decider, store)
    assert approved.origin == "approved"
    assert store.latest("records") is not None


def test_a_plan_with_a_step_the_model_gave_no_reading_for_is_not_stored(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "plans")
    unread = ScriptedInterpreter({text: reading for text, reading in READINGS.items() if text != STEPS[0]})

    first = _make(records_case(), unread, FixedDecider(), store)
    again = _make(records_case(), ScriptedInterpreter(READINGS), FixedDecider(), store)

    assert any(step.read == "page" and step.kind == "unknown" for step in first.plan.steps)
    assert (first.origin, again.origin) == ("model", "model"), "the next run asks the model again"
    assert not any(step.read == "page" and step.kind == "unknown" for step in again.plan.steps)
    assert _make(records_case(), ScriptedInterpreter(READINGS), FixedDecider(), store).origin == "cache"


def test_no_audit_without_a_decider_or_when_it_is_off() -> None:
    interpreter = ScriptedInterpreter(READINGS)
    plan = _make(records_case(), interpreter, None).plan
    assert not plan.coverage.checked and plan.coverage.note == "no decider to audit with"
    off = _make(records_case(), interpreter, FixedDecider(), coverage=False).plan
    assert off.coverage.note == "coverage audit off"


# --------------------------------------------------------------------------- diff and edits


def test_the_diff_of_two_plans_and_of_a_plan_with_its_amendments() -> None:
    case = records_case()
    before = build_plan(case, _reading(case), model="m")
    reading = _reading(case, {2: {"criteria": [DIALOG_OPEN]}})
    intents = dict(reading.intents)
    intents[(4, "action")] = (case.steps[3].action, clicks("Save", role="button").model_copy(update={"source": "case", "then": (clicks("Close").model_copy(update={"source": "case"}),)}))
    after = build_plan(case, CaseReading(intents=intents, source="model", extras=reading.extras), model="m")

    diff = diff_plans(before, after)
    changed = {item["id"]: item for item in diff["changed"]}
    assert changed["2.1"]["criteria_added"] == ['inferred: the "New Record" dialog is open']
    assert changed["4.1"]["fields"]["role"] == {"before": None, "after": "button"}
    assert [item["id"] for item in diff["added"]] == ["4.3"] and diff["same_case"] and diff["order"] is None

    from testzeus_hercules.cases.plan import Amendment

    amended = before.model_copy(update={"amendments": [Amendment(step=3, kind="reread", before='fill "Record Name"', after='fill "Name" with "Acme"', reason="no such field", source="model")]})
    ran = diff_plans(amended, as_run(amended))
    assert [item["text"] for item in ran["added"]] == ['reread: fill "Name" with "Acme"']


def test_an_edit_is_checked_reread_and_recorded_as_amendments_from_the_user() -> None:
    case = records_case()
    interpreter = ScriptedInterpreter({**READINGS, 'Type "Acme Corp" into "Record Name"': fills("Record Name", "Acme Corp")})
    original = build_plan(case, _reading(case, {2: {"criteria": [DIALOG_OPEN]}}), model="m")
    data = json.loads(original.model_dump_json())
    data["steps"][2]["text"] = 'Type "Acme Corp" into "Record Name"'
    data["steps"][1]["criteria"][0]["origin"] = "author"
    edited = Plan.model_validate(data)

    result = asyncio.run(apply_edits(original, edited, case, interpreter=interpreter, by="user"))

    assert result.review.state == "edited" and result.source == "edited"
    assert result.steps[2].value == "Acme Corp", "a step whose text changed is read again"
    assert result.steps[1].criteria[0].origin == "author"
    assert {(amendment.plan_step, amendment.source, amendment.kind) for amendment in result.amendments} == {("3.1", "user", "edit"), ("2.1", "user", "edit")}
    other = json.loads(original.model_dump_json())
    other["case_hash"] = "0" * 64
    with pytest.raises(PlanError):
        asyncio.run(apply_edits(original, Plan.model_validate(other), case, interpreter=interpreter))


def test_the_plan_prints_its_steps_criteria_risks_and_lint() -> None:
    case = records_case()
    plan = build_plan(case, _reading(case, {2: {"criteria": [DIALOG_OPEN]}, 4: {"risk": {"flags": ["irreversible"], "reason": "saves"}}}), model="m")
    text = render_plan(lint_plan(case, plan, env={}))
    assert '2.1 click: click "New" button' in text
    assert '~ [inferred] the "New Record" dialog is open (element_state)' in text
    assert "[risk: irreversible - saves]" in text and "WARNING unconfirmed_risk" in text


def test_the_intent_carries_the_plan_and_the_plan_record_leaves_it_out() -> None:
    from testzeus_hercules.engine.step_graph import _plan_record

    criterion = Criterion(id="1.1.c1", text="t", origin="inferred", check="judged", expected="t")
    action = StepIntent(kind="click", target="New", plan_step="1.1", criteria=(criterion,), risk=("destructive",))
    assert _plan_record(action) == {"kind": "click", "target": "New"}
    assert action.parts()[0].criteria == (criterion,)
