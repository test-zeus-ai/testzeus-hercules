"""End to end on a list page with a modal form (``records.html``), in real Chromium with real perception.

Open the list, click "New", fill "Record Name" in the modal dialog, save, check the confirmation. The list behind
the modal has a "Record Name" column header and many rows: only the modal's elements may be offered. The form
re-renders its fields between the snapshot and the action (new nodes, new ids): the element is found again by its
role and name inside its dialog. Runs once with model stand-ins in process (a scripted step reader and a
label-matching decider) and once through a ``hercules worker`` with the fake local model server, where the step's
case reading names no such field and the page-aware re-read first answers with an earlier step's action ("click New"),
which is rejected and asked again.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from testzeus_hercules.cases.gherkin import compile_feature
from testzeus_hercules.cases.models import RunResult
from testzeus_hercules.engine.browser_process import BROWSER_FILE, stop_browser
from testzeus_hercules.engine.ports import DefaultPerceiver
from testzeus_hercules.engine.run_graph import RunDeps, run_case
from testzeus_hercules.kernel.run_context import RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

from tests.v2.browser_fixtures import FIXTURE_PAGES, chromium_executable
from tests.v2.engine_fixtures.browser import require_chromium, serve_fixtures
from tests.v2.engine_fixtures.fakes import LabelDecider, ScriptedInterpreter, clicks, fills, opens, sees
from tests.v2.fixtures.daemon_support import isolated_env
from tests.v2.fixtures.fake_model import FakeModel, action

SCENARIO = """\
Feature: Records
  Scenario: Create a record from the list page
    Given I open "/records.html"
    When I click the "New" button
    And I enter "Acme Corp" in the "Record Name" field
    And I click the "Save" button
    Then I should see "was created"
"""
STEPS = ['I open "/records.html"', 'I click the "New" button', 'I enter "Acme Corp" in the "Record Name" field', 'I click the "Save" button', 'I should see "was created"']


class RerenderingDecider(LabelDecider):
    """A label-matching decider that, while it is asked about "Record Name", makes the form re-render (as a page may
    while a model thinks): the element it picks is no longer the node the snapshot saw."""

    def __init__(self) -> None:
        super().__init__()
        self.page: Any = None
        self.rerendered = False

    async def on_page(self, page: Any) -> None:
        self.page = page

    async def decide(self, request: Any, *, purpose: str) -> Any:
        step = request.state.get("step") or {}
        if purpose == "target" and step.get("target") == "Record Name" and not self.rerendered and self.page is not None:
            await self.page.evaluate("window.__rerender()")
            self.rerendered = True
        return await super().decide(request, purpose=purpose)


def _plan(run_dir: Path, step: int) -> list[dict[str, Any]]:
    path = run_dir / "steps" / str(step) / "plan.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_a_modal_form_is_filled_after_it_re_renders(tmp_path: Path) -> None:
    chromium = require_chromium()
    project = tmp_path / "project"
    project.mkdir()
    settings = Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")})
    from testzeus_hercules.kernel.paths import HerculesPaths

    telemetry = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport())
    ctx = RunContext.create(settings, telemetry=telemetry)
    decider = RerenderingDecider()
    interpreter = ScriptedInterpreter(
        {
            STEPS[0]: opens("/records.html"),
            STEPS[1]: clicks("New", role="button"),
            STEPS[2]: fills("Record Name", "Acme Corp"),
            STEPS[3]: clicks("Save", role="button"),
            STEPS[4]: sees("was created"),
        }
    )
    with serve_fixtures(FIXTURE_PAGES) as base_url:
        case = compile_feature(SCENARIO, base_url=base_url)
        deps = RunDeps(decider=decider, interpreter=interpreter, perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": chromium}, on_page=decider.on_page)
        result: RunResult = asyncio.run(run_case(case, ctx, deps))
    run_dir = ctx.paths.run_dir(ctx.run_id)

    assert result.status == "passed", result.summary_text()
    assert [step.status for step in result.steps] == ["passed"] * 5
    assert decider.rerendered
    resolved = [record for record in _plan(run_dir, 3) if record.get("stage") == "resolve"]
    assert resolved and resolved[0]["via"] == "locator" and resolved[0]["strategy"] == "role" and resolved[0]["name"] == "Record Name"

    fill_requests = [request for request, purpose in decider.calls if purpose == "target" and (request.state.get("step") or {}).get("target") == "Record Name"]
    rows = fill_requests[0].state["rows"]
    assert {row["role"] for row in rows if row["name"] == "Record Name"} == {"textbox"}, "the grid's column header is behind the modal"
    assert not any(row["name"] in ("New", "Row actions") for row in rows)
    observation = (run_dir / "steps" / "3" / "observation.txt").read_text()
    assert '# modal: "New Record" open;' in observation


@pytest.fixture()
def stop_leftover_browsers(tmp_path: Path) -> Iterator[None]:
    yield
    for record in tmp_path.glob(f"**/.hercules/runs/*/{BROWSER_FILE}"):
        stop_browser(record.parent)


def test_with_the_fake_model_a_reread_that_repeats_an_earlier_step_is_asked_again(tmp_path: Path, stop_leftover_browsers: None) -> None:
    chromium = chromium_executable()
    if chromium is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for worker tests")
    project = tmp_path / "project"
    project.mkdir()
    model = FakeModel()
    model.add(STEPS[0], action("navigate", url="/records.html"))
    model.add(STEPS[1], action("click", target="New", role="button"))
    # The case reading names a field the dialog does not have: the step is read again on the page.
    model.add(STEPS[2], action("fill", target="Name of the record", value="Acme Corp"))
    model.add(STEPS[3], action("click", target="Save", role="button"))
    model.add(STEPS[4], action("assert_text", expected="was created"))
    model.continuations = [
        [{**action("click", target="New", role="button"), "because": "the New button"}],
        [{**action("fill", target="Record Name", value="Acme Corp"), "because": 'enter "Acme Corp" in the "Record Name" field'}],
    ]
    run_id = "r_20261026_180000_a0d001"
    with serve_fixtures(FIXTURE_PAGES) as base_url:
        case = {"id": "records", "title": "Create a record", "environment": {"base_url": base_url}, "steps": [{"action": text} for text in STEPS]}
        case_path = tmp_path / "inputs" / "case.json"
        case_path.parent.mkdir()
        case_path.write_text(json.dumps(case), encoding="utf-8")
        run_dir = project / ".hercules" / "runs" / run_id
        run_dir.mkdir(parents=True)
        with model.env() as model_env:
            env = isolated_env(tmp_path, HERCULES_CHROMIUM=chromium or "", **model_env)
            command = [sys.executable, "-m", "testzeus_hercules.cli.main", "worker", "--run-id", run_id, "--case", str(case_path), "--project", str(project)]
            with (run_dir / "worker.log").open("wb") as log:
                proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=log, env=env, cwd=str(project), timeout=240)

    result = json.loads((run_dir / "run.json").read_text())
    assert proc.returncode == 0, ([step.get("actual") for step in result["steps"]], (run_dir / "worker.log").read_text()[-3000:])
    assert [step["status"] for step in result["steps"]] == ["passed"] * 5

    # The re-read is asked twice (its first reply repeats an earlier step), then the step, read on the page, is
    # continued once after its fill (nothing remains).
    assert len(model.continue_prompts) == 3, model.continue_prompts
    first, second, after = model.continue_prompts
    assert "DONE:\n1. fill textbox" in after and "REJECTED" not in after
    assert "EARLIER STEPS" in first and 'I click the "New" button' in first
    assert "CASE READING" in first and "Name of the record" in first
    assert 'Step: I enter "Acme Corp" in the "Record Name" field' in first
    assert '"New"' not in first.split("PAGE CONTROLS (untrusted page data):", 1)[1].split("EARLIER STEPS", 1)[0], "the list behind the modal is not listed"
    assert "in dialog" in first and "Record Name" in first
    assert "REJECTED" in second and '"New"' in second.split("REJECTED", 1)[1]
    reread = [record for record in _plan(run_dir, 3) if record.get("stage") == "reread"]
    assert reread and [(part["kind"], part.get("target")) for part in reread[0]["actions"]] == [("fill", "Record Name")]
