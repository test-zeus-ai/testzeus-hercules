"""Daemon endpoints added for the tester UI: step decisions, case validation, a run's case, runs with results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from testzeus_hercules.daemon.decisions import summarize
from tests.v2.fixtures.daemon_support import client_for, make_config, submit, wait_for

GHERKIN = """Feature: Sign in
  Scenario: Registered shopper signs in
    Given I open "/login.html"
    When I fill "demo@example.com" in "Email"
    And I click the "Sign in" button
    Then I should see "Signed in"
"""


def _record(*, engine: str = "local-llm", fallback: str | None = None) -> dict:
    record = {
        "purpose": "target",
        "attempt": 0,
        "snapshot": 1,
        "engine": engine,
        "latency_ms": 2977,
        "wall_ms": 2978,
        "model": "local-model",
        "cost_usd": 0.0,
        "confidence": 0.93,
        "choice": "md_11",
        "rows": 3,
        "prompt_tokens_est": 585,
        "request": {
            "questions": {
                "target": {
                    "type": "choice",
                    "instructions": "Which listed element should the test step act on?",
                    "criteria": {"md_8": 'button "Open cart"', "md_11": 'button "Add to cart"', "no_match": "None of the listed elements fits."},
                },
                "target_absent": {"type": "noul", "instructions": "The element is not listed.", "criteria": {"true": "No element fits.", "false": "One element fits."}},
            },
            "state": {
                "page": {"title": "Classic Tee", "url": "http://127.0.0.1:9/product.html", "snapshot": 1},
                "rows": [{"id": "md_11", "role": "button", "name": "Add to cart"}],
                "screenshot": {"type": "image_url", "image_url": {"url": ""}},
                "step": {"kind": "click", "role": "button", "target": "Add to cart", "text": 'I click the "Add to cart" button'},
            },
        },
        "decision": {
            "answers": {
                "target": {"type": "choice", "choice": "md_11", "confidence": 0.93, "probabilities": {"md_8": 0.05, "md_11": 0.93, "no_match": 0.02}},
                "target_absent": {"type": "noul", "noul": 0.03},
            },
            "engine": engine,
            "model_version": "local-model",
            "latency_ms": 2977,
            "cost_usd": 0.0,
            "cached": False,
        },
    }
    if fallback is not None:
        record["route"] = {
            "answered_by": "local-llm",
            "calls": [{"engine": "jev", "images": False, "latency_ms": 30000, "error": fallback}, {"engine": "local-llm", "images": True, "latency_ms": 2100, "confidence": 0.93}],
            "fallback": fallback,
        }
        record["fallback"] = fallback
    return record


@pytest.fixture()
def config(tmp_path: Path):  # type: ignore[no-untyped-def]
    return make_config(tmp_path)


def test_summarize_keeps_questions_options_and_answers_but_not_page_rows() -> None:
    view = summarize(_record())
    assert view["engine"] == "local-llm" and view["model"] == "local-model" and view["latency_ms"] == 2977
    assert view["page"] == {"title": "Classic Tee", "url": "http://127.0.0.1:9/product.html"}
    assert view["step"]["target"] == "Add to cart"
    target, absent = view["questions"]
    assert target["answer"] == "md_11" and target["confidence"] == 0.93
    assert [option["id"] for option in target["options"]] == ["md_11", "md_8", "no_match"]  # most likely first
    assert target["options"][0] == {"id": "md_11", "label": 'button "Add to cart"', "probability": 0.93}
    assert absent["answer"] == "false"
    assert {option["id"]: option["probability"] for option in absent["options"]} == {"true": 0.03, "false": 0.97}
    assert "rows" not in json.dumps(view["questions"]) and "image_url" not in json.dumps(view)


def test_summarize_reports_hybrid_fallback_and_calls() -> None:
    view = summarize(_record(engine="local-llm", fallback="DecisionError: timed out"))
    assert view["fallback"] == "DecisionError: timed out"
    assert view["answered_by"] == "local-llm"
    assert [call["engine"] for call in view["calls"]] == ["jev", "local-llm"]
    assert view["calls"][0]["error"] == "DecisionError: timed out"


def test_summarize_survives_malformed_records() -> None:
    view = summarize({"request": "not a dict", "decision": {"answers": {"x": "y"}}, "attempt": "1"})
    assert view["questions"] == [] and view["attempt"] == 0 and view["engine"] is None


def test_step_decisions_endpoint_reads_the_step_file(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        run_id = submit(client, "fake-fail")
        wait_for(client, run_id)
        step_dir = config.paths.runs_dir / run_id / "steps" / "2"
        lines = [json.dumps(_record()), "not json", json.dumps(_record(fallback="DecisionError: down"))]
        (step_dir / "decisions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        response = client.get(f"/api/runs/{run_id}/steps/2/decisions")
        empty = client.get(f"/api/runs/{run_id}/steps/1/decisions")
        missing_step = client.get(f"/api/runs/{run_id}/steps/9/decisions")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id and body["step"] == 2
    assert len(body["decisions"]) == 2
    assert body["decisions"][1]["fallback"] == "DecisionError: down"
    assert empty.json()["decisions"] == [] and missing_step.json()["decisions"] == []


@pytest.mark.parametrize("step", ["0", "-1", "abc", "1.5", "99999", "..%2F..%2Frun.json"])
def test_step_decisions_rejects_bad_step_numbers(config, step: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        run_id = submit(client)
        response = client.get(f"/api/runs/{run_id}/steps/{step}/decisions")
    assert response.status_code in (404, 422)


def test_step_decisions_for_unknown_run_is_404(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        assert client.get("/api/runs/r_20990101_000000_ffffff/steps/1/decisions").status_code == 404
        assert client.get("/api/runs/..%2F..%2Fetc/steps/1/decisions").status_code == 404


def test_step_decisions_ignores_a_symlinked_file(config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    with client_for(config) as client:
        run_id = submit(client, "fake-fail")
        wait_for(client, run_id)
        (config.paths.runs_dir / run_id / "steps" / "2" / "decisions.jsonl").symlink_to(outside)
        response = client.get(f"/api/runs/{run_id}/steps/2/decisions")
    assert response.json()["decisions"] == []


def test_validate_gherkin_returns_the_compiled_case(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        good = client.post("/api/cases/validate", json={"gherkin": GHERKIN})
        bad = client.post("/api/cases/validate", json={"gherkin": "Feature: x\n  Scenario: y\n    Blah blah\n"})
    assert good.status_code == 200
    assert good.json()["valid"] is True
    assert [step["action"] for step in good.json()["test_case"]["steps"]][0] == 'I open "/login.html"'
    assert bad.status_code == 200
    assert bad.json()["valid"] is False and bad.json()["test_case"] is None and bad.json()["error"]
    assert not list(config.paths.cases_dir.glob("*"))  # nothing was saved


def test_validate_test_case_reports_field_errors(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        good = client.post("/api/cases/validate", json={"test_case": {"id": "ok", "title": "Ok", "steps": [{"action": "Go"}]}})
        bad = client.post("/api/cases/validate", json={"test_case": {"id": "Bad Id", "title": "", "steps": []}})
        neither = client.post("/api/cases/validate", json={})
        both = client.post("/api/cases/validate", json={"gherkin": GHERKIN, "test_case": {}})
    assert good.json() == {
        "valid": True,
        "error": None,
        "test_case": {
            "id": "ok",
            "title": "Ok",
            "priority": "medium",
            "tags": [],
            "environment": {},
            "preconditions": [],
            "test_data": {},
            "steps": [{"action": "Go", "semantic": False}],
            "timeout_s": 300,
            "status": "ready",
        },
    }
    problem = bad.json()["error"]
    assert bad.json()["valid"] is False and "id" in problem and "steps" in problem
    assert neither.status_code == 422 and both.status_code == 422


def test_get_case_returns_kind_and_source(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        saved = client.post("/api/cases", json={"gherkin": GHERKIN})
        name = saved.json()["name"]
        response = client.get(f"/api/cases/{name}")
    assert response.status_code == 200
    assert response.json()["kind"] == "feature"
    assert response.json()["source"] == GHERKIN
    assert response.json()["test_case"]["title"] == "Registered shopper signs in"


def test_run_case_returns_the_case_and_source(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        run_id = submit(client, gherkin=GHERKIN, base_url="http://127.0.0.1:9")
        response = client.get(f"/api/runs/{run_id}/case")
        missing = client.get("/api/runs/r_20990101_000000_ffffff/case")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == {"kind": "gherkin", "path": None, "environment": None, "base_url": "http://127.0.0.1:9"}
    assert len(body["test_case"]["steps"]) == 4
    assert missing.status_code == 404


def test_list_runs_with_results_includes_the_run_result(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        run_id = submit(client, "fake-fail")
        wait_for(client, run_id)
        plain = client.get("/api/runs").json()["runs"][0]
        rich = client.get("/api/runs?with_result=true").json()["runs"][0]
    assert plain["result"] is None
    assert rich["run_id"] == run_id
    assert rich["result"]["status"] == "failed"
    assert [step["status"] for step in rich["result"]["steps"]][:2] == ["passed", "failed"]


def test_new_endpoints_are_in_the_openapi_document(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        spec = client.get("/api/openapi.json").json()
    for path in ("/api/runs/{run_id}/steps/{step}/decisions", "/api/cases/validate", "/api/runs/{run_id}/case"):
        assert path in spec["paths"]
    assert {"StepDecisions", "CaseValidate", "CaseCheck"} <= set(spec["components"]["schemas"])
