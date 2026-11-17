"""Offline tests for the TestCase and RunResult models."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from testzeus_hercules.cases.models import EnvironmentRef, RunResult, SecretRef, Step, StepResult
from testzeus_hercules.cases.models import TestCase as Case

SPEC_CASE: dict[str, Any] = {
    "id": "checkout-guest-001",
    "title": "Guest can reach payment with a valid address",
    "priority": "high",
    "tags": ["checkout", "smoke"],
    "environment": {"ref": "local-dev", "base_url": "http://localhost:3000", "viewport": "1280x800"},
    "preconditions": ["Cart contains one item"],
    "test_data": {"email": "guest+{{run_id}}@example.test", "password": {"secret": "SHOP_GUEST_PW"}},
    "steps": [
        {"action": "Open /checkout"},
        {"action": "Fill Email with {{email}}"},
        {"action": "Select India in Country"},
        {"action": "Click Continue to payment", "expected": "The Payment step is shown"},
    ],
    "timeout_s": 300,
    "source": {"agent": "claude-code", "commit": "a1b2c3d", "files_changed": ["src/checkout/Address.tsx"]},
}

SPEC_RUN: dict[str, Any] = {
    "run_id": "r_20261025_142201_7f3a",
    "status": "failed",
    "case_id": "checkout-guest-001",
    "verdict_source": "ledger",
    "steps": [
        {
            "index": 4,
            "action": "Click Continue to payment",
            "status": "failed",
            "expected": "The Payment step is shown",
            "actual": "Validation error under Email: 'Enter a valid email'",
            "duration_ms": 6120,
            "decisions": 2,
            "evidence": ["hercules://runs/r_.../steps/4/som.jpg", "hercules://runs/r_.../steps/4/plain.jpg"],
        }
    ],
    "assertions": {"total": 1, "passed": 0, "failed": 1, "unverified": 0},
    "failure": {"category": "product_bug", "confidence": 0.71, "signals": ["form validation message", "no network error"]},
    "hints": ["Email field rejected a '+' address; check the validator in src/checkout/Address.tsx"],
    "evidence": {"junit": "...", "report_md": "...", "trace": "...", "video": "...", "network": "...", "console": "..."},
    "engines": {"llm": "gemma-4-26b-a4b@ollama", "decider": "local-llm", "egress": "local"},
    "cost": {"tokens_in": 41230, "tokens_out": 2210, "usd": 0.0},
    "duration_s": 48.2,
    "ui_url": "http://127.0.0.1:7373/runs/r_20261025_142201_7f3a",
}


def _case(**overrides: Any) -> dict[str, Any]:
    body = copy.deepcopy(SPEC_CASE)
    body.update(overrides)
    return body


def _run(**overrides: Any) -> dict[str, Any]:
    body = copy.deepcopy(SPEC_RUN)
    body.update(overrides)
    return body


def _passed_run(**overrides: Any) -> dict[str, Any]:
    step = {"index": 1, "action": "Open /", "status": "passed", "duration_ms": 10}
    base = _run(status="passed", steps=[step], assertions={"total": 1, "passed": 1, "failed": 0, "unverified": 0}, failure=None, hints=[])
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- TestCase


def test_spec_example_case_validates_with_defaults() -> None:
    case = Case.model_validate(SPEC_CASE)
    assert case.status == "ready"
    assert case.priority == "high"
    assert case.test_data["password"] == SecretRef(secret="SHOP_GUEST_PW")
    assert case.steps[3].expected == "The Payment step is shown"
    assert case.steps[0].semantic is False
    assert case.source is not None and case.source.files_changed == ["src/checkout/Address.tsx"]


def test_minimal_case_defaults() -> None:
    case = Case(id="a", title="T", steps=[Step(action="Open /")])
    assert (case.priority, case.timeout_s, case.status, case.tags, case.test_data) == ("medium", 300, "ready", [], {})
    assert case.environment == EnvironmentRef()


def test_case_round_trips_through_json() -> None:
    case = Case.model_validate(SPEC_CASE)
    assert Case.model_validate_json(case.model_dump_json()) == case


@pytest.mark.parametrize("case_id", ["Upper", "-lead", ".lead", "has space", "", "a" * 101, "under_score!"])
def test_rejects_bad_case_ids(case_id: str) -> None:
    with pytest.raises(ValidationError):
        Case.model_validate(_case(id=case_id))


@pytest.mark.parametrize("case_id", ["a", "0", "checkout-guest-001", "suite.case_1", "a" * 100])
def test_accepts_good_case_ids(case_id: str) -> None:
    assert Case.model_validate(_case(id=case_id)).id == case_id


def test_rejects_unknown_priority_and_status() -> None:
    with pytest.raises(ValidationError):
        Case.model_validate(_case(priority="urgent"))
    with pytest.raises(ValidationError):
        Case.model_validate(_case(status="approved"))


def test_draft_status_allowed() -> None:
    assert Case.model_validate(_case(status="draft")).status == "draft"


def test_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Case.model_validate(_case(owner="me"))


@pytest.mark.parametrize("count", [0, 201])
def test_step_count_bounds(count: int) -> None:
    with pytest.raises(ValidationError):
        Case.model_validate(_case(steps=[{"action": "Open /"}] * count))


def test_accepts_200_steps() -> None:
    assert len(Case.model_validate(_case(steps=[{"action": "Open /"}] * 200)).steps) == 200


@pytest.mark.parametrize("timeout", [9, 7201])
def test_case_timeout_bounds(timeout: int) -> None:
    with pytest.raises(ValidationError):
        Case.model_validate(_case(timeout_s=timeout))


def test_step_action_length_bounds() -> None:
    Step(action="x" * 2000)
    with pytest.raises(ValidationError):
        Step(action="")
    with pytest.raises(ValidationError):
        Step(action="x" * 2001)


def test_step_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Step(action="Open /", timeout_s=0)


def test_semantic_step_requires_expected() -> None:
    assert Step(action="Look", expected="A friendly greeting", semantic=True).semantic
    with pytest.raises(ValidationError, match="semantic"):
        Step(action="Look", semantic=True)


@pytest.mark.parametrize("url", ["https://shop.example.test/app", "http://localhost:3000", "http://127.0.0.1:8080/"])
def test_environment_accepts_http_urls(url: str) -> None:
    assert EnvironmentRef(base_url=url).base_url == url


@pytest.mark.parametrize("url", ["ftp://host/x", "file:///etc/passwd", "localhost:3000", "http://", "javascript:alert(1)"])
def test_environment_rejects_non_http_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentRef(base_url=url)


@pytest.mark.parametrize("viewport", ["1280x800", "375x812"])
def test_environment_accepts_viewports(viewport: str) -> None:
    assert EnvironmentRef(viewport=viewport).viewport == viewport


@pytest.mark.parametrize("viewport", ["1280*800", "1280X800", "x800", "1280x", "0x800", "big"])
def test_environment_rejects_bad_viewports(viewport: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentRef(viewport=viewport)


def test_environment_browser_is_one_of_playwrights_browsers() -> None:
    for name in ("chromium", "chrome", "msedge", "firefox", "webkit"):
        assert EnvironmentRef(browser=name).browser == name
    assert EnvironmentRef().browser is None
    for other in ("edge", "safari", "opera"):
        with pytest.raises(ValidationError, match="'chromium', 'chrome', 'msedge', 'firefox' or 'webkit'"):
            EnvironmentRef(browser=other)


@pytest.mark.parametrize("name", ["lower", "1LEADING", "HAS-DASH", "", "MIXED_case"])
def test_rejects_bad_secret_names(name: str) -> None:
    with pytest.raises(ValidationError):
        SecretRef(secret=name)


def test_secret_refs_never_carry_values() -> None:
    with pytest.raises(ValidationError):
        Case.model_validate(_case(test_data={"password": {"secret": "PW", "value": "hunter2"}}))
    with pytest.raises(ValidationError):
        Case.model_validate(_case(test_data={"password": {"value": "hunter2"}}))
    with pytest.raises(ValidationError):
        Case.model_validate(_case(test_data={"count": 3}))


@pytest.mark.parametrize("key", ["has space", "1st", "a-b"])
def test_rejects_bad_test_data_keys(key: str) -> None:
    with pytest.raises(ValidationError):
        Case.model_validate(_case(test_data={key: "x"}))


@pytest.mark.parametrize("key", ["run_id", "timestamp", "downloaded_file", "downloaded_file_name"])
def test_rejects_test_data_shadowing_builtins(key: str) -> None:
    with pytest.raises(ValidationError, match=f"test_data key\\(s\\) {key} are reserved for built-in placeholders"):
        Case.model_validate(_case(test_data={**SPEC_CASE["test_data"], key: "x"}))


def test_a_step_cannot_save_into_a_download_placeholder() -> None:
    from testzeus_hercules.cases.models import Capture
    from testzeus_hercules.daemon.models import Environment

    with pytest.raises(ValidationError, match="built-in placeholder"):
        Capture(name="downloaded_file", json_path="$.path")
    with pytest.raises(ValidationError, match=r"saves \{\{downloaded_file\}\}, a built-in placeholder"):
        Case.model_validate(_case(steps=[{"action": 'I save the response JSON "$.path" as "downloaded_file"'}]))
    with pytest.raises(ValidationError, match="reserved for built-in placeholders"):
        Environment(name="shop", base_url="https://shop.example", variables={"downloaded_file_name": "x"})


def test_unknown_placeholders_are_listed() -> None:
    steps = [{"action": "Fill Email with {{emial}}"}, {"action": "Type {{ pin }}", "expected": "Shows {{greeting}}"}]
    with pytest.raises(ValidationError) as excinfo:
        Case.model_validate(_case(steps=steps))
    message = str(excinfo.value)
    for name in ("emial", "pin", "greeting"):
        assert name in message
    assert "email" in message and "run_id" in message


def test_placeholders_may_use_builtins_and_spacing() -> None:
    steps = [{"action": "Note {{ run_id }} at {{timestamp}} for {{password}}"}]
    assert Case.model_validate(_case(steps=steps)).steps[0].action.startswith("Note")


def test_placeholders_in_test_data_values_are_checked() -> None:
    with pytest.raises(ValidationError, match="nope"):
        Case.model_validate(_case(test_data={"email": "a+{{nope}}@example.test"}))


def test_malformed_placeholder_is_reported() -> None:
    with pytest.raises(ValidationError, match="not a name"):
        Case.model_validate(_case(steps=[{"action": "Type {{first name}}"}]))


# --------------------------------------------------------------------------- RunResult


def test_spec_example_run_validates() -> None:
    run = RunResult.model_validate(SPEC_RUN)
    assert run.status == "failed"
    assert run.failure is not None and run.failure.category == "product_bug"
    assert run.steps[0].decisions == 2
    assert run.engines.egress == "local"
    assert run.evidence.bundle is None


def test_run_round_trips_through_json() -> None:
    run = RunResult.model_validate(SPEC_RUN)
    assert RunResult.model_validate_json(run.model_dump_json()) == run


def test_minimal_queued_run() -> None:
    run = RunResult(run_id="r_1", status="queued", case_id="a")
    assert (run.verdict_source, run.steps, run.hints, run.duration_s) == ("none", [], [], 0.0)
    assert run.assertions.total == 0


@pytest.mark.parametrize("status", ["queued", "running", "needs_approval", "passed", "failed", "error", "cancelled"])
def test_run_statuses(status: str) -> None:
    body = _passed_run() if status == "passed" else _run(status=status)
    assert RunResult.model_validate(body).status == status


def test_rejects_unknown_run_and_step_statuses() -> None:
    with pytest.raises(ValidationError):
        RunResult.model_validate(_run(status="done"))
    bad = copy.deepcopy(SPEC_RUN["steps"])
    bad[0]["status"] = "ok"
    with pytest.raises(ValidationError):
        RunResult.model_validate(_run(steps=bad))


def test_step_result_statuses() -> None:
    for status in ("passed", "failed", "error", "skipped", "blocked", "unverified"):
        assert StepResult(index=1, action="a", status=status).status == status


def test_passed_run_is_valid() -> None:
    assert RunResult.model_validate(_passed_run()).status == "passed"


def test_passed_requires_every_step_passed() -> None:
    steps = [{"index": 1, "action": "a", "status": "passed"}, {"index": 2, "action": "b", "status": "unverified"}]
    with pytest.raises(ValidationError, match="every step"):
        RunResult.model_validate(_passed_run(steps=steps))


def test_passed_requires_steps() -> None:
    with pytest.raises(ValidationError, match="every step"):
        RunResult.model_validate(_passed_run(steps=[]))


@pytest.mark.parametrize("counts", [{"total": 1, "passed": 0, "failed": 1, "unverified": 0}, {"total": 1, "passed": 0, "failed": 0, "unverified": 1}])
def test_passed_requires_clean_assertions(counts: dict[str, int]) -> None:
    with pytest.raises(ValidationError, match="assertions"):
        RunResult.model_validate(_passed_run(assertions=counts))


def test_passed_run_has_no_failure() -> None:
    failure = {"category": "product_bug", "confidence": 0.5, "signals": []}
    with pytest.raises(ValidationError, match="failure"):
        RunResult.model_validate(_passed_run(failure=failure))


def test_assertion_counts_cannot_exceed_total() -> None:
    with pytest.raises(ValidationError, match="total"):
        RunResult.model_validate(_run(assertions={"total": 1, "passed": 1, "failed": 1, "unverified": 0}))


def test_step_indexes_must_increase() -> None:
    steps = [{"index": 2, "action": "a", "status": "failed"}, {"index": 2, "action": "b", "status": "failed"}]
    with pytest.raises(ValidationError, match="index"):
        RunResult.model_validate(_run(steps=steps))


def test_failure_category_and_confidence() -> None:
    with pytest.raises(ValidationError):
        RunResult.model_validate(_run(failure={"category": "cosmic_rays", "confidence": 0.5, "signals": []}))
    with pytest.raises(ValidationError):
        RunResult.model_validate(_run(failure={"category": "environment", "confidence": 1.5, "signals": []}))


def test_negative_numbers_rejected() -> None:
    with pytest.raises(ValidationError):
        RunResult.model_validate(_run(duration_s=-1))
    with pytest.raises(ValidationError):
        RunResult.model_validate(_run(cost={"tokens_in": -1, "tokens_out": 0}))
    with pytest.raises(ValidationError):
        StepResult(index=1, action="a", status="passed", duration_ms=-5)


def test_verdict_source_choices() -> None:
    with pytest.raises(ValidationError):
        RunResult.model_validate(_run(verdict_source="vibes"))


# --------------------------------------------------------------------------- summary_text


def test_summary_of_failed_run() -> None:
    text = RunResult.model_validate(SPEC_RUN).summary_text()
    assert "FAILED" in text
    assert "checkout-guest-001" in text
    assert "Step 4" in text and "Click Continue to payment" in text
    assert "Expected: The Payment step is shown" in text
    assert "Actual: Validation error under Email" in text
    assert "product_bug" in text
    assert "Email field rejected a '+' address" in text
    assert "0/1 passed" in text


def test_summary_of_passed_run() -> None:
    text = RunResult.model_validate(_passed_run()).summary_text()
    assert "PASSED" in text
    assert "Expected:" not in text and "Hint" not in text


def test_summary_of_queued_run() -> None:
    assert "QUEUED" in RunResult(run_id="r_1", status="queued", case_id="a").summary_text()


def test_summary_uses_first_non_passing_step() -> None:
    steps = [
        {"index": 1, "action": "Open /", "status": "passed"},
        {"index": 2, "action": "Check banner", "status": "unverified", "expected": "Banner shows"},
        {"index": 3, "action": "Later", "status": "skipped"},
    ]
    text = RunResult.model_validate(_run(steps=steps, failure=None, hints=[])).summary_text()
    assert "Step 2" in text and "UNVERIFIED" in text.upper()
    assert "Actual: (not recorded)" in text


def test_summary_respects_max_chars() -> None:
    steps = copy.deepcopy(SPEC_RUN["steps"])
    steps[0]["actual"] = "x" * 50_000
    run = RunResult.model_validate(_run(steps=steps, hints=["h" * 5000]))
    assert len(run.summary_text()) <= 2000
    short = run.summary_text(max_chars=200)
    assert len(short) <= 200
    assert short.endswith("…")


def test_summary_rejects_tiny_budget() -> None:
    with pytest.raises(ValueError):
        RunResult.model_validate(SPEC_RUN).summary_text(max_chars=10)
