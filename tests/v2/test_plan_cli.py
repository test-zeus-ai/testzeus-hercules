"""``hercules plan`` and ``hercules run --plan/--strict`` against the fake local model server (the model reads the case,
with criteria, risks and lint; it audits the plan's coverage in pure mode)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from testzeus_hercules.cli.main import main

from tests.v2.fixtures.fake_model import FakeModel, action
from tests.v2.test_cli_run import LLM_ENV, Harness

STEPS = ['Open "/records.html"', 'Click the "New" button', 'Enter "[Acme" in "Record Name"']


def _model() -> FakeModel:
    model = FakeModel()
    model.add(STEPS[0], action("navigate", url="/records.html"))
    model.add(STEPS[1], action("click", target="New", role="button"))
    model.add(STEPS[2], action("fill", target="Record Name", value="[Acme"))
    model.criteria[STEPS[1]] = [{"check": "element_state", "target": "New Record", "state": "open"}]
    model.risks[STEPS[2]] = {"flags": ["destructive"], "reason": "overwrites the record"}
    model.lint[STEPS[2]] = [{"severity": "error", "kind": "suspicious_value", "message": 'the value "[Acme" has a stray bracket', "suggestion": 'write "Acme"'}]
    return model


def _case(h: Harness) -> Path:
    case = {"id": "records", "title": "New record", "environment": {"base_url": "https://app.test"}, "steps": [{"action": text} for text in STEPS]}
    return h.write("records.json", json.dumps(case))


def _plan(h: Harness, *args: str, coverage: bool = True) -> tuple[int, str]:
    out = io.StringIO()
    env = {**h.env, "HERCULES_PLAN_COVERAGE": "1" if coverage else "0"}
    code = main(["plan", *args, "--project", str(h.project)], env=env, run_deps=h.deps, stdout=out, stderr=h.err)
    return code, out.getvalue()


def test_plan_prints_the_plan_its_criteria_risks_coverage_and_lint(tmp_path: Path) -> None:
    model = _model()
    h = Harness(tmp_path, model=model, env=LLM_ENV)
    path = _case(h)

    code, out = _plan(h, str(path))

    assert code == 0, out + h.err.getvalue()
    assert "Plan of records (3 plan steps; model, generated" in out
    assert '2.1 click: click "New" button' in out and '~ [inferred] the "New Record" dialog is open (element_state)' in out
    assert "[risk: destructive - overwrites the record]" in out
    assert "Coverage (local-llm): 3 step(s) audited; gaps: none" in out
    assert 'ERROR suspicious_value (model) step 3: the value "[Acme" has a stray bracket -> write "Acme"' in out
    assert "Planning:" in out and "local model call(s)" in out
    assert model.schemas.count("CaseReadingReply") == 1 and model.coverage_prompts, "one reading, one coverage audit"


def test_plan_json_is_cached_approved_and_runs_with_run_plan(tmp_path: Path) -> None:
    model = _model()
    h = Harness(tmp_path, model=model, env=LLM_ENV)
    path = _case(h)
    out_file = tmp_path / "plan.json"

    code, out = _plan(h, str(path), "--json", "--out", str(out_file), "--approve")
    again, cached = _plan(h, str(path), "--json")
    strict, _ = _plan(h, str(path), "--strict")

    assert (code, again, strict) == (0, 0, 1), "--strict exits 1 on lint errors"
    plan = json.loads(out)
    assert plan["review"]["state"] == "approved" and json.loads(out_file.read_text())["case_id"] == "records"
    assert json.loads(cached)["source"] == "approved", "an approved plan is reused for this case version and model"
    assert model.schemas.count("CaseReadingReply") == 1, "the stored plan read nothing again"
    store = h.project / ".hercules" / "plans"
    assert list(store.glob("approved-*.json")) and list(store.glob("plan-*.json")) and (store / "case-records.json").exists()

    plan["steps"][2]["value"] = "Acme"
    plan["lint"] = []
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(plan))
    run_code, run_out = h.run(str(path), "--plan", str(edited), "--json")
    result = json.loads(run_out)
    assert result["plan"]["source"] == "approved" and result["plan"]["steps"] == 3
    run_dir = next((h.project / ".hercules" / "runs").iterdir())
    assert json.loads((run_dir / "plan.json").read_text())["steps"][2]["value"] == "Acme"
    assert run_code in (0, 1)


def test_run_strict_stops_on_lint_errors_and_a_bad_plan_file_is_refused(tmp_path: Path) -> None:
    h = Harness(tmp_path, model=_model(), env=LLM_ENV)
    path = _case(h)

    code, out = h.run(str(path), "--strict", "--json")
    bad = h.write("bad.json", "{}")
    refused, message = h.run(str(path), "--plan", str(bad))

    result = json.loads(out)
    assert code == 2 and result["status"] == "error"
    assert result["hints"][0].startswith('strict mode: the plan has 1 lint error(s); step 3: the value "[Acme" has a stray bracket')
    assert result["plan"]["lint_errors"] == 1 and result["plan"]["strict"]
    assert refused == 2 and "invalid plan in bad.json" in message


def test_plan_without_a_model_server_is_an_error(tmp_path: Path) -> None:
    from tests.v2.test_llm_fakes import FakeServer

    h = Harness(tmp_path, server=FakeServer(reachable=False), env=LLM_ENV)
    code, out = _plan(h, str(_case(h)))
    assert code == 2 and out.startswith("error:")
