"""The API/SQL step kind (``engine.service``): structured payloads executed and judged in code.

HTTP goes to an ``httpx.MockTransport``; SQL to a real SQLite file. Plain-language
steps are read by a fake structured-output model; nothing reads step text by rule.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from testzeus_hercules.cases.models import ApiCall, SqlCall, Step, TestCase
from testzeus_hercules.decide.backends.stub import StubDecider
from testzeus_hercules.engine.data import CaseData
from testzeus_hercules.engine.evidence import EvidenceStore
from testzeus_hercules.engine.intent import IntentPlan, IntentReply, LLMInterpreter, StepInterpreter
from testzeus_hercules.engine.service import ServiceReading, ServiceSession
from testzeus_hercules.engine.service_intent import SERVICE_SYSTEM_PROMPT, ServiceInterpreter, ServicePlan, case_hint
from testzeus_hercules.engine.step_graph import StepDeps, StepRunner

from tests.v2.engine_fixtures.fakes import DomPerceiver

TOKEN = "tok-secret-8841"
PETS = {1: {"id": 1, "name": "Rex", "tags": ["dog"]}}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/pets" and request.method == "POST":
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "unauthorised"})
        body = json.loads(request.content)
        pet = {"id": 2, "name": body["name"], "tags": body.get("tags", [])}
        return httpx.Response(201, json=pet, headers={"Location": "/v1/pets/2"})
    if request.url.path.startswith("/v1/pets/"):
        pet_id = request.url.path.rsplit("/", 1)[1]
        pet = PETS.get(int(pet_id)) if pet_id.isdigit() else None
        if pet_id == "2":
            pet = {"id": 2, "name": "Bella", "tags": []}
        return httpx.Response(200, json=pet) if pet else httpx.Response(404, json={"detail": "no such pet"})
    if request.url.path == "/v1/login":
        return httpx.Response(200, json={"access_token": TOKEN})
    return httpx.Response(200, text="plain text", headers={"Content-Type": "text/plain"})


class FakeModel:
    """A structured-output model: answers each schema with the queued reply."""

    def __init__(self, **replies: Any) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str, schema: type[BaseModel], images: Any = ()) -> BaseModel:
        self.calls.append((schema.__name__, user))
        reply = self.replies[schema.__name__]
        return reply(user) if callable(reply) else reply


def session(tmp_path: Path, *, test_data: dict[str, Any] | None = None, env: dict[str, str] | None = None, model: Any = None, hint: str | None = None) -> ServiceSession:
    case = TestCase(id="c", title="t", test_data=test_data or {}, steps=[Step(action="x")])
    data = CaseData.from_case(case, env=env or {})
    evidence = EvidenceStore(tmp_path / "run", "r1", redact=data.redact)
    return ServiceSession(data=data, evidence=evidence, project_dir=tmp_path, base_url="http://api.test/v1", interpreter=ServiceInterpreter(model), hint=hint, transport=httpx.MockTransport(handler))


def run_step(service: ServiceSession, index: int, step: Step) -> Any:
    async def go() -> Any:
        reading = await service.read(step)
        assert reading is not None
        return await service.run(index, reading)

    return asyncio.run(go())


def api(action: str, **payload: Any) -> Step:
    expected = payload.pop("expected", None)
    return Step(action=action, expected=expected, api=ApiCall.model_validate(payload))


# ── requests and checks ──────────────────────────────────────────────────────


def test_request_with_checks_passes_and_records_evidence(tmp_path: Path) -> None:
    service = session(tmp_path)
    outcome = run_step(
        service,
        1,
        api(
            "get Rex",
            path="/pets/1",
            expect={
                "status": 200,
                "json_path": [{"path": "$.name", "value": "Rex"}, {"path": "$.tags", "op": "contains", "value": "dog"}],
                "headers": [{"name": "content-type", "op": "contains", "value": "json"}],
                "max_time_ms": 5000,
            },
        ),
    )
    assert outcome.status == "passed", outcome.actual
    assert outcome.actual.startswith("GET http://api.test/v1/pets/1 -> 200 OK")
    assert [entry.check for entry in outcome.ledger] == ["http_status", "json_eq", "json_contains", "header_contains", "response_time_lt"]
    assert all(entry.verdict == "passed" for entry in outcome.ledger)
    exchange = json.loads((tmp_path / "run" / "steps" / "1" / "http.json").read_text())
    assert exchange["response"]["status"] == 200 and exchange["request"]["url"] == "http://api.test/v1/pets/1"
    assert outcome.evidence == ("hercules://runs/r1/steps/1/http.json", "hercules://runs/r1/steps/1/checks.json")


def test_wrong_status_and_wrong_value_fail_never_pass(tmp_path: Path) -> None:
    service = session(tmp_path)
    missing = run_step(service, 1, api("get", path="/pets/9", expect={"status": 200}))
    assert missing.status == "failed" and missing.error_class == "AssertionFailed"
    assert "404 Not Found" in missing.actual and "no such pet" in missing.actual
    wrong = run_step(service, 2, api("get", path="/pets/1", expect={"status": "2xx", "json_path": [{"path": "$.name", "value": "Tom"}]}))
    assert wrong.status == "failed" and [entry.verdict for entry in wrong.ledger] == ["passed", "failed"]
    assert 'got "Rex"' not in wrong.actual and "got Rex" in wrong.actual


def test_checks_that_cannot_be_evaluated_make_the_step_an_error(tmp_path: Path) -> None:
    service = session(tmp_path)
    no_response = run_step(service, 1, api("check", expect={"status": 200}))
    assert no_response.status == "failed" and "no request has been sent" in no_response.actual and no_response.ledger[0].verdict == "failed"
    bad_path = run_step(service, 2, api("get", path="/pets/1", expect={"json_path": [{"path": "$[", "value": 1}]}))
    assert bad_path.status == "error" and "Invalid JSONPath" in bad_path.actual
    not_json = run_step(service, 3, api("get", path="/text", expect={"json_path": [{"path": "$.a", "op": "exists"}]}))
    assert not_json.status == "failed" and "not JSON" in not_json.actual
    no_value = run_step(service, 4, api("get", path="/pets/1", expect={"json_path": [{"path": "$.name"}]}))
    assert no_value.status == "error" and "needs an 'expected' value" in no_value.actual


def test_body_contains_and_checks_on_the_previous_response(tmp_path: Path) -> None:
    service = session(tmp_path)
    assert run_step(service, 1, api("get", path="/text", expect={"body_contains": ["plain"]})).status == "passed"
    follow_up = run_step(service, 2, Step(action="verify", expected="it says plain", api=ApiCall(expect={"body_contains": ["nope"]})))
    assert follow_up.status == "failed" and follow_up.evidence[0] == "hercules://runs/r1/steps/1/http.json"


def test_saved_values_flow_into_later_steps_with_their_type(tmp_path: Path) -> None:
    service = session(tmp_path, test_data={"pet_name": "Bella"})
    login = run_step(service, 1, api("log in", method="POST", path="/login", save=[{"name": "access_token", "json_path": "$.access_token"}]))
    assert login.status == "passed" and TOKEN not in (login.actual or "") and "[REDACTED] as {{access_token}}" in login.actual
    created = run_step(
        service,
        2,
        api(
            "create",
            method="POST",
            path="/pets",
            auth={"type": "bearer", "value": "{{access_token}}"},
            body={"name": "{{pet_name}}"},
            expect={"status": 201},
            save=[{"name": "pet_id", "json_path": "$.id"}, {"name": "where", "header": "Location"}],
        ),
    )
    assert created.status == "passed", created.actual
    assert service.data.captured["pet_id"] == 2 and service.data.captured["where"] == "/v1/pets/2"
    fetched = run_step(service, 3, api("get", path="/pets/{{pet_id}}", expect={"json_path": [{"path": "$.id", "value": "{{pet_id}}"}, {"path": "$.name", "value": "{{pet_name}}"}]}))
    assert fetched.status == "passed", fetched.actual
    assert "note" not in json.dumps([entry.model_dump() for entry in fetched.ledger])  # the saved int compared as an int
    for path in (tmp_path / "run").rglob("*.json"):
        assert TOKEN not in path.read_text(), path


def test_unknown_placeholders_and_missing_secrets_are_errors(tmp_path: Path) -> None:
    service = session(tmp_path, test_data={"key": {"secret": "PETS_KEY"}})
    unknown = run_step(service, 1, Step.model_construct(action="get", expected=None, api=ApiCall(path="/pets/{{nope}}"), sql=None, semantic=False, timeout_s=None))
    assert unknown.status == "error" and "unknown placeholder {{nope}}" in unknown.actual
    missing = run_step(service, 2, api("get", path="/pets/1", headers={"X-Key": "{{key}}"}))
    assert missing.status == "error" and "PETS_KEY" in missing.actual


def test_secret_header_values_never_reach_evidence_or_results(tmp_path: Path) -> None:
    service = session(tmp_path, test_data={"token": {"secret": "PETS_TOKEN"}}, env={"PETS_TOKEN": TOKEN})
    outcome = run_step(service, 1, api("create", method="POST", path="/pets", headers={"Authorization": "Bearer {{token}}"}, body={"name": "Bella", "password": "pw-77"}, expect={"status": 201}))
    assert outcome.status == "passed"
    text = (tmp_path / "run" / "steps" / "1" / "http.json").read_text()
    assert TOKEN not in text and "pw-77" not in text


def test_expected_text_without_structured_checks_is_unverified(tmp_path: Path) -> None:
    outcome = run_step(session(tmp_path), 1, api("get", path="/pets/1", expected="the pet is Rex"))
    assert outcome.status == "unverified" and outcome.ledger[-1].verdict == "unverified"


def test_schema_references_resolve_inside_the_project_only(tmp_path: Path) -> None:
    specs = tmp_path / ".hercules" / "specs"
    specs.mkdir(parents=True)
    (specs / "pets.yaml").write_text(
        "openapi: 3.0.3\npaths:\n  /pets/{id}:\n    get:\n      responses:\n        '200':\n          content:\n            application/json:\n              schema: {$ref: '#/components/schemas/Pet'}\ncomponents:\n  schemas:\n    Pet: {type: object, required: [id, name], properties: {id: {type: integer}, name: {type: string}}}\n    Cat: {type: object, required: [meow]}\n"
    )
    (tmp_path.parent / "outside.yaml").write_text("type: object\n")
    service = session(tmp_path)
    good = run_step(service, 1, api("get", path="/pets/1", expect={"json_schema": "pets.yaml#/components/schemas/Pet", "openapi": "pets.yaml"}))
    assert good.status == "passed", good.actual
    assert [entry.check for entry in good.ledger] == ["json_schema", "openapi_response"]
    bad = run_step(service, 2, api("get", path="/pets/1", expect={"json_schema": "pets.yaml#/components/schemas/Cat"}))
    assert bad.status == "failed" and "'meow' is a required property" in bad.actual
    inline = run_step(service, 3, api("get", path="/pets/1", expect={"json_schema": {"type": "object", "required": ["owner"]}}))
    assert inline.status == "failed"
    for ref in ("../outside.yaml", str(tmp_path.parent / "outside.yaml"), "missing.yaml#/x", "pets.yaml#/components/schemas/Dog"):
        escaped = run_step(service, 4, api("get", path="/pets/1", expect={"json_schema": ref}))
        assert escaped.status == "error", ref


def test_invalid_payloads_are_rejected_when_the_case_loads() -> None:
    with pytest.raises(ValidationError, match="belong to a request"):
        ApiCall(headers={"X": "1"})
    with pytest.raises(ValidationError, match="exactly one of json_path or header"):
        ApiCall(path="/x", save=[{"name": "a"}])  # type: ignore[list-item]
    with pytest.raises(ValidationError, match="needs db"):
        SqlCall(query="SELECT 1")
    with pytest.raises(ValidationError, match="one payload: api, sql, quality or security"):
        Step(action="x", api=ApiCall(path="/x"), sql=SqlCall(db="db", query="SELECT 1"))
    with pytest.raises(ValidationError, match="not a test_data entry"):
        TestCase(id="c", title="t", steps=[Step(action="q", sql=SqlCall(db="db", query="SELECT 1"))])
    with pytest.raises(ValidationError, match=r"unknown placeholder\(s\) \{\{pet_id\}\} in steps\[1\]\.api"):
        TestCase(id="c", title="t", steps=[Step(action="get", api=ApiCall(path="/pets/{{pet_id}}")), Step(action="save", api=ApiCall(path="/x", save=[{"name": "pet_id", "json_path": "$.id"}]))])


# ── SQL ──────────────────────────────────────────────────────────────────────


def _db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE pets (id INTEGER, name TEXT)")
        connection.executemany("INSERT INTO pets VALUES (?, ?)", [(1, "Rex"), (2, "Tom")])


def test_sql_query_checks_and_saves(tmp_path: Path) -> None:
    _db(tmp_path / "app.db")
    service = session(tmp_path, test_data={"db": {"secret": "APP_DB"}}, env={"APP_DB": "sqlite:///app.db"})
    step = Step(
        action="count pets",
        sql=SqlCall(db="db", query="SELECT id, name FROM pets WHERE id <= :n ORDER BY id", params={"n": 2}, expect={"rows": 2, "min_rows": 1, "json_path": [{"path": "$[0].name", "value": "Rex"}]}, save=[{"name": "first", "json_path": "$[0].id"}]),  # type: ignore[arg-type]
    )
    outcome = run_step(service, 1, step)
    assert outcome.status == "passed", outcome.actual
    assert [entry.check for entry in outcome.ledger] == ["row_count_eq", "row_count_gte", "sql_eq"] and service.data.captured["first"] == 1
    record = json.loads((tmp_path / "run" / "steps" / "1" / "sql.json").read_text())
    assert record["database"] == "db" and record["row_count"] == 2 and "sqlite" not in json.dumps(record["query"])
    wrong = run_step(service, 2, Step(action="check", sql=SqlCall(expect={"max_rows": 1})))  # type: ignore[arg-type]
    assert wrong.status == "failed" and wrong.ledger[0].actual == "2 row(s)"


def test_sql_refuses_writes_and_unknown_databases(tmp_path: Path) -> None:
    _db(tmp_path / "app.db")
    service = session(tmp_path, test_data={"db": "sqlite:///app.db"})
    refused = run_step(service, 1, Step(action="delete", sql=SqlCall(db="db", query="DELETE FROM pets")))
    assert refused.status == "error" and refused.error_class == "QueryRefused"
    with sqlite3.connect(tmp_path / "app.db") as connection:
        assert connection.execute("SELECT count(*) FROM pets").fetchone() == (2,)
    unknown = asyncio.run(service.run(2, ServiceReading(call=SqlCall.model_construct(db="other", query="SELECT 1", params={}, expect=None, save=[]))))
    assert unknown.status == "error" and "unknown database" in unknown.actual


# ── plain-language steps: read by the model, never by rules ──────────────────


def test_api_case_steps_without_payload_are_read_by_the_model_with_the_tag_as_hint(tmp_path: Path) -> None:
    plan = ServicePlan(kind="api", api=ApiCall(path="/pets/1", expect={"status": 200, "json_path": [{"path": "$.name", "value": "Rex"}]}), sql=None)  # type: ignore[arg-type]
    model = FakeModel(ServicePlan=plan)
    service = session(tmp_path, model=model, hint=case_hint(["kind:api"]))
    outcome = run_step(service, 1, Step(action="Fetch pet 1", expected="its name is Rex"))
    assert outcome.status == "passed", outcome.actual
    assert model.calls == [("ServicePlan", "The test case is tagged kind:api.\nStep: Fetch pet 1\nExpected: its name is Rex")]
    assert "{{name}}" in SERVICE_SYSTEM_PROMPT


def test_without_a_model_a_plain_api_step_is_an_error_not_a_guess(tmp_path: Path) -> None:
    outcome = run_step(session(tmp_path, hint="api"), 1, Step(action="Fetch pet 1"))
    assert outcome.status == "error" and "no model is configured" in outcome.actual
    none = run_step(session(tmp_path, model=FakeModel(ServicePlan=ServicePlan(kind="none", api=None, sql=None)), hint="api"), 1, Step(action="Click Save"))
    assert none.status == "error" and none.error_class == "UnreadableStep"


def test_untagged_cases_leave_steps_without_payload_to_the_step_interpreter(tmp_path: Path) -> None:
    assert asyncio.run(session(tmp_path).read(Step(action="I send a GET request to /pets/1"))) is None


def test_step_interpreter_routes_api_steps_to_the_service(tmp_path: Path) -> None:
    """In a browser case, the step interpreter's model classifies a step as ``api``; the payload then comes from the service schema."""
    model = FakeModel(IntentPlan=IntentPlan(actions=[IntentReply(kind="api", target=None, value=None, url=None, expected=None, match=None, role=None)]), ServicePlan=ServicePlan(kind="api", api=ApiCall(path="/pets/1", expect={"status": 200}), sql=None))  # type: ignore[arg-type]
    service = session(tmp_path, model=model)
    deps = StepDeps(
        page=None,
        perceiver=DomPerceiver(),
        decider=StubDecider(),
        interpreter=StepInterpreter(LLMInterpreter(model)),
        data=service.data,
        evidence=service.evidence,
        service=service,
    )

    class NoControls(StepRunner):
        async def _controls(self) -> str:
            return ""

    outcome = asyncio.run(NoControls(deps).run(1, Step(action="Check that pet 1 exists in the API")))
    assert outcome.status == "passed", outcome.actual
    assert [call[0] for call in model.calls] == ["IntentPlan", "ServicePlan"]
    plan = (tmp_path / "run" / "steps" / "1" / "plan.jsonl").read_text().splitlines()
    assert json.loads(plan[-1])["stage"] == "service" and json.loads(plan[-1])["payload"]["path"] == "/pets/1"
