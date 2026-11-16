"""The plan phase end to end in real Chromium on the records page (``records.html``): plan, approve, run.

The page ignores the first click on "New" (``?slow-new=1``). The plan's inferred criterion for that click ("the New Record
dialog is open") is unmet on the settled page, so the step is continued with that expectation and the click is done again;
the criterion then holds and the case passes, with the recovery recorded as an amendment. Once in process (a scripted step
reader and a label-matching decider, the plan reviewed and approved through the run's control), once through ``hercules
run`` with the fake local model server (the model's own case reading with criteria, risks and lint, its coverage audit and
its continuation). And a strict run of a case whose plan has a lint error stops before the browser starts.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

from testzeus_hercules.cases.gherkin import compile_feature
from testzeus_hercules.engine.control import RunControl
from testzeus_hercules.engine.ports import DefaultPerceiver
from testzeus_hercules.engine.run_graph import ChromiumLauncher, RunDeps, RunPlanOptions, run_case
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.run_context import RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

from tests.v2.browser_fixtures import FIXTURE_PAGES
from tests.v2.engine_fixtures.browser import require_chromium, serve_fixtures
from tests.v2.engine_fixtures.fakes import LabelDecider, ScriptedInterpreter, clicks, fills, opens, sees
from tests.v2.fixtures.fake_model import FakeModel, action
from tests.v2.test_plan_run import AnsweringSink

SCENARIO = """\
Feature: Records
  Scenario: Create a record on a slow list page
    Given I open "/records.html?slow-new=1"
    When I click the "New" button
    And I enter "Acme Corp" in the "Record Name" field
    And I click the "Save" button
    Then I should see "was created"
"""
STEPS = ['I open "/records.html?slow-new=1"', 'I click the "New" button', 'I enter "Acme Corp" in the "Record Name" field', 'I click the "Save" button', 'I should see "was created"']
DIALOG_OPEN = {"after": 1, "origin": "inferred", "check": "element_state", "expected": None, "target": "New Record", "value": None, "state": "open", "count": None}


class CountingLauncher(ChromiumLauncher):
    def __init__(self, executable_path: str | None) -> None:
        super().__init__(executable_path)
        self.opened = 0

    def open(self, *, viewport: tuple[int, int], headless: bool) -> Any:
        self.opened += 1
        return super().open(viewport=viewport, headless=headless)


def _context(tmp_path: Path, events: Any = None) -> RunContext:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    settings = Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")})
    telemetry = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport())
    return RunContext.create(settings, telemetry=telemetry, events=events)


def test_plan_approve_run_and_an_inferred_dialog_criterion_drives_the_recovery(tmp_path: Path) -> None:
    chromium = require_chromium()
    sink = AnsweringSink({"decision": "approve"})
    control = RunControl(events=sink)
    sink.control = control
    ctx = _context(tmp_path, sink)
    interpreter = ScriptedInterpreter(
        {
            STEPS[0]: opens("/records.html?slow-new=1"),
            STEPS[1]: clicks("New", role="button"),
            STEPS[2]: fills("Record Name", "Acme Corp"),
            STEPS[3]: clicks("Save", role="button"),
            STEPS[4]: sees("was created"),
        },
        extras={STEPS[1]: {"criteria": [DIALOG_OPEN]}},
        remaining=([clicks("New", role="button")],),
    )
    launcher = CountingLauncher(chromium)
    with serve_fixtures(FIXTURE_PAGES) as base_url:
        case = compile_feature(SCENARIO, base_url=base_url)
        deps = RunDeps(decider=LabelDecider(), interpreter=interpreter, perceiver=DefaultPerceiver(), launcher=launcher, control=control, plan=RunPlanOptions(review="ask"))
        result = asyncio.run(run_case(case, ctx, deps))
    run_dir = ctx.paths.run_dir(ctx.run_id)
    plan = json.loads((run_dir / "plan.json").read_text())

    assert result.status == "passed", result.summary_text()
    assert [event.data["kind"] for event in sink.events if event.kind == "needs_approval"] == ["plan"], "the plan was reviewed before the browser started"
    assert plan["review"]["state"] == "approved"
    (dialog,) = [item for item in plan["results"] if item["criterion"] == "2.1.c1"]
    assert (dialog["origin"], dialog["status"], dialog["recovered"]) == ("inferred", "passed", True)
    assert "'New Record'" in dialog["actual"]
    (recovery,) = [item for item in plan["amendments"] if item["kind"] == "recovery"]
    assert recovery["step"] == 2 and 'expected: the "New Record" dialog is open' in recovery["reason"] and "none" in recovery["reason"]
    assert interpreter.remaining_context[0]["expected"] == ('the "New Record" dialog is open',)
    assert result.plan is not None and result.plan.recoveries == 1 and result.plan.review == "approved"
    stages = [json.loads(line)["stage"] for line in (run_dir / "steps" / "2" / "plan.jsonl").read_text().splitlines()]
    assert stages[:4] == ["interpret", "criterion", "recover", "amend"] and stages.count("criterion") == 2


def test_strict_mode_blocks_a_case_with_a_lint_error_before_the_browser_starts(tmp_path: Path) -> None:
    chromium = require_chromium()
    ctx = _context(tmp_path)
    lint = {"lint": [{"severity": "error", "kind": "suspicious_value", "message": "the value is a Markdown link, not a record name", "suggestion": "write Acme Corp"}]}
    interpreter = ScriptedInterpreter({STEPS[2]: fills("Record Name", "[Acme Corp](https://x.test)")}, extras={STEPS[2]: lint})
    launcher = CountingLauncher(chromium)
    with serve_fixtures(FIXTURE_PAGES) as base_url:
        case = compile_feature(SCENARIO, base_url=base_url)
        deps = RunDeps(decider=LabelDecider(), interpreter=interpreter, perceiver=DefaultPerceiver(), launcher=launcher, plan=RunPlanOptions(strict=True))
        result = asyncio.run(run_case(case, ctx, deps))

    assert result.status == "error" and launcher.opened == 0
    assert result.hints[0].startswith("strict mode: the plan has 1 lint error(s); step 3: the value is a Markdown link")
    assert {step.status for step in result.steps} == {"skipped"}


def test_through_hercules_run_with_the_models_own_plan(tmp_path: Path) -> None:
    from tests.v2.test_cli_run import Harness

    chromium = require_chromium()
    model = FakeModel()
    model.add(STEPS[0], action("navigate", url="/records.html?slow-new=1"))
    model.add(STEPS[1], action("click", target="New", role="button"))
    model.add(STEPS[2], action("fill", target="Record Name", value="Acme Corp"))
    model.add(STEPS[3], action("click", target="Save", role="button"))
    model.add(STEPS[4], action("assert_text", expected="was created"))
    model.criteria[STEPS[1]] = [{"check": "element_state", "target": "New Record", "state": "open"}]
    model.criteria[STEPS[3]] = [{"check": "element_state", "target": "New Record", "state": "closed"}]
    model.risks[STEPS[3]] = {"flags": ["irreversible"], "reason": "saves the record"}
    model.continuations = [[{**action("click", target="New", role="button"), "because": 'click the "New" button'}]]
    h = Harness(tmp_path, real_browser=True, perceiver=DefaultPerceiver(), model=model, env={"HERCULES_CHROMIUM": chromium, "HERCULES_PLAN_COVERAGE": "1"})
    with serve_fixtures(FIXTURE_PAGES) as base_url:
        feature = h.write("records.feature", SCENARIO)
        out = io.StringIO()
        from testzeus_hercules.cli.main import main

        code = main(["run", str(feature), "--project", str(h.project), "--base-url", base_url, "--json"], env=h.env, run_deps=h.deps, stdout=out, stderr=h.err)
    result = json.loads(out.getvalue())
    run_dir = next((h.project / ".hercules" / "runs").iterdir())
    plan = json.loads((run_dir / "plan.json").read_text())

    assert code == 0, (result["hints"], [step.get("actual") for step in result["steps"]])
    assert plan["coverage"]["checked"] and plan["coverage"]["gaps"] == [], "the model audited the plan's coverage"
    statuses = {item["criterion"]: (item["status"], item["recovered"]) for item in plan["results"]}
    assert statuses["2.1.c1"] == ("passed", True) and statuses["4.1.c1"] == ("passed", False)
    assert [item["kind"] for item in plan["amendments"]] == ["recovery"]
    assert "EXPECTED (the plan's expectation after the actions done; the page does not show it):" in model.continue_prompts[0]
    assert {finding["code"] for finding in plan["lint"]} == {"unconfirmed_risk"}
    assert "Plan (model): 5 plan steps" in h.err.getvalue() and "1 drove a recovery" in h.err.getvalue()
