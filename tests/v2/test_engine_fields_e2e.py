"""End to end on a form whose fields check what they get (``record_form.html``), in real Chromium through a
``hercules worker`` with the fake local model server.

* a date field (a web component) that accepts only day/month/year rejects "12/31/2027" when it loses focus: the
  model writes the same date the field's way ("31/12/2027"), the decider agrees it means the same, the value is
  entered again and the step passes with a note; a proposal that changes the date is refused and the step fails
  with the field's own error;
* a phone field that reformats itself on blur passes when the decider judges the new text the same number;
* a value longer than a field accepts fails with the field's limit and the ``test_data`` classification;
* a region that depends on the country, written region first, passes: once because the case reading says the
  region step needs the country step (the block runs country first), once because the run-time question
  (``run_first``) moves the country step ahead when the region turns out to be disabled.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from testzeus_hercules.engine.browser_process import BROWSER_FILE, stop_browser

from tests.v2.browser_fixtures import FIXTURE_PAGES, chromium_executable
from tests.v2.engine_fixtures.browser import serve_fixtures
from tests.v2.fixtures.daemon_support import isolated_env
from tests.v2.fixtures.fake_model import FakeModel, action

OPEN = 'I open "/record_form.html"'
DATE = 'I enter "12/31/2027" in the "Expiry Date" field'
PHONE = 'I enter "+1 555 123 4567" in the "Phone" field'
SERIAL = 'I enter "TAG-2025-001" in the "Serial Number" field'
REGION = 'I select "Ontario" from the "Region" dropdown'
COUNTRY = 'I select "Canada" from the "Country" dropdown'
SAVE = 'I click the "Save" button'
DATE_ERROR = "Enter the date as DD/MM/YYYY."


@pytest.fixture()
def chromium(tmp_path: Path) -> Iterator[str]:
    executable = chromium_executable()
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for worker tests")
    yield executable or ""
    for record in tmp_path.glob(f"**/.hercules/runs/*/{BROWSER_FILE}"):
        stop_browser(record.parent)


def _model() -> FakeModel:
    model = FakeModel()
    model.add(OPEN, action("navigate", url="/record_form.html"))
    model.add(DATE, action("fill", target="Expiry Date", value="12/31/2027"))
    model.add(PHONE, action("fill", target="Phone", value="+1 555 123 4567"))
    model.add(SERIAL, action("fill", target="Serial Number", value="TAG-2025-001"))
    model.add(REGION, action("select", target="Region", value="Ontario"))
    model.add(COUNTRY, action("select", target="Country", value="Canada"))
    model.add(SAVE, action("click", target="Save", role="button"))
    return model


def _sees(model: FakeModel, text: str) -> str:
    step = f'I should see "{text}"'
    model.add(step, action("assert_text", expected=text))
    return step


def _run(tmp_path: Path, chromium: str, model: FakeModel, steps: list[str], name: str) -> tuple[dict[str, Any], Path, list[dict[str, Any]]]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    run_id = f"r_20261027_120000_{name}"
    with serve_fixtures(FIXTURE_PAGES) as base_url:
        case = {"id": f"fields-{name}", "title": "Record form", "environment": {"base_url": base_url}, "steps": [{"action": text} for text in steps]}
        case_path = tmp_path / "inputs" / f"{name}.json"
        case_path.parent.mkdir(exist_ok=True)
        case_path.write_text(json.dumps(case), encoding="utf-8")
        run_dir = project / ".hercules" / "runs" / run_id
        run_dir.mkdir(parents=True)
        with model.env() as model_env:
            env = isolated_env(tmp_path, HERCULES_CHROMIUM=chromium, **model_env)
            command = [sys.executable, "-m", "testzeus_hercules.cli.main", "worker", "--run-id", run_id, "--case", str(case_path), "--project", str(project)]
            with (run_dir / "worker.log").open("wb") as log:
                proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=log, env=env, cwd=str(project), timeout=240)
    result = json.loads((run_dir / "run.json").read_text())
    assert proc.returncode in (0, 1), (run_dir / "worker.log").read_text()[-3000:]
    events = [json.loads(line) for line in proc.stdout.decode().splitlines() if line.startswith("{")]
    return result, run_dir, events


def _plan(run_dir: Path, step: int) -> list[dict[str, Any]]:
    path = run_dir / "steps" / str(step) / "plan.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _started(events: list[dict[str, Any]]) -> list[int]:
    return [event["data"]["step"] for event in events if event.get("kind") == "step_started"]


def _why(result: dict[str, Any], run_dir: Path) -> Any:
    return [(step["index"], step["status"], step.get("actual"), step.get("notes")) for step in result["steps"]], (run_dir / "worker.log").read_text()[-2000:]


def test_a_date_the_field_rejects_is_entered_again_the_fields_way_and_a_reformatted_phone_passes(tmp_path: Path, chromium: str) -> None:
    model = _model()
    check = _sees(model, "Saved: Expiry 31/12/2027, Phone (555) 123-4567")
    model.field_replies = [{"outcome": "reformat", "value": "31/12/2027", "same_meaning": True, "because": "Enter the date as DD/MM/YYYY"}]

    result, run_dir, events = _run(tmp_path, chromium, model, [OPEN, DATE, PHONE, SAVE, check], "date")

    assert result["status"] == "passed", _why(result, run_dir)
    date, phone = result["steps"][1], result["steps"][2]
    assert date["notes"] == ['entered "31/12/2027" for "12/31/2027" in the "Expiry Date" field to match the field\'s format (error: Enter the date as DD/MM/YYYY.)']
    assert any('entered "31/12/2027"' in hint for hint in result["hints"])
    assert phone["notes"] and phone["notes"][0].startswith('the "Phone" field shows "(555) 123-4567" for "+1 555 123 4567" once it lost focus')
    (prompt,) = model.field_prompts
    assert f"error: {DATE_ERROR}" in prompt and "placeholder: DD/MM/YYYY" in prompt and 'entered: "12/31/2027"' in prompt
    assert "LATER STEPS:" in prompt and PHONE in prompt
    field = [record for record in _plan(run_dir, 2) if record.get("stage") == "field"]
    assert field and field[0]["outcome"] == "reformat" and field[0]["value"] == "31/12/2027"
    meanings = [json.loads(line) for line in (run_dir / "steps" / "2" / "decisions.jsonl").read_text().splitlines() if json.loads(line)["purpose"] == "meaning"]
    assert meanings and meanings[0]["probability"] >= 0.8
    assert result["automation"]["steps"][1]["actions"][0]["value"] == "31/12/2027", "the export replays the value that worked"


def test_a_proposal_that_changes_the_date_is_refused_and_the_step_fails_with_the_fields_error(tmp_path: Path, chromium: str) -> None:
    model = _model()
    check = _sees(model, "Saved: Expiry 12/12/2027")
    wrong = {"outcome": "reformat", "value": "12/12/2027", "same_meaning": True, "because": "Enter the date as DD/MM/YYYY"}
    model.field_replies = [wrong, wrong]
    model.meaning = {"12/12/2027": 0.1}

    result, run_dir, events = _run(tmp_path, chromium, model, [OPEN, DATE, SAVE, check], "wrongdate")

    assert result["status"] == "failed", _why(result, run_dir)
    date = result["steps"][1]
    assert date["status"] == "failed"
    assert date["actual"] == f'the "Expiry Date" field rejected "12/31/2027": {DATE_ERROR}'
    assert [step["status"] for step in result["steps"][2:]] == ["skipped", "skipped"]
    assert len(model.field_prompts) == 2 and "REJECTED" in model.field_prompts[1] and "judged not to mean the same" in model.field_prompts[1]
    assert not date["notes"]


def test_a_value_longer_than_the_field_accepts_fails_as_test_data_when_nothing_that_fits_is_proposed(tmp_path: Path, chromium: str) -> None:
    model = _model()

    result, run_dir, events = _run(tmp_path, chromium, model, [OPEN, SERIAL, SAVE], "serial")

    assert result["status"] == "failed", _why(result, run_dir)
    serial = result["steps"][1]
    assert serial["status"] == "failed"
    assert serial["actual"] == "the field accepts at most 10 characters; the step's value has 12"
    assert result["failure"]["category"] == "test_data" and result["failure"]["confidence"] == 1.0
    assert len(model.field_prompts) == 1 and "limit: the field takes at most 10 characters" in model.field_prompts[0]


def test_sample_data_longer_than_the_field_accepts_is_replaced_by_data_that_fits(tmp_path: Path, chromium: str) -> None:
    model = _model()
    model.field_replies = [{"outcome": "substitute", "value": "TAG-25-001", "same_meaning": False, "because": "at most 10 characters"}]

    result, run_dir, events = _run(tmp_path, chromium, model, [OPEN, SERIAL, SAVE], "serial_fits")

    assert result["status"] == "passed", _why(result, run_dir)
    assert "substitute: allowed" in model.field_prompts[0]
    assert result["steps"][1]["notes"] == [
        'entered "TAG-25-001" instead of "TAG-2025-001" in the "Serial Number" field: test data changed, the field cannot take the value (the field\'s limit: at most 10 characters)'
    ]
    plan = json.loads((run_dir / "plan.json").read_text())
    assert [(item["step"], item["kind"], item["after"]) for item in plan["amendments"]] == [(2, "substitute", 'fill "Serial Number" with "TAG-25-001"')]
    assert result["automation"]["steps"][1]["actions"][0]["value"] == "TAG-25-001", "the export replays the value that worked"


def test_a_region_written_before_its_country_runs_after_it_when_the_reading_says_it_depends_on_it(tmp_path: Path, chromium: str) -> None:
    model = _model()
    check = _sees(model, "Saved: Country Canada, Region Ontario")
    model.needs = {REGION: [3]}

    result, run_dir, events = _run(tmp_path, chromium, model, [OPEN, REGION, COUNTRY, SAVE, check], "needs")

    assert result["status"] == "passed", _why(result, run_dir)
    region, country = result["steps"][1], result["steps"][2]
    assert (region["index"], region["sequence"]) == (2, 3) and (country["index"], country["sequence"]) == (3, 2)
    assert country["notes"] == ['run before step 2 because the "Region" field depends on it']
    assert region["notes"] == ["run after step 3, whose field (Country) it depends on"]
    assert result["steps"][0]["sequence"] is None and result["steps"][3]["sequence"] is None
    order = [record for record in _plan(run_dir, 2) if record.get("stage") == "order"]
    assert order == [{"stage": "order", "needs": [3], "sequence": 3}]
    assert model.run_first_prompts == []
    assert _started(events) == [1, 3, 2, 4, 5]


def test_a_region_written_before_its_country_runs_after_it_when_the_run_finds_the_dependency(tmp_path: Path, chromium: str) -> None:
    model = _model()
    check = _sees(model, "Saved: Country Canada, Region Ontario")
    model.run_first_replies = [{"run_first": 3, "because": "the Country dropdown"}]

    result, run_dir, events = _run(tmp_path, chromium, model, [OPEN, REGION, COUNTRY, SAVE, check], "runfirst")

    assert result["status"] == "passed", _why(result, run_dir)
    region, country = result["steps"][1], result["steps"][2]
    assert (region["sequence"], country["sequence"]) == (3, 2)
    assert country["notes"] == ["run before step 2, whose field depends on it (found while step 2 ran)"]
    assert region["notes"] and region["notes"][0].startswith('run after step 3: the "Region" field depends on it')
    (prompt,) = model.run_first_prompts
    assert "the element is disabled" in prompt and f"step 3: {COUNTRY}" in prompt
    depends = [record for record in _plan(run_dir, 2) if record.get("stage") == "depends"]
    assert depends and depends[0]["run_first"] == 3
    assert _started(events) == [1, 2, 3, 2, 4, 5], "step 2 starts, waits for step 3, and runs again"
    assert any(event.get("kind") == "step_deferred" and event["data"]["step"] == 2 for event in events)
