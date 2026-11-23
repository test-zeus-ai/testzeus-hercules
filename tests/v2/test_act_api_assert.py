"""Deterministic JSON assertions (``act.api_assert``), ported from the v1 ``assert_json_path`` tests.

Pure functions: no HTTP, no model. Every case checks the verdict, never a narration of it.
"""

from __future__ import annotations

from typing import Any

import pytest

from testzeus_hercules.act import api_assert
from testzeus_hercules.act.api_assert import NOT_GIVEN, evaluate, values_equal

DOC = {
    "id": "1234567890123456789",
    "name": "order-42",
    "total": 3,
    "prices": [10, 20, 30],
    "items": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}],
    "tags": ["red", "blue"],
    "note": None,
    "blank": "   ",
}


def check(doc: Any, path: str, operator: str = "eq", expected: Any = NOT_GIVEN, **variables: Any) -> dict[str, Any]:
    return evaluate(doc, path, operator, expected, variables=variables)


# ── operator matrix ──────────────────────────────────────────────────────────


def test_eq_pass_and_fail() -> None:
    assert check(DOC, "$.name", "eq", "order-42")["assertion"] == "PASSED"
    failed = check(DOC, "$.name", "eq", "order-43")
    assert failed["assertion"] == "FAILED"
    assert failed["actual"] == "order-42"


def test_eq_loose_text_across_types_records_a_note() -> None:
    verdict = check(DOC, "$.total", "eq", "3")
    assert verdict["assertion"] == "PASSED"
    assert "text comparison" in verdict["note"]
    verdict = check(DOC, "$.id", "eq", 1234567890123456789)
    assert verdict["assertion"] == "PASSED"
    assert "text comparison" in verdict["note"]


def test_bool_never_silently_equals_int() -> None:
    assert values_equal(1, True) == (False, None)
    assert values_equal(True, 1) == (False, None)
    assert values_equal(False, 0) == (False, None)
    assert values_equal(True, True) == (True, None)
    assert values_equal("true", True) == (True, "Matched by text comparison across types (strict comparison disagreed).")
    verdict = check(DOC, "$.items[0].qty", "eq", True)
    assert verdict["assertion"] == "FAILED" and "note" not in verdict
    assert check(DOC, "$.items[0].qty", "ne", True)["assertion"] == "PASSED"
    verdict = check({"flag": True}, "$.flag", "eq", "${expected_flag}", expected_flag="true")
    assert verdict["assertion"] == "PASSED" and "text comparison" in verdict["note"]


def test_ne_fails_when_textually_equal() -> None:
    verdict = check(DOC, "$.total", "ne", "3")
    assert verdict["assertion"] == "FAILED"
    assert "not-equal" in verdict["note"]
    assert check(DOC, "$.total", "ne", "4")["assertion"] == "PASSED"


def test_contains_and_not_contains() -> None:
    assert check(DOC, "$.name", "contains", "der-4")["assertion"] == "PASSED"
    assert check(DOC, "$.tags", "contains", "blue")["assertion"] == "PASSED"
    assert check(DOC, "$.tags", "contains", "green")["assertion"] == "FAILED"
    assert check(DOC, "$.name", "contains", "nope")["assertion"] == "FAILED"
    assert check(DOC, "$.tags", "not_contains", "green")["assertion"] == "PASSED"
    assert check(DOC, "$.tags", "not_contains", "red")["assertion"] == "FAILED"


def test_matches_uses_a_regular_expression() -> None:
    assert check(DOC, "$.name", "matches", r"^order-\d+$")["assertion"] == "PASSED"
    assert check(DOC, "$.name", "matches", r"^item-")["assertion"] == "FAILED"
    bad = check(DOC, "$.name", "matches", "(")
    assert bad["assertion"] == "ERROR" and "regular expression" in bad["reason"]


def test_exists_and_not_exists() -> None:
    assert check(DOC, "$.note", "exists")["assertion"] == "PASSED"  # null still exists
    assert check(DOC, "$.items[*].sku", "exists")["assertion"] == "PASSED"
    assert check(DOC, "$.missing", "exists")["assertion"] == "FAILED"
    assert check(DOC, "$.missing", "not_exists")["assertion"] == "PASSED"
    assert check(DOC, "$.name", "not_exists")["assertion"] == "FAILED"


def test_not_empty_strictness() -> None:
    assert check(DOC, "$.name", "not_empty")["assertion"] == "PASSED"
    assert check(DOC, "$.note", "not_empty")["assertion"] == "FAILED"
    assert check(DOC, "$.blank", "not_empty")["assertion"] == "FAILED"
    assert check({"items": []}, "$.items", "not_empty")["assertion"] == "FAILED"
    assert check(DOC, "$.missing", "not_empty")["assertion"] == "FAILED"


def test_gt_lt() -> None:
    assert check(DOC, "$.total", "gt", 2)["assertion"] == "PASSED"
    assert check(DOC, "$.total", "gt", 3)["assertion"] == "FAILED"
    assert check(DOC, "$.total", "lt", "100")["assertion"] == "PASSED"
    not_numeric = check(DOC, "$.name", "gt", 2)
    assert not_numeric["assertion"] == "ERROR" and "Cannot compare" in not_numeric["reason"]


def test_all_gte_all_lte_bind_every_value() -> None:
    assert check(DOC, "$.prices", "all_gte", 10)["assertion"] == "PASSED"
    violated = check(DOC, "$.prices", "all_gte", 15)
    assert violated["assertion"] == "FAILED" and "index 0" in violated["reason"]
    assert check(DOC, "$.prices", "all_lte", 30)["assertion"] == "PASSED"
    assert check(DOC, "$.prices", "all_lte", 29)["assertion"] == "FAILED"
    assert check({"prices": []}, "$.prices", "all_gte", 1)["assertion"] == "FAILED"  # never vacuously true


def test_count_eq() -> None:
    verdict = check(DOC, "$.items", "count_eq", 2)
    assert verdict["assertion"] == "PASSED" and verdict["count_of"] == "array elements"
    verdict = check(DOC, "$.items[*]", "count_eq", 2)
    assert verdict["assertion"] == "PASSED" and verdict["count_of"] == "path matches"
    assert check(DOC, "$.items", "count_eq", 3)["assertion"] == "FAILED"
    assert check({"m": [[1, 2, 3]]}, "$.m[*]", "count_eq", 1)["assertion"] == "PASSED"
    assert check({"m": [[1, 2, 3]]}, "$.m[0]", "count_eq", 3)["assertion"] == "PASSED"
    zero = check({"items": []}, "$.items[*]", "count_eq", 0)
    assert zero["assertion"] == "PASSED" and zero["actual"] == 0
    for bad in (2.5, "2.5"):
        assert "whole number" in check(DOC, "$.items", "count_eq", bad)["reason"]
    for bad in ("nan", "inf", "1_000"):
        assert "not a number" in check(DOC, "$.items", "count_eq", bad)["reason"]


def test_eq_path_compares_two_fields() -> None:
    doc = {"shipping": 30, "declared": 30, "total": 999}
    assert check(doc, "$.shipping", "eq_path", "$.declared")["assertion"] == "PASSED"
    verdict = check(doc, "$.shipping", "eq_path", "$.total")
    assert verdict["assertion"] == "FAILED" and verdict["expected"] == 999
    unmatched = check(doc, "$.shipping", "eq_path", "$.nope")
    assert unmatched["assertion"] == "ERROR" and "matched nothing" in unmatched["reason"]


# ── every match must satisfy per-value operators ─────────────────────────────


def test_per_value_operators_check_every_match() -> None:
    verdict = check(DOC, "$.items[*].qty", "eq", 1)
    assert verdict["assertion"] == "FAILED" and verdict["actual"] == [1, 2] and "Match 1 of 2" in verdict["reason"]
    assert "Match 1 of 2" in check(DOC, "$.items[*].sku", "ne", "B")["reason"]
    assert "Match 2 of 3" in check({"n": ["a-1", "b-1", "c-2"]}, "$.n[*]", "contains", "-1")["reason"]
    assert "Match 0 of 2" in check(DOC, "$.items[*].qty", "gt", 1)["reason"]
    assert "Match 1 of 2" in check({"rows": [{"v": "x"}, {"v": " "}]}, "$.rows[*].v", "not_empty")["reason"]
    single = check(DOC, "$.total", "eq", 3)
    assert single["actual"] == 3 and "reason" not in single


# ── no-match diagnostics and input rules ─────────────────────────────────────


def test_no_match_names_the_available_keys_and_captured_name_misuse() -> None:
    verdict = check({"body": DOC}, "$.body.totl", "eq", 3)
    assert verdict["assertion"] == "FAILED" and "total" in verdict["available_keys"] and "$.body" in verdict["reason"]
    hinted = check(DOC, "$.items[0].quantity", "eq", 2, quantity=2)
    assert "${quantity}" in hinted["hint"]


def test_unknown_operator_and_missing_expected_are_errors() -> None:
    unknown = check(DOC, "$.name", "equals", "x")
    assert unknown["assertion"] == "ERROR" and "Unknown operator" in unknown["reason"]
    for operator in ("eq", "ne", "contains", "gt", "lt", "count_eq", "all_gte", "eq_path"):
        assert "needs an 'expected' value" in check({"a": None}, "$.a", operator)["reason"]
    assert check({"a": None}, "$.a", "eq", None)["assertion"] == "PASSED"
    assert check({"a": 1}, "$.b", "not_exists")["assertion"] == "PASSED"


def test_operator_slips_are_caught_only_when_guarded() -> None:
    assert check(DOC, "$.name", "ne", "not_empty")["assertion"] == "ERROR"
    assert check(DOC, "$.name", "eq", "ne_path:$.id")["assertion"] == "ERROR"
    swapped = check(DOC, "$.name", "eq", "not_empty")
    assert swapped["assertion"] == "PASSED" and swapped["operator"] == "not_empty" and "reinterpreted" in swapped["note"]
    # A step's own words are compared as written: "exists" is a value here, not an operator.
    assert evaluate({"mode": "exists"}, "$.mode", "eq", "exists", guard=False)["assertion"] == "PASSED"
    assert check({"mode": "exists"}, "$.mode", "eq", "literal:exists")["assertion"] == "PASSED"


@pytest.mark.parametrize("value", ["exists_in_cart", "contains the cart", "eq_check_passed", "not_empty_cart", "count_equal"])
def test_strings_starting_with_an_operator_word_are_values(value: str) -> None:
    assert check({"status": value}, "$.status", "eq", value)["assertion"] == "PASSED"


def test_unresolved_placeholder_is_an_error_but_literal_is_compared() -> None:
    verdict = check(DOC, "$.name", "eq", "${never_captured}")
    assert verdict["assertion"] == "ERROR" and "never_captured" in verdict["reason"] and "ne would always pass" in verdict["reason"]
    assert check({"a": "${nope}"}, "$.a", "eq", "literal:${nope}")["assertion"] == "PASSED"


def test_invalid_json_and_invalid_path_are_errors() -> None:
    assert "not valid JSON" in check("not-json{", "$.a", "eq", 1)["reason"]
    assert "Invalid JSONPath" in check(DOC, "$[", "eq", 1)["reason"]


# ── captured values ──────────────────────────────────────────────────────────


def test_captured_values_in_path_and_expected_keep_their_type() -> None:
    assert check(DOC, "$.items[?(@.sku == '${sku}')].qty", "eq", 2, sku="B")["assertion"] == "PASSED"
    typed = check(DOC, "$.total", "eq", "${expected_total}", expected_total=3)
    assert typed["assertion"] == "PASSED" and "note" not in typed


def test_interpolation_rules() -> None:
    variables = {"n": 2, "s": "x", "obj": {"a": 1}}
    assert api_assert.interpolate("${n}", variables) == 2
    assert api_assert.interpolate("id-${n}-${s}", variables) == "id-2-x"
    assert api_assert.interpolate("${obj}", variables) == {"a": 1}
    assert api_assert.interpolate("${unknown}", variables) == "${unknown}"
    assert api_assert.interpolate(5, variables) == 5
    assert api_assert.interpolate_tree({"a": ["${n}", {"b": "${s}"}]}, variables) == {"a": [2, {"b": "x"}]}
    assert api_assert.stringify(None) == "null" and api_assert.stringify("a") == "a"


@pytest.mark.parametrize("text", ["1_000", "inf", "nan", "NaN", "Infinity", "0x10", " 1e999 "])
def test_non_decimal_number_spellings_are_not_numeric(text: str) -> None:
    assert check({"a": text}, "$.a", "gt", 0)["assertion"] == "ERROR"


def test_large_integers_compare_exactly() -> None:
    big = 12345678901234567
    assert check({"a": big}, "$.a", "gt", 12345678901234566)["assertion"] == "PASSED"
    assert check({"a": str(big)}, "$.a", "lt", "12345678901234568")["assertion"] == "PASSED"
    assert check({"a": big}, "$.a", "all_gte", str(big + 1))["assertion"] == "FAILED"
    assert check({"a": "-1.5e2"}, "$.a", "lt", -100)["assertion"] == "PASSED"
