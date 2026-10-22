"""Tests for the deterministic JSONPath assertion tool (core.tools.api_assertions).

All judgment rules are covered offline: no HTTP, no LLM. The assertion ledger
is pointed at a tmp proof folder and every test verifies the verdict the tool
returns — not what an agent would narrate about it.
"""

import asyncio
import hashlib
import importlib.util
import json

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.api_assertions import assert_json_path
from testzeus_hercules.core.tools.api_calls import _VARIABLES, set_variable_value

HAS_JSONPATH_NG = importlib.util.find_spec("jsonpath_ng") is not None

requires_jsonpath_ng = pytest.mark.skipif(not HAS_JSONPATH_NG, reason="jsonpath_ng (api extra) is not installed")

DOC = {
    "status_code": 200,
    "body": {
        "id": "1234567890123456789",
        "name": "order-42",
        "total": 3,
        "prices": [10, 20, 30],
        "items": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}],
        "tags": ["red", "blue"],
        "note": None,
        "blank": "   ",
    },
}


@pytest.fixture
def proof_env(tmp_path, monkeypatch):
    """Point the proof folder at tmp_path; restore the config afterwards."""
    conf = get_global_conf()
    original_config = dict(conf.get_config())
    original_test_id = conf.get_default_test_id()
    conf.get_config()["PROJECT_SOURCE_ROOT"] = str(tmp_path)
    conf.set_default_test_id("assertion-tests")
    _VARIABLES.pop("assertion-tests", None)
    yield conf
    _VARIABLES.pop("assertion-tests", None)
    conf.get_config().clear()
    conf.get_config().update(original_config)
    conf.set_default_test_id(original_test_id)


def _assert(doc, path, operator="eq", expected=None):
    verdict = asyncio.run(assert_json_path(doc, path, operator, expected))
    assert isinstance(verdict, str) and verdict.startswith("{")
    return json.loads(verdict)


def _ledger_lines(conf):
    log_path = f"{conf.get_proof_path()}/assertion_logs.log"
    with open(log_path, "r", encoding="utf-8") as log_file:
        return [json.loads(line) for line in log_file if line.strip()]


# ── operator matrix ──────────────────────────────────────────────────────────


@requires_jsonpath_ng
def test_eq_pass_and_fail(proof_env) -> None:
    assert _assert(DOC, "$.body.name", "eq", "order-42")["assertion"] == "PASSED"
    failed = _assert(DOC, "$.body.name", "eq", "order-43")
    assert failed["assertion"] == "FAILED"
    assert failed["actual"] == "order-42"


@requires_jsonpath_ng
def test_eq_loose_text_across_types_records_note(proof_env) -> None:
    # int64 ids usually arrive as strings; the loose match decides the result
    # but is never silent.
    verdict = _assert(DOC, "$.body.total", "eq", "3")
    assert verdict["assertion"] == "PASSED"
    assert "text comparison" in verdict["note"]

    verdict = _assert(DOC, "$.body.id", "eq", 1234567890123456789)
    assert verdict["assertion"] == "PASSED"
    assert "text comparison" in verdict["note"]


def test_values_equal_bool_vs_int_never_silently_matches() -> None:
    # Python collapses 1 == True; a bool-vs-non-bool pair must not strict-pass
    # without a trace. It goes through the text comparison fallback instead,
    # which records a note whenever the loose match decides the result.
    from testzeus_hercules.core.tools.api_assertions import _values_equal

    assert _values_equal(1, True) == (False, None)
    assert _values_equal(True, 1) == (False, None)
    assert _values_equal(False, 0) == (False, None)
    assert _values_equal(True, True) == (True, None)  # same type stays strict
    widened = _values_equal("true", True)
    assert widened == (True, "Matched by text comparison across types (strict comparison disagreed).")


@requires_jsonpath_ng
def test_eq_bool_vs_int_never_silently_passes(proof_env) -> None:
    # End to end: `eq` with expected True against the int 1 must not be a
    # silent PASSED — the text fallback decides, and a widened match records
    # its note.
    verdict = _assert(DOC, "$.body.items[0].qty", "eq", True)  # qty is the int 1
    assert verdict["assertion"] == "FAILED"
    assert "note" not in verdict  # the text fallback found no match, so nothing to widen

    assert _assert(DOC, "$.body.items[0].qty", "ne", True)["assertion"] == "PASSED"

    # A textual bool still matches its typed counterpart — with a note.
    set_variable_value("expected_flag", "true")
    verdict = _assert('{"body": {"flag": true}}', "$.body.flag", "eq", "${expected_flag}")
    assert verdict["assertion"] == "PASSED"
    assert "text comparison" in verdict["note"]


@requires_jsonpath_ng
def test_ne_fails_when_textually_equal(proof_env) -> None:
    verdict = _assert(DOC, "$.body.total", "ne", "3")
    assert verdict["assertion"] == "FAILED"
    assert "not-equal" in verdict["note"]
    assert _assert(DOC, "$.body.total", "ne", "4")["assertion"] == "PASSED"


@requires_jsonpath_ng
def test_contains(proof_env) -> None:
    assert _assert(DOC, "$.body.name", "contains", "der-4")["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.tags", "contains", "blue")["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.tags", "contains", "green")["assertion"] == "FAILED"
    assert _assert(DOC, "$.body.name", "contains", "nope")["assertion"] == "FAILED"


@requires_jsonpath_ng
def test_exists_and_not_exists(proof_env) -> None:
    assert _assert(DOC, "$.body.note", "exists")["assertion"] == "PASSED"  # null still exists
    assert _assert(DOC, "$.body.items[*].sku", "exists")["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.missing", "exists")["assertion"] == "FAILED"
    assert _assert(DOC, "$.body.missing", "not_exists")["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.name", "not_exists")["assertion"] == "FAILED"


@requires_jsonpath_ng
def test_not_empty_strictness(proof_env) -> None:
    assert _assert(DOC, "$.body.name", "not_empty")["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.note", "not_empty")["assertion"] == "FAILED"  # null
    assert _assert(DOC, "$.body.blank", "not_empty")["assertion"] == "FAILED"  # blank string
    empty = {"items": []}
    assert _assert(empty, "$.items", "not_empty")["assertion"] == "FAILED"  # empty container
    assert _assert(DOC, "$.body.missing", "not_empty")["assertion"] == "FAILED"  # no match


@requires_jsonpath_ng
def test_gt_lt_check_first_match_only(proof_env) -> None:
    assert _assert(DOC, "$.body.total", "gt", 2)["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.total", "gt", 3)["assertion"] == "FAILED"
    assert _assert(DOC, "$.body.total", "lt", "100")["assertion"] == "PASSED"
    not_numeric = _assert(DOC, "$.body.name", "gt", 2)
    assert not_numeric["assertion"] == "ERROR"
    assert "not numeric" in not_numeric["reason"] or "Cannot compare" in not_numeric["reason"]


@requires_jsonpath_ng
def test_all_gte_all_lte_bind_every_value(proof_env) -> None:
    assert _assert(DOC, "$.body.prices", "all_gte", 10)["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.prices", "all_gte", "5")["assertion"] == "PASSED"
    violated = _assert(DOC, "$.body.prices", "all_gte", 15)
    assert violated["assertion"] == "FAILED"
    assert "index 0" in violated["reason"]
    assert _assert(DOC, "$.body.prices", "all_lte", 30)["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.prices", "all_lte", 29)["assertion"] == "FAILED"


@requires_jsonpath_ng
def test_all_gte_empty_array_is_failed_not_vacuous(proof_env) -> None:
    verdict = _assert({"prices": []}, "$.prices", "all_gte", 1)
    assert verdict["assertion"] == "FAILED"
    verdict = _assert(DOC, "$.body.items[*].qty", "all_gte", 99)  # matched set exists, values violate
    assert verdict["assertion"] == "FAILED"


@requires_jsonpath_ng
def test_count_eq_counts_array_elements_when_path_lands_on_one(proof_env) -> None:
    verdict = _assert(DOC, "$.body.items", "count_eq", 2)
    assert verdict["assertion"] == "PASSED"
    assert verdict["count_of"] == "array elements"
    assert verdict["actual"] == 2
    # An explicit [*] suffix still counts path matches.
    verdict = _assert(DOC, "$.body.items[*]", "count_eq", 2)
    assert verdict["assertion"] == "PASSED"
    assert verdict["count_of"] == "path matches"
    assert _assert(DOC, "$.body.items", "count_eq", 3)["assertion"] == "FAILED"


@requires_jsonpath_ng
def test_eq_path_cross_field_compare(proof_env) -> None:
    doc = {"body": {"shipping": 30, "declared_total": 30, "total": 999}}
    assert _assert(doc, "$.body.shipping", "eq_path", "$.body.declared_total")["assertion"] == "PASSED"
    verdict = _assert(doc, "$.body.shipping", "eq_path", "$.body.total")
    assert verdict["assertion"] == "FAILED"
    assert verdict["expected"] == 999
    unmatched = _assert(doc, "$.body.shipping", "eq_path", "$.body.nope")
    assert unmatched["assertion"] == "ERROR"
    assert "matched nothing" in unmatched["reason"]


# ── no-match diagnostics ─────────────────────────────────────────────────────


@requires_jsonpath_ng
def test_no_match_failure_names_available_keys(proof_env) -> None:
    verdict = _assert(DOC, "$.body.totl", "eq", 3)
    assert verdict["assertion"] == "FAILED"
    assert "total" in verdict["available_keys"]
    assert "$.body" in verdict["reason"]


@requires_jsonpath_ng
def test_no_match_failure_hints_captured_variable_misuse(proof_env) -> None:
    set_variable_value("quantity", 2)
    verdict = _assert(DOC, "$.body.items[0].quantity", "eq", 2)
    assert verdict["assertion"] == "FAILED"
    assert "${quantity}" in verdict.get("hint", "")


# ── input judgment rules (no jsonpath needed for these branches) ─────────────


def test_unknown_operator_is_error(proof_env) -> None:
    verdict = _assert(DOC, "$.body.name", "equals", "order-42")
    assert verdict["assertion"] == "ERROR"
    assert "Unknown operator" in verdict["reason"]
    assert "eq" in verdict["reason"]


def test_operator_name_in_expected_is_error(proof_env) -> None:
    verdict = _assert(DOC, "$.body.name", "ne", "not_empty")
    assert verdict["assertion"] == "ERROR"
    assert "operator" in verdict["reason"].lower()

    compound = _assert(DOC, "$.body.name", "eq", "ne_path:$.body.id")
    assert compound["assertion"] == "ERROR"


@requires_jsonpath_ng
def test_bare_operator_in_expected_with_default_operator_is_reinterpreted(proof_env) -> None:
    verdict = _assert(DOC, "$.body.name", "eq", "not_empty")
    assert verdict["assertion"] == "PASSED"
    assert verdict["operator"] == "not_empty"
    assert "reinterpreted" in verdict["note"]


def test_unresolved_placeholder_in_expected_is_error(proof_env) -> None:
    verdict = _assert(DOC, "$.body.name", "eq", "${never_captured}")
    assert verdict["assertion"] == "ERROR"
    assert "never_captured" in verdict["reason"]
    assert "ne would always pass" in verdict["reason"]


def test_invalid_response_json_is_error(proof_env) -> None:
    verdict = _assert("not-json{", "$.a", "eq", 1)
    assert verdict["assertion"] == "ERROR"
    assert "not valid JSON" in verdict["reason"]


# ── interpolation in path and expected ───────────────────────────────────────


@requires_jsonpath_ng
def test_placeholder_interpolation_in_path_and_expected(proof_env) -> None:
    set_variable_value("sku", "B")
    verdict = _assert(DOC, "$.body.items[?(@.sku == '${sku}')].qty", "eq", 2)
    assert verdict["assertion"] == "PASSED"

    set_variable_value("expected_name", "order-42")
    verdict = _assert(DOC, "$.body.name", "eq", "${expected_name}")
    assert verdict["assertion"] == "PASSED"
    # A typed capture stays typed: the expected side carries the int, not "2".
    set_variable_value("expected_total", 3)
    verdict = _assert(DOC, "$.body.total", "eq", "${expected_total}")
    assert verdict["assertion"] == "PASSED"
    assert "note" not in verdict  # strict typed equality decided it


def test_operator_swap_branch_interpolates_path(proof_env, monkeypatch) -> None:
    """The operator-swap reinterpretation must run on an interpolated path.

    Pure-function level: find_json_path is stubbed, so this runs without
    jsonpath_ng and observes exactly the path each branch evaluates.
    """
    from testzeus_hercules.core.tools import api_assertions

    seen_paths: list = []

    def fake_find_json_path(document, path):
        seen_paths.append(path)
        return [DOC["body"]["name"]]

    monkeypatch.setattr(api_assertions, "find_json_path", fake_find_json_path)
    set_variable_value("uid", "order-42")

    swapped = _assert(DOC, "$.body.items[?(@.id == '${uid}')].name", "eq", "not_empty")
    explicit = _assert(DOC, "$.body.items[?(@.id == '${uid}')].name", "not_empty")

    # Both forms must evaluate the same interpolated path (pre-fix, the swap
    # branch passed the raw ${uid} path through).
    assert seen_paths == ["$.body.items[?(@.id == 'order-42')].name"] * 2
    swapped.pop("note", None)  # only the swapped form carries the reinterpretation note
    assert swapped == explicit
    assert swapped["assertion"] == "PASSED"
    assert swapped["operator"] == "not_empty"


# ── audit ledger ─────────────────────────────────────────────────────────────


@requires_jsonpath_ng
def test_every_verdict_is_recorded_with_payload_fingerprint(proof_env) -> None:
    _assert(DOC, "$.body.name", "eq", "order-42")  # PASSED
    _assert(DOC, "$.body.name", "eq", "nope")  # FAILED
    _assert(DOC, "$.body.name", "equals", "x")  # ERROR

    lines = _ledger_lines(proof_env)
    assert [entry["verdict"] for entry in lines] == ["PASSED", "FAILED", "ERROR"]
    for entry in lines:
        assert entry["source"] == "assert_json_path"
        assert entry["path"] == "$.body.name"
        assert entry["subject"] == hashlib.sha1(json.dumps(DOC, separators=(",", ":"), default=str).encode()).hexdigest()[:12]
        assert len(entry["subject"]) == 12


# ── per-value operators judge EVERY match ────────────────────────────────────


@requires_jsonpath_ng
def test_eq_checks_every_match_not_only_the_first(proof_env) -> None:
    # qty values are [1, 2]: the first match equals 1, the second does not.
    verdict = _assert(DOC, "$.body.items[*].qty", "eq", 1)
    assert verdict["assertion"] == "FAILED"
    assert verdict["matched_count"] == 2
    assert verdict["actual"] == [1, 2]
    assert "Match 1 of 2" in verdict["reason"]


@requires_jsonpath_ng
def test_ne_requires_that_no_match_equals_expected(proof_env) -> None:
    assert _assert(DOC, "$.body.items[*].sku", "ne", "Z")["assertion"] == "PASSED"
    # "B" is the SECOND match; the old first-match-only check passed this.
    verdict = _assert(DOC, "$.body.items[*].sku", "ne", "B")
    assert verdict["assertion"] == "FAILED"
    assert "Match 1 of 2" in verdict["reason"]


@requires_jsonpath_ng
def test_contains_checks_every_match(proof_env) -> None:
    doc = {"names": ["alpha-1", "beta-1", "gamma-2"]}
    assert _assert(doc, "$.names[*]", "contains", "-")["assertion"] == "PASSED"
    verdict = _assert(doc, "$.names[*]", "contains", "-1")
    assert verdict["assertion"] == "FAILED"
    assert "Match 2 of 3" in verdict["reason"]


@requires_jsonpath_ng
def test_gt_lt_check_every_match(proof_env) -> None:
    assert _assert(DOC, "$.body.items[*].qty", "gt", 0)["assertion"] == "PASSED"
    verdict = _assert(DOC, "$.body.items[*].qty", "gt", 1)
    assert verdict["assertion"] == "FAILED"
    assert "Match 0 of 2" in verdict["reason"]
    assert _assert(DOC, "$.body.items[*].qty", "lt", 3)["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.items[*].qty", "lt", 2)["assertion"] == "FAILED"
    not_numeric = _assert(DOC, "$.body.items[*].sku", "gt", 1)
    assert not_numeric["assertion"] == "ERROR"


@requires_jsonpath_ng
def test_not_empty_checks_every_match(proof_env) -> None:
    doc = {"rows": [{"v": "x"}, {"v": "  "}]}
    verdict = _assert(doc, "$.rows[*].v", "not_empty")
    assert verdict["assertion"] == "FAILED"
    assert "Match 1 of 2" in verdict["reason"]


@requires_jsonpath_ng
def test_single_match_behaviour_is_unchanged(proof_env) -> None:
    verdict = _assert(DOC, "$.body.total", "eq", 3)
    assert verdict["assertion"] == "PASSED"
    assert verdict["actual"] == 3  # not wrapped in a list
    assert "reason" not in verdict


# ── count_eq must be a whole number ──────────────────────────────────────────


@requires_jsonpath_ng
def test_count_eq_rejects_non_integer_counts(proof_env) -> None:
    for bad in (2.5, "2.5", "nan", "inf"):
        verdict = _assert(DOC, "$.body.items", "count_eq", bad)
        assert verdict["assertion"] == "ERROR", bad
        assert "whole number" in verdict["reason"]
    assert _assert(DOC, "$.body.items", "count_eq", "2")["assertion"] == "PASSED"
    assert _assert(DOC, "$.body.items", "count_eq", 2.0)["assertion"] == "PASSED"


# ── operator lookalike guard no longer eats legitimate strings ───────────────


@requires_jsonpath_ng
@pytest.mark.parametrize("value", ["exists_in_cart", "contains the cart", "eq_check_passed", "not_empty_cart", "count_equal"])
def test_legitimate_strings_starting_with_an_operator_word_are_compared(proof_env, value) -> None:
    doc = {"body": {"status": value}}
    verdict = _assert(doc, "$.body.status", "eq", value)
    assert verdict["assertion"] == "PASSED", verdict
    assert "reason" not in verdict


@requires_jsonpath_ng
def test_literal_prefix_asserts_a_value_that_is_exactly_an_operator_name(proof_env) -> None:
    doc = {"body": {"mode": "exists"}}
    assert _assert(doc, "$.body.mode", "eq", "literal:exists")["assertion"] == "PASSED"
    # Without the escape the slip is still caught.
    assert _assert(doc, "$.body.mode", "ne", "exists")["assertion"] == "ERROR"


def test_operator_slips_are_still_caught(proof_env) -> None:
    for slip in ("count_eq:2", "ne_path:$.body.id", "ne_path", "not_empty", "exists"):
        verdict = _assert(DOC, "$.body.name", "ne", slip)
        assert verdict["assertion"] == "ERROR", slip


# ── ledger redaction ─────────────────────────────────────────────────────────


@requires_jsonpath_ng
def test_ledger_redacts_secret_named_paths_and_captured_secret_values(proof_env) -> None:
    doc = {"body": {"password": "hunter2-pass", "note": "value hunter2-token is here", "auth_token": "tok-abc-123"}}
    _assert(doc, "$.body.password", "eq", "hunter2-pass")
    set_variable_value("api_token", "hunter2-token")
    _assert(doc, "$.body.note", "contains", "${api_token}")

    raw = open(f"{proof_env.get_proof_path()}/assertion_logs.log", encoding="utf-8").read()
    assert "hunter2-pass" not in raw
    assert "hunter2-token" not in raw
    assert "[REDACTED]" in raw
    lines = _ledger_lines(proof_env)
    assert [entry["verdict"] for entry in lines] == ["PASSED", "PASSED"]  # redaction never changes the verdict
