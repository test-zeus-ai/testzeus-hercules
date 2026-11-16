"""End to end: import an OpenAPI spec, run the generated cases against a real local API, and run SQL cases on SQLite.

The API is the Starlette pet store in ``fixtures/api`` on a free port. API/SQL-only
cases must never start a browser (the launcher fails if asked). A case that must fail
fails; there are no false passes. The mixed case (browser steps plus an API step)
needs Chromium and is skipped without it.
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.importers.openapi import OpenAPIImportOptions, import_openapi
from testzeus_hercules.cases.models import ApiCall, RunResult, SqlCall, Step, TestCase
from testzeus_hercules.cli.main import main
from testzeus_hercules.engine.intent import LLMInterpreter, StepInterpreter
from testzeus_hercules.engine.run_graph import RunDeps, run_case
from testzeus_hercules.engine.service_intent import ServicePlan
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.run_context import RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

from tests.v2.engine_fixtures.browser import require_chromium, serve_fixtures
from tests.v2.engine_fixtures.fakes import DomPerceiver, LabelDecider, ScriptedInterpreter, clicks, fills, opens, sees
from tests.v2.fixtures.api.app import API_KEY, SPEC, serve_api


class NoBrowser:
    """A launcher that fails the test if an API/SQL-only run asks for a browser."""

    def open(self, **kwargs: Any) -> Any:
        raise AssertionError("an API/SQL-only case must not start a browser")


@pytest.fixture
def api_base() -> Any:
    with serve_api() as base:
        yield base


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def run(case: TestCase, project: Path, *, env: dict[str, str] | None = None, **deps: Any) -> tuple[RunResult, Path]:
    settings = Settings.load(project, env={"HERCULES_HOME": str(project.parent / "home")})
    telemetry = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport())
    ctx = RunContext.create(settings, telemetry=telemetry)
    options = {"decider": LabelDecider(), "launcher": NoBrowser(), "env": env or {}, **deps}
    result = asyncio.run(run_case(case, ctx, RunDeps(**options)))
    return result, ctx.paths.run_dir(ctx.run_id)


def imported(project: Path, base_url: str) -> dict[str, TestCase]:
    import_openapi(OpenAPIImportOptions(spec_path=SPEC, project_dir=project, base_url=base_url))
    return {path.stem: TestCase.model_validate_json(path.read_text()) for path in sorted((project / ".hercules" / "cases").glob("*.json"))}


# ── OpenAPI import -> run ────────────────────────────────────────────────────


def test_imported_openapi_cases_run_against_the_real_api(project: Path, api_base: str) -> None:
    cases = imported(project, api_base)
    assert sorted(cases) == ["broken-pet", "create-pet", "health", "list-pets", "show-pet-by-id"]
    outcomes = {name: run(case, project, env={"API_KEY": API_KEY}) for name, case in cases.items()}
    statuses = {name: result.status for name, (result, _) in outcomes.items()}
    assert statuses == {"broken-pet": "failed", "create-pet": "passed", "health": "passed", "list-pets": "passed", "show-pet-by-id": "passed"}

    show, run_dir = outcomes["show-pet-by-id"]
    assert show.verdict_source == "ledger" and show.assertions.total == 2 and show.assertions.passed == 2
    assert show.steps[0].actual and show.steps[0].actual.startswith(f"GET {api_base}/pets/1 -> 200 OK")
    assert any(uri.endswith("/steps/1/http.json") for uri in show.steps[0].evidence)
    exchange = json.loads((run_dir / "steps" / "1" / "http.json").read_text())
    assert exchange["response"]["status"] == 200 and json.loads(exchange["response"]["body"])["name"] == "Rex"

    broken, _ = outcomes["broken-pet"]
    step = broken.steps[0]
    assert step.status == "failed" and "schema violation" in (step.actual or "") and "'seven' is not of type 'integer'" in (step.actual or "")
    assert broken.assertions.failed == 1 and broken.assertions.passed == 1  # the status was right, the body was not

    created, created_dir = outcomes["create-pet"]
    for path in created_dir.rglob("*"):
        if path.is_file():
            assert API_KEY not in path.read_text(errors="ignore"), path


def test_wrong_status_wrong_key_and_missing_secret_never_pass(project: Path, api_base: str) -> None:
    cases = imported(project, api_base)
    show = cases["show-pet-by-id"].model_copy(update={"test_data": {"petId": "999"}})
    result, _ = run(show, project)
    assert result.status == "failed" and "404 Not Found" in (result.steps[0].actual or "")

    wrong_key, _ = run(cases["create-pet"], project, env={"API_KEY": "not-the-key"})
    assert wrong_key.status == "failed" and "401" in (wrong_key.steps[0].actual or "")
    no_key, _ = run(cases["create-pet"], project)
    assert no_key.status == "error" and "API_KEY" in (no_key.steps[0].actual or "")


def test_hercules_run_cli_on_imported_cases(project: Path, api_base: str) -> None:
    imported(project, api_base)
    env = {"HOME": str(project.parent), "HERCULES_HOME": str(project.parent / "home"), "HERCULES_TELEMETRY": "0", "API_KEY": API_KEY}
    codes = {}
    for name in ("show-pet-by-id", "create-pet", "broken-pet"):
        out = io.StringIO()
        codes[name] = main(["run", str(project / ".hercules" / "cases" / f"{name}.json"), "--decider", "stub", "--project", str(project), "--json"], env=env, stdout=out, stderr=io.StringIO())
        result = RunResult.model_validate_json(out.getvalue())
        assert result.status == ("failed" if name == "broken-pet" else "passed"), out.getvalue()
    assert codes == {"show-pet-by-id": 0, "create-pet": 0, "broken-pet": 1}


# ── hand-written API cases ───────────────────────────────────────────────────


def test_saved_values_chain_requests_and_typed_checks(project: Path, api_base: str) -> None:
    case = TestCase(
        id="pet-lifecycle",
        title="Create a pet and read it back",
        tags=["kind:api"],
        environment={"base_url": api_base},  # type: ignore[arg-type]
        test_data={"key": {"secret": "API_KEY"}, "name": "Bella"},  # type: ignore[dict-item]
        steps=[
            Step(
                action="Create Bella",
                api=ApiCall.model_validate(
                    {
                        "method": "POST",
                        "path": "/pets",
                        "headers": {"X-API-Key": "{{key}}"},
                        "body": {"name": "{{name}}", "tag": "dog"},
                        "expect": {"status": 201, "headers": [{"name": "Location", "op": "matches", "value": "^/v1/pets/\\d+$"}]},
                        "save": [{"name": "pet_id", "json_path": "$.id"}],
                    }
                ),
            ),
            Step(
                action="Read Bella back",
                api=ApiCall.model_validate(
                    {
                        "path": "/pets/{{pet_id}}",
                        "expect": {"status": 200, "openapi": "petstore_api.yaml", "json_path": [{"path": "$.id", "value": "{{pet_id}}"}, {"path": "$.name", "value": "{{name}}"}]},
                    }
                ),
            ),
            Step(
                action="List pets",
                api=ApiCall.model_validate(
                    {
                        "path": "/pets",
                        "query": {"limit": "10"},
                        "expect": {"json_path": [{"path": "$[*].name", "op": "matches", "value": "^[A-Z][a-z]+$"}, {"path": "$", "op": "count_eq", "value": 3}]},
                    }
                ),
            ),
        ],
    )
    (project / ".hercules" / "specs").mkdir(parents=True)
    (project / ".hercules" / "specs" / "petstore_api.yaml").write_text(SPEC.read_text())
    result, _ = run(case, project, env={"API_KEY": API_KEY})
    assert result.status == "passed", result.summary_text()
    assert result.assertions.total == 8 and result.assertions.passed == 8

    failing = case.model_copy(
        update={
            "steps": [
                *case.steps[:2],
                Step(action="List pets", api=ApiCall.model_validate({"path": "/pets", "query": {"limit": "10"}, "expect": {"json_path": [{"path": "$", "op": "count_eq", "value": 2}]}})),
            ]
        }
    )
    result, _ = run(failing, project, env={"API_KEY": API_KEY})
    assert result.status == "failed" and result.steps[2].status == "failed" and "expected json_count_eq $ 2: got 4" in (result.steps[2].actual or "")  # the second run created a fourth pet


def test_plain_language_api_case_is_read_by_the_model(project: Path, api_base: str) -> None:
    """A kind:api case without payloads: the (fake) model returns the structured payload; the run judges it in code."""

    class Model:
        async def complete(self, *, system: str, user: str, schema: type, images: Any = ()) -> Any:
            assert schema is ServicePlan and "kind:api" in user
            if "pet 1" in user:
                return ServicePlan(kind="api", api=ApiCall.model_validate({"path": "/pets/1", "expect": {"status": 200}}), sql=None)
            return ServicePlan(kind="api", api=ApiCall.model_validate({"expect": {"json_path": [{"path": "$.name", "value": "Tom"}]}}), sql=None)

    case = TestCase(
        id="plain",
        title="Plain-language API steps",
        tags=["kind:api"],
        environment={"base_url": api_base},  # type: ignore[arg-type]
        steps=[Step(action="Fetch pet 1"), Step(action="verify", expected="the pet is called Tom")],
    )
    result, run_dir = run(case, project, interpreter=StepInterpreter(LLMInterpreter(Model())))
    assert [step.status for step in result.steps] == ["passed", "failed"]  # Rex is not Tom: the model's reading is judged in code
    assert json.loads((run_dir / "steps" / "1" / "plan.jsonl").read_text().splitlines()[0])["stage"] == "service"


# ── SQL ──────────────────────────────────────────────────────────────────────


def test_sqlite_case_end_to_end(project: Path) -> None:
    with sqlite3.connect(project / "shop.db") as connection:
        connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, total REAL, status TEXT)")
        connection.executemany("INSERT INTO orders (customer, total, status) VALUES (?, ?, ?)", [("ada", 30.5, "paid"), ("bob", 12.0, "paid"), ("ada", 7.25, "open")])
    case = TestCase(
        id="orders",
        title="Paid orders",
        tags=["kind:sql"],
        test_data={"shop": {"secret": "SHOP_DB_URL"}},  # type: ignore[dict-item]
        steps=[
            Step(
                action="Read ada's orders",
                sql=SqlCall.model_validate(
                    {
                        "db": "shop",
                        "query": "SELECT id, total, status FROM orders WHERE customer = :who ORDER BY id",
                        "params": {"who": "ada"},
                        "expect": {"rows": 2, "json_path": [{"path": "$[*].total", "op": "all_gte", "value": 7}, {"path": "$[0].status", "value": "paid"}]},
                        "save": [{"name": "first_order", "json_path": "$[0].id"}],
                    }
                ),
            ),
            Step(
                action="Read it again by id",
                sql=SqlCall.model_validate(
                    {
                        "db": "shop",
                        "query": "SELECT customer FROM orders WHERE id = :id",
                        "params": {"id": "{{first_order}}"},
                        "expect": {"rows": 1, "json_path": [{"path": "$[0].customer", "value": "ada"}]},
                    }
                ),
            ),
        ],
    )
    env = {"SHOP_DB_URL": "sqlite:///shop.db"}
    result, run_dir = run(case, project, env=env)
    assert result.status == "passed", result.summary_text()
    assert result.assertions.passed == 5
    assert "sqlite:///shop.db" not in (run_dir / "run.json").read_text()

    wrong = case.model_copy(update={"steps": [Step(action="count", sql=SqlCall.model_validate({"db": "shop", "query": "SELECT * FROM orders", "expect": {"rows": 2}}))]})
    result, _ = run(wrong, project, env=env)
    assert result.status == "failed" and result.steps[0].actual and "3 row(s)" in result.steps[0].actual

    write = case.model_copy(update={"steps": [Step(action="wipe", sql=SqlCall.model_validate({"db": "shop", "query": "DELETE FROM orders", "expect": {"rows": 0}}))]})
    result, _ = run(write, project, env=env)
    assert result.status == "error" and "Only SELECT" in (result.steps[0].actual or "")
    with sqlite3.connect(project / "shop.db") as connection:
        assert connection.execute("SELECT count(*) FROM orders").fetchone() == (3,)


# ── mixed browser + API ──────────────────────────────────────────────────────


def test_mixed_case_runs_browser_and_api_steps(project: Path, api_base: str) -> None:
    chromium = require_chromium()
    with serve_fixtures() as site:
        case = TestCase(
            id="mixed",
            title="Sign in, then check the API",
            environment={"base_url": site},  # type: ignore[arg-type]
            test_data={"password": {"secret": "LOGIN_PW"}},  # type: ignore[dict-item]
            steps=[
                Step(action='I open "/login.html"'),
                Step(action='I fill "alice" in "Username"'),
                Step(action='I fill "{{password}}" in "Password"'),
                Step(action='I click the "Sign in" button'),
                Step(action="verify", expected='I should see "Welcome"'),
                Step(action="The pet API still knows Rex", api=ApiCall.model_validate({"path": f"{api_base}/pets/1", "expect": {"status": 200, "json_path": [{"path": "$.name", "value": "Rex"}]}})),
            ],
        )
        reader = ScriptedInterpreter(
            {
                'I open "/login.html"': opens("/login.html"),
                'I fill "alice" in "Username"': fills("Username", "alice"),
                'I fill "{{password}}" in "Password"': fills("Password", "{{password}}"),
                'I click the "Sign in" button': clicks("Sign in", role="button"),
                'I should see "Welcome"': sees("Welcome"),
            }
        )
        result, _ = run(case, project, env={"HERCULES_CHROMIUM": chromium, "LOGIN_PW": "correct-horse-42"}, launcher=None, perceiver=DomPerceiver(), interpreter=reader)
    assert result.status == "passed", result.summary_text()
    assert [step.status for step in result.steps] == ["passed"] * 6
    assert result.steps[5].actual and "-> 200 OK" in result.steps[5].actual
    assert [item.step for item in reader.case_calls[0]] == [1, 2, 3, 4, 5], "the API step with a payload is not read by the model"
