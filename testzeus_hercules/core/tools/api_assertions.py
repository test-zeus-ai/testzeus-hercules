"""
Deterministic JSON assertions for API responses.

The planner must not read a response and decide pass/fail itself — that makes
the same test flake across reruns with no code change. Instead the agent calls
:func:`assert_json_path`, which evaluates the check in code and returns an
explicit PASSED/FAILED/ERROR verdict (as minified JSON) for the agent to relay.

Every assertion — pass included — is appended to the audit ledger
(``assertion_logger``), tagged with a short fingerprint of the examined payload
(``assertion_subject``), so the report side can deduplicate retries without
folding away cross-page checks.
"""

import json
import math
import re
from typing import Annotated, Any, Callable, Dict, List, Optional, Tuple, Union

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.api_calls import (
    _var_store,
    interpolate_value,
    parse_json_payload,
    sensitive_variable_values,
    stringify_interpolated_value,
)
from testzeus_hercules.core.tools.tool_registry import (
    assertion_logger,
    assertion_subject,
    tool,
)
from testzeus_hercules.utils.jsonpath_helper import (
    closest_parent_keys,
    find_json_path,
    is_singular_path,
    path_leaf_name,
)
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.redact import redact_value

OPERATORS = (
    "eq",
    "ne",
    "contains",
    "exists",
    "not_exists",
    "not_empty",
    "gt",
    "lt",
    "all_gte",
    "all_lte",
    "count_eq",
    "eq_path",
)

DEFAULT_OPERATOR = "eq"

# Prefix that marks `expected` as a literal value, bypassing the operator-name
# lookalike guard ("literal:exists" asserts the text "exists").
LITERAL_PREFIX = "literal:"

_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\$\{([^{}]+)\}")

# Operators that judge presence/emptiness only and so legitimately omit `expected`.
OPERATORS_WITHOUT_EXPECTED = ("exists", "not_exists", "not_empty")


class _ExpectedNotGiven:
    """Sentinel default for `expected`: tells "omitted" apart from an explicit null."""

    def __repr__(self) -> str:
        return "<not given>"


EXPECTED_NOT_GIVEN = _ExpectedNotGiven()

# Plain decimal number text (JSON-literal style plus a leading '+'): no "1_000",
# "inf", "nan", "0x10" or surrounding junk.
_NUMBER_TEXT_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_INTEGER_TEXT_RE = re.compile(r"[+-]?\d+")

# Guidance shown when an operator name is found inside `expected` instead of
# the `operator` parameter — a recurring LLM slip that would otherwise judge
# the wrong thing silently.
_OPERATOR_MISPLACED_GUIDANCE = (
    "Operator names belong in the 'operator' parameter, not inside 'expected'. " "Retry with the operator passed via 'operator' and the compared value (or " "JSONPath) passed via 'expected'."
)


def _minified(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _strip_quoted(text: str) -> str:
    token = text.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1].strip()
    return token


def _operator_lookalike(expected: Any) -> Optional[str]:
    """
    Return the operator name that `expected` looks like, or None.

    Only unambiguous slips match: a bare operator name ("not_empty"), an
    operator glued to a value with a colon ("count_eq:2", "ne_path:$.x") and
    the bare path variants ("ne_path"). Ordinary strings that merely start
    with an operator word ("exists_in_cart", "contains the cart") are legitimate
    expected values and are NOT flagged; use the "literal:" prefix for a value
    that is exactly an operator name.
    """
    if not isinstance(expected, str):
        return None
    token = _strip_quoted(expected).lower()
    if not token:
        return None
    if token in OPERATORS:
        return token
    head, separator, rest = token.partition(":")
    variant_of = {f"{operator}_path": operator for operator in OPERATORS}
    if separator and rest[:1] and not rest[:1].isspace():
        if head in OPERATORS:
            return head
        if head in variant_of:
            return variant_of[head]
    if not separator and token in variant_of:
        return variant_of[token]
    return None


def _as_number(value: Any) -> Optional[Union[int, float]]:
    """
    Coerce a value (or plain decimal text) to a finite number, else None.

    Integral inputs stay ints so large ids (beyond 2**53) compare exactly
    instead of after a lossy float conversion; Python compares int/float
    pairs exactly too. "1_000", "inf", "nan" and other non-decimal spellings
    that float() would accept are rejected.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if _INTEGER_TEXT_RE.fullmatch(text):
            return int(text)
        if _NUMBER_TEXT_RE.fullmatch(text):
            number = float(text)
            return number if math.isfinite(number) else None
    return None


def _text_equal(actual: Any, expected: Any) -> tuple:
    actual_text = stringify_interpolated_value(actual)
    expected_text = stringify_interpolated_value(expected)
    if actual_text == expected_text:
        return True, "Matched by text comparison across types (strict comparison disagreed)."
    return False, None


def _values_equal(actual: Any, expected: Any) -> tuple:
    """
    Strict typed equality first; text fallback across the string/number divide
    (int64 ids usually arrive as strings). Returns (equal, note|None) — the
    caller records the note when the loose match decided the result, so a
    widened comparison is never silent.
    """
    actual_is_bool = isinstance(actual, bool)
    expected_is_bool = isinstance(expected, bool)
    if actual_is_bool != expected_is_bool:
        # bool is an int subclass: `1 == True` strict-passes, which would
        # silently widen a typed check. A bool-vs-non-bool pair falls through
        # to the text comparison instead, so any widened match is recorded.
        return _text_equal(actual, expected)
    if actual == expected:
        return True, None
    if actual_is_bool or expected_is_bool:
        # bool is an int subclass; "true" vs 1 must not match loosely.
        return False, None
    return _text_equal(actual, expected)


def _compare_eq(actual: Any, expected: Any) -> tuple:
    return _values_equal(actual, expected)


def _compare_contains(actual: Any, expected: Any) -> tuple:
    if isinstance(actual, str):
        needle = expected if isinstance(expected, str) else stringify_interpolated_value(expected)
        return (needle in actual), None
    if isinstance(actual, list):
        for item in actual:
            equal, _ = _values_equal(item, expected)
            if equal:
                return True, None
        return False, None
    if isinstance(actual, dict):
        if isinstance(expected, str) and expected in actual:
            return True, None
        return False, None
    return False, None


def _compare_ordered(actual: Any, expected: Any, operator: str) -> tuple:
    actual_number = _as_number(actual)
    expected_number = _as_number(expected)
    if actual_number is None or expected_number is None:
        return False, f"Cannot compare non-numeric values ({actual!r} {operator} {expected!r})."
    if operator == "gt":
        return (actual_number > expected_number), None
    return (actual_number < expected_number), None


def _binds_array_elements(path: str, matches: List[Any]) -> bool:
    """
    True when the path names exactly one array (no wildcard/slice/filter/union/
    recursive descent anywhere in it), so count_eq/all_gte/all_lte act on its
    elements ($.body.items). A multi-valued path that happens to match one
    value that is itself a list ($.m[*] on [[1,2,3]]) binds that single match.
    """
    return len(matches) == 1 and isinstance(matches[0], list) and is_singular_path(path)


def _compare_bounded(matches: List[Any], expected: Any, operator: str, path: str) -> tuple:
    """
    all_gte/all_lte bind every matched value (like count_eq, a singular path
    landing on one array binds its elements); an empty bound set is FAILED,
    not vacuously true. Returns (passed, note, is_error) — is_error marks an
    unevaluable (type) problem rather than an evaluable mismatch.
    """
    expected_number = _as_number(expected)
    if expected_number is None:
        return False, f"Expected value {expected!r} is not numeric.", True
    values = matches[0] if _binds_array_elements(path, matches) else matches
    if not values:
        return False, "The bound matched an empty set of values.", False
    for index, value in enumerate(values):
        value_number = _as_number(value)
        if value_number is None:
            return False, f"Matched value at index {index} ({value!r}) is not numeric.", True
        if operator == "all_gte" and value_number < expected_number:
            return False, f"Matched value at index {index} ({value!r}) is below {expected!r}.", False
        if operator == "all_lte" and value_number > expected_number:
            return False, f"Matched value at index {index} ({value!r}) is above {expected!r}.", False
    return True, None, False


def _check_each(matches: List[Any], check: Callable[[Any], Tuple[bool, Optional[str]]]) -> Tuple[bool, Optional[str], Optional[int]]:
    """Apply `check(value) -> (passed, note)` to EVERY match; returns (passed, note, first_failing_index)."""
    first_note: Optional[str] = None
    for index, value in enumerate(matches):
        passed, note = check(value)
        if note and first_note is None:
            first_note = note
        if not passed:
            return False, note or first_note, index
    return True, first_note, None


def _record(response_json: Any, result: Dict[str, Any]) -> None:
    """Append the verdict to the audit ledger, tagged with the payload fingerprint."""
    # Ledger values are redacted: a secret-named path (or the value of a
    # secret-named captured variable) never lands in the audit file.
    key_hint = f"{result.get('path') or ''} {result.get('compared_path') or ''}"
    secrets = sensitive_variable_values()
    entry = {
        "source": "assert_json_path",
        "test_id": get_global_conf().get_default_test_id(),
        "subject": assertion_subject(_payload_text(response_json)),
        "path": result.get("path"),
        "operator": result.get("operator"),
        "expected": redact_value(result.get("expected"), key_hint, secrets),
        "actual": redact_value(result.get("actual"), key_hint, secrets),
        "verdict": result.get("assertion"),
    }
    for optional_key in ("note", "reason"):
        if result.get(optional_key):
            entry[optional_key] = redact_value(result[optional_key], "", secrets)
    try:
        assertion_logger(json.dumps(entry, separators=(",", ":"), default=str))
    except Exception as e:
        # The ledger must never turn a verdict into a crash.
        logger.warning(f"Failed to append to the assertion ledger: {e}")


def _payload_text(response_json: Any) -> str:
    if isinstance(response_json, str):
        return response_json
    try:
        return json.dumps(response_json, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(response_json)


@tool(
    agent_names=["api_nav_agent"],
    name="assert_json_path",
    description=(
        "Deterministically assert a value in a JSON response body via JSONPath. "
        "NEVER decide pass/fail by reading the response yourself — call this "
        "tool and use its returned 'assertion' value (PASSED/FAILED/ERROR). "
        f"Operators: {', '.join(OPERATORS)}. "
        "'expected' is required except for exists/not_exists/not_empty (pass null explicitly to compare against null) "
        "and may reference captured values as ${name}. "
        "When several values match the path, eq/ne/contains/gt/lt/not_empty must hold for EVERY match "
        "(use exists, count_eq, all_gte/all_lte or a narrower path for aggregate checks). "
        f"To assert a value that is exactly an operator name, pass it as '{LITERAL_PREFIX}<value>'."
    ),
)
async def assert_json_path(
    response_json: Annotated[Any, "The JSON response body (object or JSON string) to assert against."],
    path: Annotated[str, "JSONPath expression selecting the value(s) to check, e.g. $.body.items[0].id."],
    operator: Annotated[str, f"Comparison operator. One of: {', '.join(OPERATORS)}. Defaults to eq."] = DEFAULT_OPERATOR,
    expected: Annotated[
        Any,
        "Expected value (or a second JSONPath when operator is eq_path). Required for every operator except exists/not_exists/not_empty; pass null explicitly to compare against null.",
    ] = EXPECTED_NOT_GIVEN,
) -> Annotated[str, "Minified JSON verdict: {assertion: PASSED|FAILED|ERROR, path, operator, actual, expected, ...}."]:
    try:
        result = _evaluate(response_json, path, operator, expected)
    except Exception as e:
        logger.exception("assert_json_path failed unexpectedly")
        shown_expected = None if expected is EXPECTED_NOT_GIVEN else expected
        result = {"assertion": "ERROR", "path": path, "operator": operator, "expected": shown_expected, "reason": f"Assertion could not be evaluated: {type(e).__name__}: {e}"}
    _record(response_json, result)
    return _minified(result)


def _evaluate(response_json: Any, path: str, operator: str, expected: Any = EXPECTED_NOT_GIVEN) -> Dict[str, Any]:
    """Run one assertion and build its verdict payload."""
    operator_name = str(operator or DEFAULT_OPERATOR).strip().lower() or DEFAULT_OPERATOR
    expected_given = expected is not EXPECTED_NOT_GIVEN
    if not expected_given:
        expected = None

    def _payload(**overrides: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {"path": path, "operator": operator_name, "actual": None, "expected": expected}
        base.update(overrides)
        return base

    # ---- ${name} interpolation (runs once, upstream of every branch below —
    # in particular before the operator-swap reinterpretation, so a path that
    # contains placeholders is clean no matter which branch evaluates it) ----
    try:
        path = stringify_interpolated_value(interpolate_value(path))
        expected = interpolate_value(expected)
    except Exception as e:
        return _payload(assertion="ERROR", reason=f"Interpolation failed: {type(e).__name__}: {e}")

    # ---- input judgment rules (run before anything can look green) --------
    if operator_name not in OPERATORS:
        return _payload(
            assertion="ERROR",
            reason=f"Unknown operator '{operator_name}'. Valid operators: {', '.join(OPERATORS)}.",
        )

    if not expected_given and operator_name not in OPERATORS_WITHOUT_EXPECTED:
        return _payload(
            assertion="ERROR",
            reason=(
                f"Operator '{operator_name}' needs an 'expected' value, but none was given. " f"Only {', '.join(OPERATORS_WITHOUT_EXPECTED)} may omit it; pass null explicitly to compare against null."
            ),
        )

    literal = isinstance(expected, str) and expected.startswith(LITERAL_PREFIX)
    if literal:
        expected = expected[len(LITERAL_PREFIX) :]

    lookalike = None if literal else _operator_lookalike(expected)
    if lookalike:
        if operator_name == DEFAULT_OPERATOR and _strip_quoted(str(expected)).lower() == lookalike:
            # `expected` is exactly an operator name and `operator` is still
            # the default: the agent swapped the parameters. Reinterpret.
            operator_name = lookalike
            expected = None
            note = f"'{lookalike}' was passed as 'expected' with the default operator; " "reinterpreted as the operator. Pass operators via the 'operator' parameter."
            return _evaluate_with_operator(response_json, path, operator_name, expected, note)
        return _payload(
            assertion="ERROR",
            reason=(f"'expected' looks like an operator name ('{expected}'). " f"{_OPERATOR_MISPLACED_GUIDANCE}"),
        )

    # A literal: value is compared as written, placeholder text included.
    if isinstance(expected, str) and not literal:
        unresolved = _UNRESOLVED_PLACEHOLDER_RE.search(expected)
        if unresolved:
            return _payload(
                assertion="ERROR",
                reason=(
                    f"Expected value still contains the unresolved placeholder ${{{unresolved.group(1).strip()}}}. "
                    "A literal placeholder never equals a real value, so eq would always fail and "
                    "ne would always pass. Capture it first (extract_from_response) or assert on the "
                    "placeholder-free value."
                ),
            )

    return _evaluate_with_operator(response_json, path, operator_name, expected, None)


def _count_verdict(payload: Callable[..., Dict[str, Any]], expected: Any, count: int, count_of: str, matched_count: int) -> Dict[str, Any]:
    """count_eq verdict for an already computed `count`."""
    expected_number = _as_number(expected)
    if expected_number is None:
        return payload(assertion="ERROR", actual=count, reason=f"Expected count {expected!r} is not a number.")
    if expected_number != int(expected_number):
        # int(2.5) == 2 would silently turn "2.5" into a passing count of 2.
        return payload(assertion="ERROR", actual=count, reason=f"Expected count {expected!r} is not a whole number.")
    passed = count == int(expected_number)
    return payload(assertion="PASSED" if passed else "FAILED", actual=count, count_of=count_of, matched_count=matched_count)


def _evaluate_with_operator(response_json: Any, path: str, operator: str, expected: Any, note: Optional[str]) -> Dict[str, Any]:
    """Evaluate the assertion once path/expected are clean and operator final."""

    def _payload(**overrides: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {"path": path, "operator": operator, "actual": None, "expected": expected}
        if note:
            base["note"] = note
        base.update(overrides)
        return base

    try:
        document = parse_json_payload(response_json)
    except ValueError as e:
        return _payload(assertion="ERROR", reason=f"Response is not valid JSON: {e}")

    try:
        matches = find_json_path(document, path)
    except ImportError as e:
        return _payload(assertion="ERROR", reason=str(e))
    except Exception as e:
        return _payload(assertion="ERROR", reason=f"Invalid JSONPath '{path}': {e}")

    if operator == "not_exists":
        verdict = "PASSED" if not matches else "FAILED"
        return _payload(assertion=verdict, actual=None if not matches else matches[0], matched_count=len(matches))

    if not matches and operator == "count_eq":
        # Zero matches is a count of 0 (e.g. $.items[*] on an empty array).
        return _count_verdict(_payload, expected, 0, "path matches", 0)

    if not matches:
        failure: Dict[str, Any] = {
            "assertion": "FAILED",
            "reason": f"Path matched nothing in the response body (operator '{operator}' could not be evaluated).",
            "matched_count": 0,
        }
        parent_info = closest_parent_keys(document, path)
        if parent_info and parent_info.get("keys"):
            failure["available_keys"] = parent_info["keys"]
            failure["reason"] += f" Keys available at '{parent_info['location']}': {', '.join(parent_info['keys'])}."
        elif parent_info:
            failure["reason"] += f" The closest resolvable parent '{parent_info['location']}' is not a container."
        leaf = path_leaf_name(path)
        if leaf and leaf in _var_store():
            failure["hint"] = f"The path segment '{leaf}' is a captured variable name; to compare against its " f"value, reference it as ${{{leaf}}} instead of as a path segment."
        return _payload(**failure)

    actual = matches[0]
    matched_count = len(matches)
    # Per-value operators judge EVERY match; several matches are reported as the full list.
    shown = actual if matched_count == 1 else list(matches)

    def _each_reason(index: Optional[int], detail: str = "did not satisfy") -> Optional[str]:
        if index is None or matched_count == 1:
            return None
        return f"Match {index} of {matched_count} ({matches[index]!r}) {detail} '{operator}'; with several matches the operator must hold for every one (narrow the path, or use exists/count_eq/all_gte/all_lte)."

    if operator == "exists":
        return _payload(assertion="PASSED", actual=actual, matched_count=matched_count)
    if operator == "not_empty":

        def _non_empty(value: Any) -> Tuple[bool, Optional[str]]:
            if value is None:
                return False, "Value is null."
            if isinstance(value, str) and not value.strip():
                return False, "Value is a blank string."
            if isinstance(value, (list, dict)) and not value:
                return False, "Value is an empty container."
            return True, None

        passed, empty_note, failed_index = _check_each(matches, _non_empty)
        if not passed:
            reason = empty_note if matched_count == 1 else f"Match {failed_index} of {matched_count}: {empty_note}"
            return _payload(assertion="FAILED", actual=shown, matched_count=matched_count, reason=reason)
        return _payload(assertion="PASSED", actual=shown, matched_count=matched_count)
    if operator in ("gt", "lt"):
        bound_error: List[str] = []

        def _ordered(value: Any) -> Tuple[bool, Optional[str]]:
            passed_one, error_note = _compare_ordered(value, expected, operator)
            if error_note:
                bound_error.append(error_note)
                return False, error_note
            return passed_one, None

        passed, _bound_note, failed_index = _check_each(matches, _ordered)
        if bound_error:
            return _payload(assertion="ERROR", actual=shown, reason=bound_error[0])
        verdict = _payload(assertion="PASSED" if passed else "FAILED", actual=shown, matched_count=matched_count)
        if not passed and matched_count > 1:
            verdict["reason"] = _each_reason(failed_index)
        return verdict
    if operator in ("all_gte", "all_lte"):
        passed, bound_note, is_error = _compare_bounded(matches, expected, operator, path)
        if is_error:
            return _payload(assertion="ERROR", actual=actual, reason=bound_note)
        return _payload(
            assertion="PASSED" if passed else "FAILED",
            actual=actual,
            matched_count=matched_count,
            reason=None if passed else bound_note,
        )
    if operator == "count_eq":
        # A singular path landing on one array counts its elements
        # ($.body.items matches the array itself). A multi-valued path
        # (wildcard, slice, filter, ...) counts its matches, even when there is
        # exactly one match that happens to be a list ($.m[*] on [[1,2,3]] is 1).
        if _binds_array_elements(path, matches):
            return _count_verdict(_payload, expected, len(actual), "array elements", matched_count)
        return _count_verdict(_payload, expected, matched_count, "path matches", matched_count)
    if operator == "eq_path":
        # Cross-field check: compare the first match against a second JSONPath.
        if not isinstance(expected, str):
            return _payload(assertion="ERROR", actual=actual, reason="eq_path expects a second JSONPath string as 'expected'.")
        try:
            other_matches = find_json_path(document, expected)
        except ImportError as e:
            return _payload(assertion="ERROR", actual=actual, reason=str(e))
        except Exception as e:
            return _payload(assertion="ERROR", actual=actual, reason=f"Invalid comparison JSONPath '{expected}': {e}")
        if not other_matches:
            return _payload(
                assertion="ERROR",
                actual=actual,
                reason=f"Comparison path '{expected}' matched nothing in the response body.",
            )
        other = other_matches[0]
        passed, eq_note = _compare_eq(actual, other)
        result = _payload(assertion="PASSED" if passed else "FAILED", actual=actual, expected=other, compared_path=expected, matched_count=matched_count)
        if eq_note:
            result["note"] = f"{note}; {eq_note}" if note else eq_note
        return result

    # eq / ne / contains: every match must satisfy the operator.
    if operator == "contains":
        passed, contains_note, failed_index = _check_each(matches, lambda value: _compare_contains(value, expected))
        verdict = _payload(
            assertion="PASSED" if passed else "FAILED",
            actual=shown,
            matched_count=matched_count,
            note=contains_note or note,
        )
        if not passed and matched_count > 1:
            verdict["reason"] = _each_reason(failed_index)
        return verdict

    eq_notes: List[str] = []

    def _equals(value: Any) -> Tuple[bool, Optional[str]]:
        equal, equal_note = _compare_eq(value, expected)
        if equal_note:
            eq_notes.append(equal_note)
        if operator == "ne":
            return (not equal), None
        return equal, None

    passed, _unused_note, failed_index = _check_each(matches, _equals)
    eq_note = eq_notes[0] if eq_notes else None
    if operator == "ne" and eq_note:
        eq_note = eq_note.replace("Matched by text comparison", "Declared not-equal, but the values match by text comparison")
    verdict_label = "PASSED" if passed else "FAILED"
    result = _payload(assertion=verdict_label, actual=shown, matched_count=matched_count)
    if not passed and matched_count > 1:
        result["reason"] = _each_reason(failed_index)
    if eq_note:
        result["note"] = f"{note}; {eq_note}" if note else eq_note
    return result
