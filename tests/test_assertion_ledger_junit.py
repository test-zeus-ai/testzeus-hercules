"""Offline tests for the assertion audit ledger consumer in junit_helper.

The deterministic assertion tools append one JSON object per verdict (PASSED
included) to ``<proof>/assertion_logs.log``. These tests pin the report-side
behavior:
1. counts are always reported as junit case properties when a ledger exists,
   and a missing ledger file changes nothing at all;
2. retries of the same assertion target dedupe to the LAST verdict per
   (subject, path, operator, expected), while cross-payload checks are kept;
3. a planner pass contradicted by a FAILED (evaluable mismatch) or ERROR
   (unresolved) ledger entry becomes a Failure naming the offenders;
4. a planner fail is never softened, notices / unknown verdicts never fail,
   and a zero-assertion pass is only marked (Assertions Missing property);
5. attribution: entries tagged with another test id are ignored when the
   case's test id is discoverable; an untagged ledger belongs to the case.
"""

import asyncio
import json
import os

import pytest
from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.utils.junit_helper import build_junit_xml

_PASS = {
    "terminate": "yes",
    "final_response": "ok",
    "is_assert": True,
    "is_passed": True,
    "assert_summary": "verified ok",
}

_FAIL = {
    "terminate": "yes",
    "final_response": "expected 200 got 500",
    "is_assert": True,
    "is_passed": False,
    "assert_summary": "expected 200 got 500",
}


class _FakeConf:
    """Minimal stand-in for the global config used by junit_helper."""

    def __init__(self, tmp_path) -> None:
        self.junit_base = tmp_path / "junit"
        self.junit_base.mkdir(parents=True, exist_ok=True)
        self.test_id = "default"

    def get_junit_xml_base_path(self) -> str:
        return str(self.junit_base)

    def get_mode(self) -> str:
        return "prod"

    def set_default_test_id(self, test_id: str = "default") -> None:
        self.test_id = test_id

    def get_default_test_id(self) -> str:
        return self.test_id


@pytest.fixture()
def ledger_env(monkeypatch, tmp_path):
    """Patch the config and create an (empty) per-scenario proofs folder."""
    conf = _FakeConf(tmp_path)
    import testzeus_hercules.utils.junit_helper as junit_helper

    monkeypatch.setattr(junit_helper, "get_global_conf", lambda: conf)
    proofs = tmp_path / "proofs" / "scenario_one"
    proofs.mkdir(parents=True)
    return conf, str(proofs)


def _ledger_line(verdict="PASSED", path="$.body.id", operator="eq", expected="200", actual="200", subject="abc123", test_id=None, **extra) -> str:
    """One ledger line in the producer's format (source/test_id/verdict/...)."""
    entry = {
        "source": "assert_json_path",
        "path": path,
        "operator": operator,
        "expected": expected,
        "actual": actual,
        "verdict": verdict,
    }
    if subject is not None:
        entry["subject"] = subject
    if test_id is not None:
        entry["test_id"] = test_id
    entry.update(extra)
    return json.dumps(entry)


def _write_ledger(proofs: str, lines) -> str:
    ledger_path = os.path.join(proofs, "assertion_logs.log")
    with open(ledger_path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")
    return ledger_path


def _build(ledger_env, json_data: dict, test_id: str = None) -> dict:
    """Drive the real build_junit_xml and return the single parsed testcase."""
    conf, proofs = ledger_env
    conf.set_default_test_id(test_id or "scenario_one")
    path = asyncio.run(
        build_junit_xml(
            dict(json_data),
            execution_time=1.0,
            cost_metric={},
            feature="Feature: Ledger",
            scenario="Scenario: ledger probe",
            proofs_path=proofs,
        )
    )
    cases = parse_junit_testcases(path)
    assert len(cases) == 1
    return cases[0]


# ── ledger presence / properties ─────────────────────────────────────────────


def test_missing_ledger_file_emits_no_properties_and_changes_nothing(ledger_env) -> None:
    case = _build(ledger_env, _PASS)

    assert case["status"] == "passed"
    assert case["failure_message"] is None
    assert not [name for name in case["properties"] if name.startswith("Assertions ")]


def test_ledger_properties_report_counts(ledger_env) -> None:
    _write_ledger(
        ledger_env[1],
        [
            _ledger_line(verdict="PASSED", path="$.a"),
            _ledger_line(verdict="PASSED", path="$.b"),
            _ledger_line(verdict="FAILED", path="$.c", expected="1", actual="2"),
            _ledger_line(verdict="ERROR", path="$.d", reason="bad path"),
            _ledger_line(verdict="notice", path="$.e", source="invariant"),
        ],
    )
    case = _build(ledger_env, dict(_FAIL, is_passed=False))

    assert case["status"] == "failed"
    properties = case["properties"]
    assert properties["Assertions Total"] == "5"
    assert properties["Assertions Passed"] == "2"
    assert properties["Assertions Failed"] == "1"
    assert properties["Assertions Unresolved"] == "1"
    assert properties["Assertions Notices"] == "1"
    assert "Assertions Missing" not in properties


def test_unknown_verdict_counts_in_total_only(ledger_env) -> None:
    _write_ledger(ledger_env[1], [_ledger_line(verdict="SUSPICIOUS", path="$.a")])
    case = _build(ledger_env, _PASS)

    assert case["status"] == "passed"
    properties = case["properties"]
    assert properties["Assertions Total"] == "1"
    assert properties["Assertions Passed"] == "0"
    assert properties["Assertions Failed"] == "0"
    assert properties["Assertions Unresolved"] == "0"
    assert properties["Assertions Notices"] == "0"


def test_malformed_lines_are_skipped(ledger_env) -> None:
    _write_ledger(
        ledger_env[1],
        ["not json at all", json.dumps(["not", "an", "object"]), "", _ledger_line(verdict="FAILED", path="$.body.id", expected="200", actual="500")],
    )
    case = _build(ledger_env, _PASS)

    assert case["status"] == "failed"
    assert case["properties"]["Assertions Total"] == "1"
    assert "$.body.id" in (case["failure_message"] or "")


# ── retry semantics (dedupe, last verdict wins) ──────────────────────────────


def test_retry_last_verdict_wins_passes_after_failed_retry(ledger_env) -> None:
    target = {"path": "$.body.id", "operator": "eq", "expected": "200"}
    _write_ledger(
        ledger_env[1],
        [
            _ledger_line(verdict="FAILED", actual="500", **target),
            _ledger_line(verdict="PASSED", actual="200", **target),
        ],
    )
    case = _build(ledger_env, _PASS)

    assert case["status"] == "passed"
    assert case["properties"]["Assertions Total"] == "1"
    assert case["properties"]["Assertions Passed"] == "1"
    assert case["properties"]["Assertions Failed"] == "0"


def test_retry_last_verdict_wins_fails_after_late_failure(ledger_env) -> None:
    target = {"path": "$.body.id", "operator": "eq", "expected": "200"}
    _write_ledger(
        ledger_env[1],
        [
            _ledger_line(verdict="PASSED", actual="200", **target),
            _ledger_line(verdict="FAILED", actual="500", **target),
        ],
    )
    case = _build(ledger_env, _PASS)

    assert case["status"] == "failed"
    assert case["properties"]["Assertions Failed"] == "1"


def test_cross_payload_checks_are_not_folded_together(ledger_env) -> None:
    # Same path/operator/expected but different payload fingerprints: two
    # distinct targets (e.g. pagination pages), both must be counted.
    _write_ledger(
        ledger_env[1],
        [
            _ledger_line(verdict="PASSED", subject="page1", path="$.body.id"),
            _ledger_line(verdict="FAILED", subject="page2", path="$.body.id", expected="200", actual="404"),
        ],
    )
    case = _build(ledger_env, _PASS)

    assert case["status"] == "failed"
    assert case["properties"]["Assertions Total"] == "2"
    assert case["properties"]["Assertions Failed"] == "1"


# ── verdict override (planner pass contradicted by the ledger) ───────────────


def test_planner_pass_with_failed_assertion_becomes_failure(ledger_env) -> None:
    _write_ledger(
        ledger_env[1],
        [
            _ledger_line(verdict="PASSED", path="$.a"),
            _ledger_line(verdict="FAILED", path="$.body.id", operator="eq", expected="200", actual="500"),
        ],
    )
    case = _build(ledger_env, _PASS)

    assert case["status"] == "failed"
    message = case["failure_message"] or ""
    assert "Assertion audit ledger contradicts the reported pass" in message
    assert "1 failed and 0 unresolved assertion(s) out of 2 recorded" in message
    assert "path=$.body.id" in message
    assert "operator=eq" in message
    assert "expected=200" in message
    assert "actual=500" in message


def test_unresolved_only_with_planner_pass_becomes_failure(ledger_env) -> None:
    _write_ledger(ledger_env[1], [_ledger_line(verdict="ERROR", path="$.missing", reason="Path matched nothing")])
    case = _build(ledger_env, _PASS)

    assert case["status"] == "failed"
    message = case["failure_message"] or ""
    assert "0 failed and 1 unresolved assertion(s) out of 1 recorded" in message
    assert "ERROR" in message
    assert "path=$.missing" in message


def test_override_names_at_most_three_offenders(ledger_env) -> None:
    _write_ledger(
        ledger_env[1],
        [_ledger_line(verdict="FAILED", path=f"$.field{i}", expected=str(i), actual="x") for i in range(5)],
    )
    case = _build(ledger_env, _PASS)

    assert case["status"] == "failed"
    message = case["failure_message"] or ""
    assert "5 failed and 0 unresolved assertion(s) out of 5 recorded" in message
    for i in range(3):
        assert f"$.field{i}" in message
    for i in range(3, 5):
        assert f"$.field{i}" not in message
    assert "...and 2 more offending assertion(s)." in message


def test_override_fields_are_truncated_and_single_line(ledger_env) -> None:
    long_value = "V" * 500
    _write_ledger(ledger_env[1], [_ledger_line(verdict="FAILED", path="$.x", expected=long_value, actual=f"{long_value}\nsecond line")])
    case = _build(ledger_env, _PASS)

    assert case["status"] == "failed"
    message = case["failure_message"] or ""
    assert "V" * 500 not in message
    assert "..." in message
    for line in message.splitlines():
        assert len(line) < 600


# ── what must NOT change ─────────────────────────────────────────────────────


def test_planner_fail_stays_failed_with_planner_message(ledger_env) -> None:
    _write_ledger(
        ledger_env[1],
        [_ledger_line(verdict="FAILED", path="$.body.id", expected="200", actual="500"), _ledger_line(verdict="PASSED", path="$.a")],
    )
    case = _build(ledger_env, dict(_FAIL))

    # The planner already reported red: no ledger re-rendering on top of it.
    assert case["status"] == "failed"
    assert case["failure_message"] == "expected 200 got 500"
    assert "Assertion audit ledger" not in (case["failure_message"] or "")
    assert case["properties"]["Assertions Failed"] == "1"


def test_notice_and_unknown_verdicts_never_fail(ledger_env) -> None:
    _write_ledger(
        ledger_env[1],
        [
            _ledger_line(verdict="notice", source="invariant", path="console", reason="4xx noise"),
            _ledger_line(verdict="SUSPICIOUS", path="$.weird"),
        ],
    )
    case = _build(ledger_env, _PASS)

    assert case["status"] == "passed"
    assert case["failure_message"] is None
    assert case["properties"]["Assertions Notices"] == "1"


def test_zero_assertion_pass_is_marked_missing_but_not_failed(ledger_env) -> None:
    # The ledger exists but recorded nothing for this case: the pass is
    # model-narrated. Reported via the Assertions Missing property only.
    _write_ledger(ledger_env[1], [])
    case = _build(ledger_env, _PASS)

    assert case["status"] == "passed"
    assert case["failure_message"] is None
    assert case["properties"]["Assertions Total"] == "0"
    assert case["properties"]["Assertions Missing"] == "true"


# ── attribution (shared proofs folder) ───────────────────────────────────────


def test_entries_tagged_for_other_test_id_are_ignored(ledger_env) -> None:
    _write_ledger(
        ledger_env[1],
        [
            _ledger_line(verdict="PASSED", path="$.mine", test_id="scenario_one"),
            _ledger_line(verdict="FAILED", path="$.theirs", expected="1", actual="2", test_id="scenario_two"),
        ],
    )
    case = _build(ledger_env, _PASS, test_id="scenario_one")

    assert case["status"] == "passed"
    assert case["properties"]["Assertions Total"] == "1"
    assert case["properties"]["Assertions Passed"] == "1"
    assert "$.theirs" not in (case["failure_message"] or "")


def test_all_entries_for_other_test_ids_leave_zero_assertion_mark(ledger_env) -> None:
    _write_ledger(ledger_env[1], [_ledger_line(verdict="FAILED", path="$.theirs", test_id="scenario_two")])
    case = _build(ledger_env, _PASS, test_id="scenario_one")

    assert case["status"] == "passed"
    assert case["properties"]["Assertions Total"] == "0"
    assert case["properties"]["Assertions Missing"] == "true"


def test_untagged_entries_are_attributed_to_the_case(ledger_env) -> None:
    # Producers that predate test-id tagging still count for the case.
    _write_ledger(ledger_env[1], [_ledger_line(verdict="FAILED", path="$.body.id", expected="200", actual="500")])
    case = _build(ledger_env, _PASS, test_id="scenario_one")

    assert case["status"] == "failed"
    assert "$.body.id" in (case["failure_message"] or "")


def test_case_test_id_in_json_data_wins_over_harness_default(ledger_env) -> None:
    _write_ledger(
        ledger_env[1],
        [
            _ledger_line(verdict="FAILED", path="$.theirs", test_id="other_case"),
            _ledger_line(verdict="PASSED", path="$.mine", test_id="explicit_case"),
        ],
    )
    conf, proofs = ledger_env
    conf.set_default_test_id("harness_default")
    path = asyncio.run(
        build_junit_xml(
            dict(_PASS, test_id="explicit_case"),
            execution_time=1.0,
            cost_metric={},
            feature="Feature: Ledger",
            scenario="Scenario: ledger probe",
            proofs_path=proofs,
        )
    )
    cases = parse_junit_testcases(path)
    assert len(cases) == 1
    case = cases[0]

    assert case["status"] == "passed"
    assert case["properties"]["Assertions Total"] == "1"
    assert case["properties"]["Assertions Passed"] == "1"
