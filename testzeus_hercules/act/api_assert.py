"""Deterministic JSON assertions for API responses and SQL rows (JSONPath + operator + expected value).

:func:`evaluate` returns a verdict dict ``{"assertion": "PASSED"|"FAILED"|"ERROR", ...}``;
``ERROR`` means the check could not be evaluated (bad path, unknown operator, a
value that is not a number), never that it passed. Per-value operators must hold
for every match; aggregate operators (``exists``, ``count_eq``, ``all_gte``,
``all_lte``) judge the matched set. Values captured earlier in the run are
referenced as ``${name}``: an exact placeholder keeps the captured value's type,
an embedded one is substituted as text, an unknown one is an error rather than a
comparison that can never be right.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable, Mapping

from testzeus_hercules.act import jsonpath

OPERATORS = ("eq", "ne", "contains", "not_contains", "matches", "exists", "not_exists", "not_empty", "gt", "lt", "all_gte", "all_lte", "count_eq", "eq_path")
WITHOUT_EXPECTED = ("exists", "not_exists", "not_empty")
DEFAULT_OPERATOR = "eq"
# Marks ``expected`` as a literal value: no ``${name}`` substitution and no operator-name guard.
LITERAL_PREFIX = "literal:"
MAX_PATTERN_CHARS = 500

_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")
_NUMBER_TEXT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_INTEGER_TEXT = re.compile(r"[+-]?\d+")
_MISPLACED = "Operator names belong in the operator, not in the expected value."


class _NotGiven:
    def __repr__(self) -> str:
        return "<not given>"


NOT_GIVEN: Any = _NotGiven()
Verdict = dict[str, Any]


# --------------------------------------------------------------------------- interpolation


def stringify(value: Any) -> str:
    """A value as text: strings unchanged, anything else as compact JSON (``null``, not ``None``)."""
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), default=str)


def interpolate(value: Any, variables: Mapping[str, Any]) -> Any:
    """Substitute ``${name}`` placeholders from ``variables``; an exact placeholder keeps the value's type."""
    if not isinstance(value, str):
        return value
    exact = _PLACEHOLDER.fullmatch(value.strip())
    if exact:
        name = exact.group(1).strip()
        return variables[name] if name in variables else value
    return _PLACEHOLDER.sub(lambda match: stringify(variables[match.group(1).strip()]) if match.group(1).strip() in variables else match.group(0), value)


def interpolate_tree(node: Any, variables: Mapping[str, Any]) -> Any:
    """:func:`interpolate` applied to every string leaf of a JSON-like tree."""
    if isinstance(node, dict):
        return {key: interpolate_tree(item, variables) for key, item in node.items()}
    if isinstance(node, list):
        return [interpolate_tree(item, variables) for item in node]
    return interpolate(node, variables)


def parse_payload(payload: Any) -> Any:
    """An already parsed document, or the parse of a JSON string (ValueError when it is not JSON)."""
    if isinstance(payload, (str, bytes, bytearray)):
        return json.loads(payload)
    return payload


# --------------------------------------------------------------------------- comparisons


def as_number(value: Any) -> int | float | None:
    """A finite number from a number or plain decimal text; integral values stay exact ints."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if _INTEGER_TEXT.fullmatch(text):
            return int(text)
        if _NUMBER_TEXT.fullmatch(text):
            number = float(text)
            return number if math.isfinite(number) else None
    return None


def _text_equal(actual: Any, expected: Any) -> tuple[bool, str | None]:
    if stringify(actual) == stringify(expected):
        return True, "Matched by text comparison across types (strict comparison disagreed)."
    return False, None


def values_equal(actual: Any, expected: Any) -> tuple[bool, str | None]:
    """Strict typed equality, else a text comparison across the string/number divide (recorded as a note)."""
    actual_bool, expected_bool = isinstance(actual, bool), isinstance(expected, bool)
    if actual_bool != expected_bool:
        return _text_equal(actual, expected)  # 1 == True must never pass silently
    if actual == expected:
        return True, None
    if actual_bool or expected_bool:
        return False, None
    return _text_equal(actual, expected)


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str):
        return (expected if isinstance(expected, str) else stringify(expected)) in actual
    if isinstance(actual, list):
        return any(values_equal(item, expected)[0] for item in actual)
    if isinstance(actual, dict):
        return isinstance(expected, str) and expected in actual
    return False


def _ordered(actual: Any, expected: Any, operator: str) -> tuple[bool, str | None]:
    left, right = as_number(actual), as_number(expected)
    if left is None or right is None:
        return False, f"Cannot compare non-numeric values ({actual!r} {operator} {expected!r})."
    return (left > right if operator == "gt" else left < right), None


def _strip_quoted(text: str) -> str:
    token = text.strip()
    return token[1:-1].strip() if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"') else token


def operator_lookalike(expected: Any) -> str | None:
    """The operator ``expected`` looks like (``not_empty``, ``count_eq:2``, ``ne_path``), else None."""
    if not isinstance(expected, str):
        return None
    token = _strip_quoted(expected).lower()
    if not token:
        return None
    if token in OPERATORS:
        return token
    head, separator, rest = token.partition(":")
    variants = {f"{operator}_path": operator for operator in OPERATORS}
    if separator and rest[:1] and not rest[:1].isspace():
        if head in OPERATORS:
            return head
        if head in variants:
            return variants[head]
    return variants.get(token) if not separator else None


def _binds_elements(path: str, matches: list[Any]) -> bool:
    return len(matches) == 1 and isinstance(matches[0], list) and jsonpath.is_singular(path)


def _check_each(matches: list[Any], check: Callable[[Any], tuple[bool, str | None]]) -> tuple[bool, str | None, int | None]:
    first_note: str | None = None
    for index, value in enumerate(matches):
        passed, note = check(value)
        if note and first_note is None:
            first_note = note
        if not passed:
            return False, note or first_note, index
    return True, first_note, None


# --------------------------------------------------------------------------- evaluation


def evaluate(document: Any, path: str, operator: str = DEFAULT_OPERATOR, expected: Any = NOT_GIVEN, *, variables: Mapping[str, Any] | None = None, guard: bool = True) -> Verdict:
    """Evaluate one assertion on ``document`` (parsed, or JSON text).

    ``guard`` rejects an expected value that is really an operator name (a slip a model
    makes); steps read by rules pass ``guard=False`` since their values are the author's words.
    """
    known = variables or {}
    name = str(operator or DEFAULT_OPERATOR).strip().lower() or DEFAULT_OPERATOR
    given = expected is not NOT_GIVEN
    value: Any = expected if given else None

    def payload(**overrides: Any) -> Verdict:
        return {"path": path, "operator": name, "actual": None, "expected": value, **overrides}

    literal = isinstance(value, str) and value.startswith(LITERAL_PREFIX)
    if literal:
        value = value[len(LITERAL_PREFIX) :]
    try:
        path = stringify(interpolate(path, known))
        if not literal:
            value = interpolate(value, known)
    except Exception as exc:
        return payload(assertion="ERROR", reason=f"Interpolation failed: {type(exc).__name__}: {exc}")
    if name not in OPERATORS:
        return payload(assertion="ERROR", reason=f"Unknown operator '{name}'. Valid operators: {', '.join(OPERATORS)}.")
    if not given and name not in WITHOUT_EXPECTED:
        return payload(assertion="ERROR", reason=f"Operator '{name}' needs an 'expected' value, but none was given. Only {', '.join(WITHOUT_EXPECTED)} may omit it.")
    lookalike = None if (literal or not guard) else operator_lookalike(value)
    if lookalike:
        if name == DEFAULT_OPERATOR and _strip_quoted(str(value)).lower() == lookalike:
            note = f"'{lookalike}' was given as the expected value with the default operator; reinterpreted as the operator."
            return _evaluate_operator(document, path, lookalike, None, note, known)
        return payload(assertion="ERROR", reason=f"The expected value looks like an operator name ('{value}'). {_MISPLACED}")
    if isinstance(value, str) and not literal:
        unresolved = _PLACEHOLDER.search(value)
        if unresolved:
            return payload(
                assertion="ERROR",
                reason=f"The expected value still contains the unresolved placeholder ${{{unresolved.group(1).strip()}}}: capture it first. "
                "A literal placeholder never equals a real value, so eq would always fail and ne would always pass.",
            )
    return _evaluate_operator(document, path, name, value, None, known)


def _count_verdict(payload: Callable[..., Verdict], expected: Any, count: int, count_of: str, matched: int) -> Verdict:
    number = as_number(expected)
    if number is None:
        return payload(assertion="ERROR", actual=count, reason=f"Expected count {expected!r} is not a number.")
    if number != int(number):
        return payload(assertion="ERROR", actual=count, reason=f"Expected count {expected!r} is not a whole number.")
    return payload(assertion="PASSED" if count == int(number) else "FAILED", actual=count, count_of=count_of, matched_count=matched)


def _bounded(matches: list[Any], expected: Any, operator: str, path: str) -> tuple[bool, str | None, bool]:
    bound = as_number(expected)
    if bound is None:
        return False, f"Expected value {expected!r} is not numeric.", True
    values = matches[0] if _binds_elements(path, matches) else matches
    if not values:
        return False, "The bound matched an empty set of values.", False
    for index, item in enumerate(values):
        number = as_number(item)
        if number is None:
            return False, f"Matched value at index {index} ({item!r}) is not numeric.", True
        if operator == "all_gte" and number < bound:
            return False, f"Matched value at index {index} ({item!r}) is below {expected!r}.", False
        if operator == "all_lte" and number > bound:
            return False, f"Matched value at index {index} ({item!r}) is above {expected!r}.", False
    return True, None, False


def _no_match(payload: Callable[..., Verdict], document: Any, path: str, operator: str, variables: Mapping[str, Any]) -> Verdict:
    failure: Verdict = {"assertion": "FAILED", "reason": f"Path matched nothing (operator '{operator}' could not be evaluated).", "matched_count": 0}
    parent = jsonpath.closest_parent(document, path)
    if parent and parent.get("keys"):
        failure["available_keys"] = parent["keys"]
        failure["reason"] += f" Keys available at '{parent['location']}': {', '.join(parent['keys'])}."
    elif parent:
        failure["reason"] += f" The closest resolvable parent '{parent['location']}' is not a container."
    leaf = jsonpath.leaf_name(path)
    if leaf and leaf in variables:
        failure["hint"] = f"'{leaf}' is a captured value; to compare against it, reference it as ${{{leaf}}} instead of as a path segment."
    return payload(**failure)


def _evaluate_operator(document: Any, path: str, operator: str, expected: Any, note: str | None, variables: Mapping[str, Any]) -> Verdict:
    def payload(**overrides: Any) -> Verdict:
        base: Verdict = {"path": path, "operator": operator, "actual": None, "expected": expected}
        if note:
            base["note"] = note
        return {**base, **overrides}

    try:
        document = parse_payload(document)
    except ValueError as exc:
        return payload(assertion="ERROR", reason=f"The response is not valid JSON: {exc}")
    try:
        matches = jsonpath.find(document, path)
    except ImportError as exc:
        return payload(assertion="ERROR", reason=str(exc))
    except Exception as exc:
        return payload(assertion="ERROR", reason=f"Invalid JSONPath '{path}': {exc}")
    if operator == "not_exists":
        return payload(assertion="PASSED" if not matches else "FAILED", actual=matches[0] if matches else None, matched_count=len(matches))
    if not matches and operator == "count_eq":
        return _count_verdict(payload, expected, 0, "path matches", 0)
    if not matches:
        return _no_match(payload, document, path, operator, variables)
    actual, count = matches[0], len(matches)
    shown = actual if count == 1 else list(matches)

    def each_reason(index: int | None) -> str | None:
        if index is None or count == 1:
            return None
        return f"Match {index} of {count} ({matches[index]!r}) did not satisfy '{operator}'; with several matches the operator must hold for every one (narrow the path, or use exists/count_eq/all_gte/all_lte)."

    if operator == "exists":
        return payload(assertion="PASSED", actual=actual, matched_count=count)
    if operator == "not_empty":
        return _not_empty(payload, matches, shown, count)
    if operator in ("gt", "lt"):
        errors: list[str] = []

        def ordered(item: Any) -> tuple[bool, str | None]:
            ok, problem = _ordered(item, expected, operator)
            if problem:
                errors.append(problem)
            return ok, problem

        passed, _, failed_index = _check_each(matches, ordered)
        if errors:
            return payload(assertion="ERROR", actual=shown, reason=errors[0])
        verdict = payload(assertion="PASSED" if passed else "FAILED", actual=shown, matched_count=count)
        if not passed and count > 1:
            verdict["reason"] = each_reason(failed_index)
        return verdict
    if operator in ("all_gte", "all_lte"):
        passed, problem, is_error = _bounded(matches, expected, operator, path)
        if is_error:
            return payload(assertion="ERROR", actual=actual, reason=problem)
        return payload(assertion="PASSED" if passed else "FAILED", actual=actual, matched_count=count, reason=None if passed else problem)
    if operator == "count_eq":
        if _binds_elements(path, matches):
            return _count_verdict(payload, expected, len(actual), "array elements", count)
        return _count_verdict(payload, expected, count, "path matches", count)
    if operator == "eq_path":
        return _eq_path(payload, document, actual, expected, count, note)
    if operator == "matches":
        return _matches(payload, matches, expected, shown, count, each_reason)
    if operator in ("contains", "not_contains"):
        wanted = operator == "contains"
        passed, _, failed_index = _check_each(matches, lambda item: (_contains(item, expected) == wanted, None))
        verdict = payload(assertion="PASSED" if passed else "FAILED", actual=shown, matched_count=count)
        if not passed and count > 1:
            verdict["reason"] = each_reason(failed_index)
        return verdict
    notes: list[str] = []

    def equals(item: Any) -> tuple[bool, str | None]:
        equal, equal_note = values_equal(item, expected)
        if equal_note:
            notes.append(equal_note)
        return (not equal if operator == "ne" else equal), None

    passed, _, failed_index = _check_each(matches, equals)
    eq_note = notes[0] if notes else None
    if operator == "ne" and eq_note:
        eq_note = eq_note.replace("Matched by text comparison", "Declared not-equal, but the values match by text comparison")
    result = payload(assertion="PASSED" if passed else "FAILED", actual=shown, matched_count=count)
    if not passed and count > 1:
        result["reason"] = each_reason(failed_index)
    if eq_note:
        result["note"] = f"{note}; {eq_note}" if note else eq_note
    return result


def _not_empty(payload: Callable[..., Verdict], matches: list[Any], shown: Any, count: int) -> Verdict:
    def non_empty(item: Any) -> tuple[bool, str | None]:
        if item is None:
            return False, "Value is null."
        if isinstance(item, str) and not item.strip():
            return False, "Value is a blank string."
        if isinstance(item, (list, dict)) and not item:
            return False, "Value is an empty container."
        return True, None

    passed, problem, failed_index = _check_each(matches, non_empty)
    if not passed:
        return payload(assertion="FAILED", actual=shown, matched_count=count, reason=problem if count == 1 else f"Match {failed_index} of {count}: {problem}")
    return payload(assertion="PASSED", actual=shown, matched_count=count)


def _eq_path(payload: Callable[..., Verdict], document: Any, actual: Any, expected: Any, count: int, note: str | None) -> Verdict:
    if not isinstance(expected, str):
        return payload(assertion="ERROR", actual=actual, reason="eq_path expects a second JSONPath as the expected value.")
    try:
        others = jsonpath.find(document, expected)
    except ImportError as exc:
        return payload(assertion="ERROR", actual=actual, reason=str(exc))
    except Exception as exc:
        return payload(assertion="ERROR", actual=actual, reason=f"Invalid comparison JSONPath '{expected}': {exc}")
    if not others:
        return payload(assertion="ERROR", actual=actual, reason=f"Comparison path '{expected}' matched nothing.")
    passed, eq_note = values_equal(actual, others[0])
    result = payload(assertion="PASSED" if passed else "FAILED", actual=actual, expected=others[0], compared_path=expected, matched_count=count)
    if eq_note:
        result["note"] = f"{note}; {eq_note}" if note else eq_note
    return result


def _matches(payload: Callable[..., Verdict], matches: list[Any], expected: Any, shown: Any, count: int, each_reason: Callable[[int | None], str | None]) -> Verdict:
    pattern_text = stringify(expected)
    if len(pattern_text) > MAX_PATTERN_CHARS:
        return payload(assertion="ERROR", actual=shown, reason=f"The pattern is longer than {MAX_PATTERN_CHARS} characters.")
    try:
        pattern = re.compile(pattern_text)
    except re.error as exc:
        return payload(assertion="ERROR", actual=shown, reason=f"Invalid regular expression {pattern_text!r}: {exc}")
    passed, _, failed_index = _check_each(matches, lambda item: (pattern.search(stringify(item)) is not None, None))
    verdict = payload(assertion="PASSED" if passed else "FAILED", actual=shown, matched_count=count)
    if not passed and count > 1:
        verdict["reason"] = each_reason(failed_index)
    return verdict
