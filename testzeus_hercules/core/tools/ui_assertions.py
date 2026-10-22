"""
Deterministic assertions over the visible text of a page-read payload.

The planner must not read page text and decide pass/fail itself — that makes
the same UI test flake across reruns with no code change. Instead the agent
feeds the payload of a page-reading tool (get_page_text-style) into
:func:`assert_ui`, which evaluates the check in code and returns an explicit
PASSED/FAILED/ERROR verdict (as minified JSON) for the agent to relay.
:func:`capture_ui_value` extracts a value from the same text and stores it in
the per-scenario ``${name}`` variable store shared with the API tools, so a
value seen on a page can be cross-checked against an API response later.

These tools are OPT-IN: they are registered on the browser/MCP/mobile nav
agents only when ``ENABLE_UI_ASSERTIONS=true`` (default off), and the agent
prompts mention them only then (see :func:`prompt_addendum_for`).

Before matching, the payload is normalized:

- a JSON payload is searched ONLY in its string values (keys, numbers and
  structure never satisfy a text assertion);
- URLs and id/test-id/class-like attribute assignments are stripped, so a
  value that exists only inside a hidden attribute (e.g. ``id="login"``) or a
  link target cannot satisfy a visible-text assertion (pass ``raw=true`` to
  keep them, e.g. to assert that a link target is shown);
- an empty or blank payload is an ERROR — never a silent pass;
- ``scope`` narrows matching to the paragraph (block of consecutive non-empty
  lines) containing the first occurrence of the scope substring.

Placeholder heuristics (mode ``no_placeholder``) — best-effort shape scans,
not guarantees. Each heuristic trades recall for fewer false positives, so
unusual placeholder shapes can slip through and ordinary copy that merely
resembles a shape is deliberately spared. The documented tradeoffs:

- bare ``undefined``, ``NaN`` and ``[object Object]`` tokens (word-bounded,
  so a word like "undefinedness" is not flagged; prose-adjective use like
  "undefined behavior" is spared, while a standalone or punctuation-
  delimited token is flagged);
- unsubstituted ``{{template}}`` / ``${template}`` placeholders;
- raw markdown ATX headers (a line starting with 1-6 ``#`` followed by
  whitespace — ``#1 ranked`` is not flagged, there is no space);
- leaked i18n keys: a dot-separated token of >= 3 segments where every
  segment is a lowercase identifier of >= 4 characters (e.g.
  ``checkout.payment.declined``). The 4-character floor keeps domains
  (``www.example.com`` contains the 3-char segment ``com``) and short
  abbreviations out; candidates ending in a common file extension (e.g.
  ``config.settings.json``) are file names, not keys, and are spared.

Every verdict — pass included — is appended to the assertion audit ledger
(``assertion_logger``), tagged with a fingerprint of the normalized search
text (``assertion_subject``), so the report side can deduplicate retries of
the same page without folding away checks against different pages.
"""

import json
import re
from typing import Annotated, Any, Dict, List, Optional, Tuple

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.api_calls import (
    interpolate_value,
    sensitive_variable_values,
    set_variable_value,
    stringify_interpolated_value,
)
from testzeus_hercules.core.tools.tool_registry import (
    assertion_logger,
    assertion_subject,
    tool,
    tool_registry,
)
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.redact import redact_value

MODES = ("contains", "absent", "count_eq", "no_placeholder")

_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\$\{([^{}]+)\}")

# URL-shaped tokens (link targets, image sources) are noise for visible-text
# assertions and hide the placeholder shapes the no_placeholder mode hunts for.
_URL_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://\S*")

# id=/testId=/class=-style attribute assignments (DOM dumps). Quoted,
# single-quoted or bare values; kept conservative — nothing else is stripped.
# The key must be glued to its '=' (as in real markup): spaced prose phrasing
# like "Travel class = economy" is natural copy, not an attribute.
_ATTR_ASSIGNMENT_RE = re.compile(r"\b(?:data-)?(?:test-?id|testid|id|class|classname)=(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)

# Bare "undefined"/"NaN" tokens; word-bounded so longer words never match.
# A token followed by a space + lowercase word reads as an English adjective
# in prose ("undefined behavior") and is spared; a standalone or
# punctuation-delimited token reads as a leaked value and is flagged.
_UNDEFINED_TOKEN_RE = re.compile(r"\bundefined\b(?![ \t]+[a-z])")
_NAN_TOKEN_RE = re.compile(r"\bNaN\b(?![ \t]+[a-z])")

# Unsubstituted mustache/handlebars and ${} templates (bounded so a stray
# brace pair in a huge minified blob cannot explode the scan).
_TEMPLATE_RE = re.compile(r"\{\{[^{}]{0,200}\}\}|\$\{[^{}]{0,200}\}")

# Raw markdown ATX header line: 1-6 '#' then whitespace then content.
_MARKDOWN_HEADER_RE = re.compile(r"^[ \t]{0,3}#{1,6}\s+\S", re.MULTILINE)

# Leaked i18n key: >= 3 dot-separated lowercase identifier segments, each
# >= 4 chars (keeps domains and short abbreviations out). Candidates ending
# in a common file extension ("config.settings.json") are file names, not
# keys.
_I18N_KEY_RE = re.compile(r"(?<![\w.])[a-z][a-z0-9_]{3,}(?:\.[a-z][a-z0-9_]{3,}){2,}(?![\w.])")
_FILE_NAME_EXT_RE = re.compile(r"\.(?:json|js|ts|css|html|png|jpg|svg)$")

UI_ASSERTION_AGENTS = ["browser_nav_agent", "mcp_nav_agent", "mobile_nav_agent"]

_ASSERT_UI_DESCRIPTION = (
    "Deterministically assert on the visible text returned by a page-reading "
    "tool. NEVER decide pass/fail by reading the page yourself — call this "
    "tool and use its returned 'assertion' value (PASSED/FAILED/ERROR). "
    f"Modes: {', '.join(MODES)}. "
    "'contains': expected text is visible. "
    "'absent': forbidden text is NOT visible; REQUIRES must_contain (a "
    "string that must be visible) as positive control proving the page "
    "actually rendered — without it the check is rejected, because absent "
    "passes trivially on an unrendered page. "
    "'count_eq': expected text occurs exactly 'count' times. "
    "'no_placeholder': best-effort scan for unrendered placeholder shapes "
    "(undefined / NaN tokens, [object Object], {{tpl}}/${tpl}, raw "
    "markdown headers, leaked i18n keys). Heuristic, not a guarantee: "
    "prose that merely resembles a shape (e.g. 'undefined behavior', "
    "'config.settings.json') is deliberately spared, and unusual "
    "placeholder shapes may be missed. "
    "JSON payloads are searched only in their string values; ids/testIds/"
    "class names/URLs are stripped before matching (set raw=true to keep "
    "URLs and attribute assignments, e.g. to assert a link target is shown). "
    "'scope' narrows matching to the paragraph containing it. "
    "expected/forbidden/must_contain/scope may reference captured values "
    "as ${name}."
)

_CAPTURE_UI_VALUE_DESCRIPTION = (
    "Capture a value from the visible text returned by a page-reading tool "
    "into a named variable for later use. Reference the captured value in "
    "subsequent calls or assertions (including the API tools) as "
    "${as_name}. Strategy: when 'path_hint' already IS the value it is "
    "captured verbatim (whole-line match first); when the hint is a label, "
    "the value following it on the same line is captured (hint: value / "
    "hint - value / hint value, or a value glued to the hint as "
    "hintvalue). "
    "'scope' narrows the search to the paragraph containing it. URLs are "
    "stripped before matching unless raw=true."
)

_RAW_PARAM_DESCRIPTION = "Keep URLs and id/class attribute assignments in the searched text instead of stripping them (needed to assert on a link target or attribute value)."


def _minified(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _string_values(node: Any, found: List[str]) -> None:
    """Collect every string VALUE of a JSON-ish tree (keys are not text)."""
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            _string_values(value, found)
    elif isinstance(node, list):
        for item in node:
            _string_values(item, found)


def _join_string_values(node: Any) -> str:
    """Render a JSON-ish tree as newline-joined string values (keys excluded)."""
    values: List[str] = []
    _string_values(node, values)
    return "\n".join(values)


def _payload_to_text(payload: Any) -> str:
    """
    Normalize a page-read payload into searchable text.

    A JSON payload — passed as a JSON string or already parsed into a
    dict/list — contributes only its string values (keys, numbers and
    structure never satisfy a text assertion); a scalar payload keeps its
    text form. A missing (None) or JSON-null payload normalizes to empty so
    it hits the blank-payload ERROR instead of becoming matchable text.
    """
    if payload is None:
        return ""
    if isinstance(payload, (str, bytes, bytearray)):
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, (bytes, bytearray)) else payload
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return text
        if isinstance(parsed, (dict, list)):
            return _join_string_values(parsed)
        if parsed is None:
            return ""
        return parsed if isinstance(parsed, str) else stringify_interpolated_value(parsed)
    if isinstance(payload, (dict, list)):
        return _join_string_values(payload)
    return stringify_interpolated_value(payload)


def _strip_noise(text: str) -> str:
    """Remove URLs and id/test-id/class-like attribute assignments."""
    text = _URL_TOKEN_RE.sub(" ", text)
    return _ATTR_ASSIGNMENT_RE.sub(" ", text)


def _prepare_search_text(payload: Any, raw: bool = False) -> str:
    """Normalize a payload and strip non-visible noise from it (raw=True keeps URLs and attribute assignments)."""
    text = _payload_to_text(payload)
    return (text if raw else _strip_noise(text)).strip()


def _scope_window(text: str, scope: str) -> Optional[str]:
    """
    Narrow matching to the paragraph containing the first scope occurrence.

    A paragraph is a block of consecutive non-empty lines — the smallest
    predictable region that keeps a label and its value together.
    """
    position = text.find(scope)
    if position < 0:
        return None
    lines = text.splitlines()
    if not lines:  # pragma: no cover - a non-empty text always splits
        return text
    # Find the line holding the first occurrence.
    offset = 0
    hit = len(lines) - 1
    for index, line in enumerate(lines):
        if position < offset + len(line) + 1:
            hit = index
            break
        offset += len(line) + 1
    start = hit
    while start > 0 and lines[start - 1].strip():
        start -= 1
    end = hit
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    return "\n".join(lines[start : end + 1])


def _interpolate_param(value: Any, param_name: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve ${name} in a string parameter.

    Returns (text, error_reason). An unresolved placeholder is an error: like
    the API side, a literal placeholder would make contains always fail and
    absent always pass, so it is surfaced instead of evaluated.
    """
    try:
        value = interpolate_value(value)
    except Exception as e:  # pragma: no cover - interpolation is defensive
        return None, f"Interpolation of '{param_name}' failed: {type(e).__name__}: {e}"
    text = value if isinstance(value, str) else stringify_interpolated_value(value)
    unresolved = _UNRESOLVED_PLACEHOLDER_RE.search(text)
    if unresolved:
        return None, (
            f"Parameter '{param_name}' still contains the unresolved placeholder "
            f"${{{unresolved.group(1).strip()}}}. Capture it first (capture_ui_value / "
            "extract_from_response) before asserting on it."
        )
    return text, None


def _record(source: str, search_text: str, result: Dict[str, Any]) -> None:
    """Append the (redacted) verdict to the audit ledger, tagged with the text fingerprint."""
    entry: Dict[str, Any] = {
        "source": source,
        "test_id": get_global_conf().get_default_test_id(),
        "subject": assertion_subject(search_text),
        "verdict": result.get("assertion"),
    }
    # A secret-named capture/hint, or the value of a secret-named captured
    # variable, never lands in the audit file.
    key_hint = f"{result.get('as_name') or ''} {result.get('path_hint') or ''}"
    secrets = sensitive_variable_values()
    for key in ("mode", "expected", "actual", "as_name", "value", "scope", "reason", "note"):
        # Keep every documented field that exists — falsy values (an actual
        # count of 0, an empty reason) are still facts worth recording.
        if key in result:
            entry[key] = redact_value(result[key], key_hint if key in ("expected", "actual", "value") else "", secrets)
    try:
        assertion_logger(json.dumps(entry, separators=(",", ":"), default=str))
    except Exception as e:
        # The ledger must never turn a verdict into a crash.
        logger.warning(f"Failed to append to the assertion ledger: {e}")


def _placeholder_findings(text: str) -> List[Dict[str, str]]:
    """Scan normalized text for placeholder shapes; returns {kind, sample} entries."""
    findings: List[Dict[str, str]] = []

    def _add(kind: str, match: "re.Match[str]") -> None:
        sample = match.group(0)
        if len(sample) > 120:
            sample = sample[:120] + "..."
        findings.append({"kind": kind, "sample": sample})

    for match in _UNDEFINED_TOKEN_RE.finditer(text):
        _add("undefined_token", match)
    for match in _NAN_TOKEN_RE.finditer(text):
        _add("nan_token", match)
    for match in re.finditer(r"\[object Object\]", text):
        _add("object_object", match)
    for match in _TEMPLATE_RE.finditer(text):
        _add("unsubstituted_template", match)
    for match in _MARKDOWN_HEADER_RE.finditer(text):
        _add("raw_markdown_header", match)
    for match in _I18N_KEY_RE.finditer(text):
        if _FILE_NAME_EXT_RE.search(match.group(0)):
            continue
        _add("i18n_key", match)
    return findings


async def assert_ui(
    payload: Annotated[Any, "The text/payload returned by a page-reading tool (get_page_text-style)."],
    mode: Annotated[str, f"Assertion mode. One of: {', '.join(MODES)}."],
    expected: Annotated[Optional[str], "Text expected to be visible (contains, count_eq) or the text that must be absent (absent, when 'forbidden' is not used)."] = None,
    count: Annotated[Optional[int], "Exact number of occurrences required by the count_eq mode."] = None,
    scope: Annotated[Optional[str], "Optional substring narrowing the check to the paragraph containing it."] = None,
    forbidden: Annotated[Optional[str], "Text that must NOT be visible (absent mode). Falls back to 'expected' when omitted."] = None,
    must_contain: Annotated[Optional[str], "Positive control required by the absent mode: text that must be visible, proving the page rendered."] = None,
    raw: Annotated[bool, _RAW_PARAM_DESCRIPTION] = False,
) -> Annotated[str, "Minified JSON verdict: {assertion: PASSED|FAILED|ERROR, mode, expected, actual, ...}."]:
    add_event(EventType.INTERACTION, EventData(detail="assert_ui"))
    try:
        result = _evaluate_assert_ui(payload, mode, expected, count, scope, forbidden, must_contain, raw)
    except Exception as e:
        logger.exception("assert_ui failed unexpectedly")
        result = {"assertion": "ERROR", "mode": mode, "expected": expected, "reason": f"Assertion could not be evaluated: {type(e).__name__}: {e}"}
    search_text = result.pop("_search_text", "") or ""
    _record("assert_ui", search_text, result)
    return _minified(result)


def _evaluate_assert_ui(payload: Any, mode: str, expected: Any, count: Any, scope: Any, forbidden: Any, must_contain: Any, raw: bool = False) -> Dict[str, Any]:
    """Run one UI assertion and build its verdict payload."""
    mode_name = str(mode or "").strip().lower()

    def _payload(**overrides: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {"mode": mode_name, "expected": expected, "actual": None}
        base.update(overrides)
        return base

    # ---- input judgment rules (run before anything can look green) --------
    if mode_name not in MODES:
        return _payload(assertion="ERROR", reason=f"Unknown mode '{mode_name}'. Valid modes: {', '.join(MODES)}.")

    search_text = _prepare_search_text(payload, bool(raw))
    if not search_text:
        return _payload(
            assertion="ERROR",
            _search_text=search_text,
            reason="The page payload is empty or blank. Re-read the page (get_page_text) before asserting on it.",
        )

    scoped_text = search_text
    if scope is not None and str(scope).strip():
        scope_text, error = _interpolate_param(scope, "scope")
        if error:
            return _payload(assertion="ERROR", _search_text=search_text, reason=error)
        window = _scope_window(search_text, scope_text or "")
        if window is None:
            return _payload(
                assertion="ERROR",
                _search_text=search_text,
                reason=f"Scope {scope_text!r} was not found in the payload; nothing was narrowed.",
            )
        scoped_text = window

    def _resolve(param_name: str, raw: Any) -> Tuple[Optional[str], Optional[str]]:
        if raw is None:
            return None, None
        return _interpolate_param(raw, param_name)

    if mode_name == "contains":
        if expected is None or not str(expected).strip():
            return _payload(assertion="ERROR", _search_text=search_text, reason="The 'contains' mode requires non-blank 'expected' text.")
        expected_text, error = _resolve("expected", expected)
        if error:
            return _payload(assertion="ERROR", _search_text=search_text, reason=error)
        passed = expected_text in scoped_text
        return _payload(
            assertion="PASSED" if passed else "FAILED",
            _search_text=search_text,
            expected=expected_text,
            actual="visible" if passed else "not visible",
            scope=scope,
        )

    if mode_name == "absent":
        absent_raw = forbidden if forbidden is not None else expected
        if absent_raw is None or not str(absent_raw).strip():
            return _payload(
                assertion="ERROR",
                _search_text=search_text,
                reason="The 'absent' mode requires the text to check via 'forbidden' (or 'expected').",
            )
        if must_contain is None or not str(must_contain).strip():
            return _payload(
                assertion="ERROR",
                _search_text=search_text,
                reason=(
                    "The 'absent' mode requires 'must_contain' as a positive control proving the page "
                    "actually rendered — without it, absent passes trivially on an unrendered page. "
                    "Pass a string that must be visible on the rendered page."
                ),
            )
        absent_text, error = _resolve("forbidden", absent_raw)
        if error:
            return _payload(assertion="ERROR", _search_text=search_text, reason=error)
        control_text, error = _resolve("must_contain", must_contain)
        if error:
            return _payload(assertion="ERROR", _search_text=search_text, reason=error)
        if control_text not in scoped_text:
            return _payload(
                assertion="FAILED",
                _search_text=search_text,
                expected=absent_text,
                actual="positive control not visible",
                scope=scope,
                reason=(f"Positive control {control_text!r} is not visible, so the page did not render as " "expected and the absence check is meaningless here."),
            )
        passed = absent_text not in scoped_text
        return _payload(
            assertion="PASSED" if passed else "FAILED",
            _search_text=search_text,
            expected=absent_text,
            actual="absent" if passed else "visible",
            scope=scope,
        )

    if mode_name == "count_eq":
        if expected is None or not str(expected).strip():
            return _payload(assertion="ERROR", _search_text=search_text, reason="The 'count_eq' mode requires non-blank 'expected' text.")
        if count is None:
            return _payload(assertion="ERROR", _search_text=search_text, reason="The 'count_eq' mode requires a numeric 'count'.")
        try:
            expected_count = int(str(count).strip())
        except (TypeError, ValueError):
            return _payload(assertion="ERROR", _search_text=search_text, reason=f"Count {count!r} is not a number.")
        if expected_count < 0:
            return _payload(assertion="ERROR", _search_text=search_text, reason=f"Count {expected_count} is negative; counts cannot be negative.")
        expected_text, error = _resolve("expected", expected)
        if error:
            return _payload(assertion="ERROR", _search_text=search_text, reason=error)
        actual_count = scoped_text.count(expected_text or "")
        return _payload(
            assertion="PASSED" if actual_count == expected_count else "FAILED",
            _search_text=search_text,
            expected=expected_text,
            actual=actual_count,
            expected_count=expected_count,
            scope=scope,
        )

    # no_placeholder
    note = None
    if expected is not None:
        note = "The 'no_placeholder' mode ignores 'expected'; it scans the whole (scoped) text for placeholder shapes."
    findings = _placeholder_findings(scoped_text)
    result = _payload(
        assertion="PASSED" if not findings else "FAILED",
        _search_text=search_text,
        actual=findings,
        finding_count=len(findings),
        scope=scope,
    )
    if note:
        result["note"] = note
    if findings:
        samples = "; ".join(f"{finding['kind']}:{finding['sample']!r}" for finding in findings[:3])
        result["reason"] = f"Rendered text contains unrendered placeholder shapes: {samples}."
    return result


async def capture_ui_value(
    payload: Annotated[Any, "The text/payload returned by a page-reading tool (get_page_text-style)."],
    path_hint: Annotated[str, "Label or exact value to locate; the hint itself is captured when it already is the value."],
    as_name: Annotated[str, "Name to store the value under; reference it later as ${as_name}."],
    scope: Annotated[Optional[str], "Optional substring narrowing the search to the paragraph containing it."] = None,
    raw: Annotated[bool, _RAW_PARAM_DESCRIPTION] = False,
) -> Annotated[str, "Minified JSON with the capture verdict, the stored value and its ${as_name} reference."]:
    add_event(EventType.INTERACTION, EventData(detail="capture_ui_value"))
    try:
        result = _evaluate_capture_ui_value(payload, path_hint, as_name, scope, raw)
    except Exception as e:
        logger.exception("capture_ui_value failed unexpectedly")
        result = {"assertion": "ERROR", "path_hint": path_hint, "as_name": as_name, "reason": f"Capture could not be evaluated: {type(e).__name__}: {e}"}
    search_text = result.pop("_search_text", "") or ""
    _record("capture_ui_value", search_text, result)
    return _minified(result)


def _evaluate_capture_ui_value(payload: Any, path_hint: Any, as_name: Any, scope: Any, raw: bool = False) -> Dict[str, Any]:
    """Locate the value for one capture and store it in the variable store."""

    def _payload(**overrides: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {"path_hint": path_hint, "as_name": as_name, "scope": scope}
        base.update(overrides)
        return base

    hint = str(path_hint or "").strip()
    name = str(as_name or "").strip()
    if not hint:
        return _payload(assertion="ERROR", reason="A non-blank 'path_hint' is required.")
    if not name:
        return _payload(assertion="ERROR", reason="A non-blank 'as_name' is required.")

    search_text = _prepare_search_text(payload, bool(raw))
    if not search_text:
        return _payload(
            assertion="ERROR",
            _search_text=search_text,
            reason="The page payload is empty or blank. Re-read the page (get_page_text) before capturing from it.",
        )

    searched = search_text
    if scope is not None and str(scope).strip():
        scope_text, error = _interpolate_param(scope, "scope")
        if error:
            return _payload(assertion="ERROR", _search_text=search_text, reason=error)
        window = _scope_window(search_text, scope_text or "")
        if window is None:
            return _payload(
                assertion="ERROR",
                _search_text=search_text,
                reason=f"Scope {scope_text!r} was not found in the payload; nothing was captured.",
            )
        searched = window

    # Capture strategies, tried in order:
    # 1. exact: the hint equals a whole line, or is found verbatim in the text
    #    with no value token following it — the hint itself IS the value
    #    (e.g. a known order id).
    # 2. adjacent: the hint acts as a label; the value is the rest of its line
    #    ("Order total: 42.50", "Order total - 42.50", "Order total 42.50";
    #    the colon-free variant keeps only the remainder of that line). A hint
    #    glued to a longer token ("Total42") reads as a label too: the value
    #    is the remainder of that line ("42"), never the label itself.
    # The adjacent pass runs before the loose verbatim capture so a hint that
    # happens to be a label ("Carrier: Pony Express") captures "Pony Express",
    # not the label itself.
    value: Optional[str] = None
    strategy: Optional[str] = None
    adjacent_pattern = re.compile(re.escape(hint) + r"[ \t]*[:\-\u2013\u2014][ \t]*([^\n]+)|" + re.escape(hint) + r"[ \t]+([^\n:]+)")
    lines = [line.strip() for line in searched.splitlines()]
    if hint in lines:
        value = hint
        strategy = "exact"
    else:
        match = adjacent_pattern.search(searched)
        if match:
            candidate = (match.group(1) or match.group(2) or "").strip().strip("\"'.,;:")
            if candidate:
                value = candidate
                strategy = "adjacent"
        elif hint in searched:
            after = searched[searched.find(hint) + len(hint) :]
            if after[:1] and (after[:1].isalnum() or after[:1] == "_"):
                # The hint is glued to a longer token ("Total42", "Total_42"):
                # the hint reads as a label, so capture the rest of that line
                # as the value instead of mistaking the label for the value
                # (the glue character is stripped from the captured value).
                candidate = after.split("\n", 1)[0].strip().strip("\"'.,;:_")
                if candidate:
                    value = candidate
                    strategy = "adjacent"
            else:
                value = hint
                strategy = "exact"
    if not value or not strategy:
        where = f" within the scope {scope!r}" if scope else ""
        return _payload(
            assertion="ERROR",
            _search_text=search_text,
            reason=(f"Neither the value {hint!r} nor a value labeled by it was found{where}. " "Searched the visible text of the page payload only."),
        )

    set_variable_value(name, value)
    return _payload(
        assertion="PASSED",
        _search_text=search_text,
        value=value,
        reference=f"${{{name}}}",
        strategy=strategy,
    )


# ------------------------------------------------------------------------------
# Opt-in registration (ENABLE_UI_ASSERTIONS) and prompt guidance
# ------------------------------------------------------------------------------

_PROMPT_ADDENDUM = """

## Deterministic UI assertions
- Never decide pass/fail on visible page text by reading it yourself. Read the page with the page-text tool, then call `assert_ui` with that payload and use its returned `assertion` value (PASSED/FAILED/ERROR) as the verdict.
- Use `capture_ui_value` to store a value seen on the page as `${name}` for later steps or API cross-checks.
"""


def ui_assertions_enabled() -> bool:
    """True when ENABLE_UI_ASSERTIONS opts the run into the assert_ui / capture_ui_value tools."""
    return get_global_conf().get_enable_ui_assertions()


def register_ui_assertion_tools() -> None:
    """Register assert_ui / capture_ui_value on the browser, MCP and mobile nav agents."""
    tool(agent_names=UI_ASSERTION_AGENTS, name="assert_ui", description=_ASSERT_UI_DESCRIPTION)(assert_ui)
    tool(agent_names=UI_ASSERTION_AGENTS, name="capture_ui_value", description=_CAPTURE_UI_VALUE_DESCRIPTION)(capture_ui_value)


def prompt_addendum_for(agent_name: str) -> str:
    """Prompt guidance for `agent_name`: non-empty only when assert_ui is actually registered for it."""
    if any(entry.get("name") == "assert_ui" for entry in tool_registry.get(agent_name, [])):
        return _PROMPT_ADDENDUM
    return ""


if ui_assertions_enabled():
    register_ui_assertion_tools()
