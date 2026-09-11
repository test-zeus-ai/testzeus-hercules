"""Heuristic failure classification for failed testcases.

Maps failure evidence (failure message, final response, network logs) onto one
of five categories, evaluated in priority order (infra issues mask everything
else):

1. ``environment`` -- infra problems (connection refused, DNS, browser missing).
2. ``contract_violation`` -- HTTP 4xx/5xx statuses, schema/validation errors.
3. ``test_fragility`` -- selector rot, visibility issues, flakiness.
4. ``product_bug`` -- assertions that failed while the app was otherwise healthy.
5. ``unclassified`` -- no known signal matched.

Confidence is the share of matched signals within the winning category,
clamped to ``[MIN_CONFIDENCE, MAX_CONFIDENCE]``; ``unclassified`` (no signal
matched) reports the fixed low ``UNCLASSIFIED_CONFIDENCE`` instead, since it is
a guess rather than a scored match.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Callable

from testzeus_hercules.utils.logger import logger

CATEGORY_ENVIRONMENT = "environment"
CATEGORY_CONTRACT_VIOLATION = "contract_violation"
CATEGORY_TEST_FRAGILITY = "test_fragility"
CATEGORY_PRODUCT_BUG = "product_bug"
CATEGORY_UNCLASSIFIED = "unclassified"

# Priority order when several categories have matching signals.
CATEGORY_PRIORITY = (CATEGORY_ENVIRONMENT, CATEGORY_CONTRACT_VIOLATION, CATEGORY_TEST_FRAGILITY, CATEGORY_PRODUCT_BUG)

MIN_CONFIDENCE = 0.3
MAX_CONFIDENCE = 0.95
# "No signal matched" (unclassified) is a pure guess: report it distinctly below
# the matched-signal floor so the two are not confusable at 0.30.
UNCLASSIFIED_CONFIDENCE = 0.10

# How many trailing lines of the network log are inspected for boosting.
NETWORK_TAIL_LINES = 50

# Regex matching runs over the combined evidence text; huge failure dumps are
# reduced to head+tail before matching so a multi-MB log cannot stall it.
EVIDENCE_HEAD_LIMIT = 20 * 1024
EVIDENCE_TAIL_LIMIT = 20 * 1024
_EVIDENCE_ELISION_MARKER = "\n...[evidence truncated]...\n"

EventCallback = Callable[[dict], None]

# ---------------------------------------------------------------------------
# Signal patterns (case-insensitive), grouped per category.
# ---------------------------------------------------------------------------

# A bare timeout is infra; a timeout explicitly waiting for an element/selector
# is fragility, so the environment pattern excludes those contexts. The second
# lookahead also excludes kwarg/call-site occurrences ("timeout=timeout",
# "query(q, timeout=30)"), which name a parameter, not a failure reason.
_ENVIRONMENT_TIMEOUT_RE = re.compile(r"\btimeouts?\b(?!\s+waiting\s+for\s+(?:the\s+)?(?:element|selector|locator))(?![\s]*[=,):])|\btimed\s+out\b", re.IGNORECASE)

# Real Playwright timeout messages put the wait clause on a later line ("Timeout
# 30000ms exceeded.\nwaiting for locator('#submit') to be visible"), which the
# environment pattern's negative lookahead (text right after the word "timeout")
# cannot see. This broader check runs on the whole evidence text instead. The
# locator branch also tolerates a selector expression between "locator(" and
# "to be" (e.g. "locator('button.submit') to be visible") and matches the
# underscore form used by expect() assertions ("to_be_visible").
_WAIT_FOR_ELEMENT_RE = re.compile(
    r"waiting\s+for\s+(?:the\s+)?(?:element|selector|locator|get_by|frame)" r"|locator\s*\([^)]*\)\s+to\s+be" r"|\bto[_\s]+be[_\s]+visible\b",
    re.IGNORECASE,
)

# expect()-assertion waits ("waiting for expect(locator).to_have_url(...)",
# "Expect: to_have_text") are assertion failures, not selector fragility: when
# they strip the environment timeout below, the fragility signal must NOT be
# minted so the already-matched assertion_failed signal can win for product_bug.
_ASSERTION_WAIT_RE = re.compile(r"waiting\s+for\s+expect\s*\(|\bExpect:\s*to_(?:have|contain|be)_", re.IGNORECASE)

ENVIRONMENT_PATTERNS = {
    "connection_refused": re.compile(r"connection[\s_-]*refused|econnrefused", re.IGNORECASE),
    "timeout": _ENVIRONMENT_TIMEOUT_RE,
    "net_err": re.compile(r"net::err_", re.IGNORECASE),
    "name_resolution": re.compile(r"name[\s_-]+resolution|name[\s_-]+not[\s_-]+resolved|getaddrinfo|\bdns\b", re.IGNORECASE),
    "proxy": re.compile(r"\bproxy\b", re.IGNORECASE),
    "ssl_certificate": re.compile(r"\bssl\b|certificate\s+verify\s+failed|err_cert", re.IGNORECASE),
    "browser_missing": re.compile(r"executable\s+doesn'?t\s+exist|playwright\s+install|browser\s+(?:is\s+)?missing|failed\s+to\s+launch", re.IGNORECASE),
    # Browser/tab crash or abrupt teardown ("Target page, context or browser has
    # been closed", "Target crashed", SIGSEGV) is infra, distinct from a missing
    # browser install (browser_missing above) and from click timeouts.
    "browser_crash": re.compile(
        r"target\s+(?:page|context|browser)[^.\n]{0,30}closed|target\s+closed|browser\s+(?:has\s+been\s+)?closed(?:\s+unexpectedly)?|target\s+crashed|page\s+crashed|targetclosederror|browserclosederror|browser\s+disconnected",
        re.IGNORECASE,
    ),
    "navigation_interrupted": re.compile(r"navigation\s+(?:interrupted|aborted|failed)", re.IGNORECASE),
}

CONTRACT_PATTERNS = {
    "http_status_code": re.compile(r"\b(?:400|401|403|404|409|422|500|502|503)\b"),
    "status_code_mention": re.compile(r"status\s+code|expected\s+status|http\s+status", re.IGNORECASE),
    "schema_validation": re.compile(r"schema|validation\s+error|failed\s+(?:schema\s+)?validation|does\s+not\s+conform", re.IGNORECASE),
    "json_decode": re.compile(r"json\s+decode\s+error|jsondecodeerror|invalid\s+json|expecting\s+(?:value|property)", re.IGNORECASE),
}

FRAGILITY_PATTERNS = {
    "element_not_found": re.compile(
        r"element\s+(?:was\s+)?not\s+(?:found|located)|element\s+does\s+not\s+exist|no\s+element\s+matches|no\s+such\s+element|unable\s+to\s+locate\s+element", re.IGNORECASE
    ),
    "strict_mode_violation": re.compile(r"strict\s+mode\s+violation", re.IGNORECASE),
    "not_visible": re.compile(r"(?:element\s+)?is\s+not\s+visible|not\s+visible|outside\s+of\s+the\s+viewport", re.IGNORECASE),
    "intercepts_pointer_events": re.compile(r"intercepts\s+pointer\s+events|subtree\s+intercepts|receives\s+pointer\s+events", re.IGNORECASE),
    "timeout_waiting_for_element": re.compile(r"timeout\s+waiting\s+for\s+(?:the\s+)?(?:element|selector|locator)|waiting\s+for\s+(?:the\s+)?(?:selector|locator)", re.IGNORECASE),
    "selector_md_not_found": re.compile(r"(?:selector|locator)\s+(?:was\s+)?not\s+found|md\s+(?:id|attribute)\s+not\s+found|md=\d+[^\n]*not\s+found|unknown\s+selector", re.IGNORECASE),
    "stale_element": re.compile(r"stale\s+element|not\s+attached\s+to\s+the\s+dom|detached\s+from\s+the\s+dom", re.IGNORECASE),
    "flaky_language": re.compile(r"\bsometimes\b|\bintermittent(?:ly)?\b|\bflak(?:y|iness)\b|race\s+condition", re.IGNORECASE),
}

PRODUCT_PATTERNS = {
    "expected_but_found": re.compile(r"expected\b[^.\n]{0,200}?\bbut\b", re.IGNORECASE),
    "assertion_failed": re.compile(r"assert(?:ion)?\s+failed|assertionerror|expect\s*\(", re.IGNORECASE),
    "unexpected_error_message": re.compile(r"unexpected\s+error\s+message|error\s+message\s+(?:is|was)\s+shown|unexpected\s+error\s+(?:is|was)\s+shown", re.IGNORECASE),
    "error_banner": re.compile(r"error\s+banner|banner\s+(?:shows?|displaying|displayed)", re.IGNORECASE),
    "wrong_redirect": re.compile(r"wrong\s+redirect|redirected?\s+to\s+(?:the\s+)?(?:wrong|incorrect)|did\s+not\s+redirect|unexpected\s+redirect", re.IGNORECASE),
    "data_not_saved": re.compile(r"data\s+(?:was\s+)?not\s+saved|not\s+persisted|record\s+(?:was\s+)?not\s+(?:created|saved)|changes\s+(?:were\s+)?lost", re.IGNORECASE),
}

ALL_PATTERNS = {
    CATEGORY_ENVIRONMENT: ENVIRONMENT_PATTERNS,
    CATEGORY_CONTRACT_VIOLATION: CONTRACT_PATTERNS,
    CATEGORY_TEST_FRAGILITY: FRAGILITY_PATTERNS,
    CATEGORY_PRODUCT_BUG: PRODUCT_PATTERNS,
}

# The page clearly rendered while the element went missing -> fragility, not env.
_PAGE_LOADED_RE = re.compile(r"page\s+(?:is\s+|was\s+)?(?:loaded|rendered|open)|on\s+the\s+(?:loaded\s+)?page", re.IGNORECASE)

_NETWORK_STATUS_RE = re.compile(r'"status"\s*:\s*(\d+)')


@dataclass
class FailureClassification:
    """Result of classifying one failed testcase."""

    category: str
    confidence: float
    rationale: str
    signals: list[str] = field(default_factory=list)


def _match_category(text: str, patterns: dict) -> list[str]:
    """Return the names of all signals whose pattern matches text."""
    return [name for name, pattern in patterns.items() if pattern.search(text)]


def _head_tail(text: str, head_limit: int = EVIDENCE_HEAD_LIMIT, tail_limit: int = EVIDENCE_TAIL_LIMIT) -> str:
    """Keep the head and tail of huge evidence text, eliding the middle.

    Signals rarely hide exclusively in the middle of a multi-megabyte dump, and
    unbounded regex matching over such text is slow; anything that fits within
    the limits passes through untouched.
    """
    if len(text) <= head_limit + tail_limit + len(_EVIDENCE_ELISION_MARKER):
        return text
    return text[:head_limit] + _EVIDENCE_ELISION_MARKER + text[-tail_limit:]


def _build_classification(matches: dict) -> FailureClassification:
    """Pick the winning category by priority and score its confidence."""
    winner = next((category for category in CATEGORY_PRIORITY if matches.get(category)), None)
    if winner is None:
        return FailureClassification(
            category=CATEGORY_UNCLASSIFIED,
            confidence=UNCLASSIFIED_CONFIDENCE,
            rationale="No known failure signals matched the provided evidence.",
            signals=[],
        )

    matched = matches[winner]
    total = len(ALL_PATTERNS[winner]) + (1 if "element_not_found_page_loaded" in matched else 0)
    raw_score = len(matched) / total if total else 0.0
    confidence = min(MAX_CONFIDENCE, max(MIN_CONFIDENCE, raw_score))
    return FailureClassification(
        category=winner,
        confidence=confidence,
        rationale=f"{len(matched)} {winner} signal(s) matched: {', '.join(matched)}.",
        signals=list(matched),
    )


def _read_tail_lines(network_log_path: str, limit: int = NETWORK_TAIL_LINES) -> list[str]:
    """Read the last ``limit`` lines of a file; never raises."""
    try:
        with open(network_log_path, "r", encoding="utf-8", errors="replace") as log_file:
            lines = log_file.readlines()
        return lines[-limit:]
    except OSError as exc:
        logger.warning("Could not read network log %s: %s", network_log_path, exc)
        return []


def _network_signals(network_log_path: str) -> dict:
    """Derive extra classification signals from a network log (JSON lines).

    Many 5xx/4xx responses boost contract_violation; a log where no request
    completed successfully (and none got an HTTP status back) boosts
    environment.
    """
    total = success = client_errors = server_errors = 0
    for line in _read_tail_lines(network_log_path):
        status_match = _NETWORK_STATUS_RE.search(line)
        if not status_match:
            continue
        try:
            status = int(status_match.group(1))
        except ValueError:
            continue
        total += 1
        if 200 <= status < 400:
            success += 1
        elif 400 <= status < 500:
            client_errors += 1
        elif 500 <= status < 600:
            server_errors += 1

    signals: dict = {}
    if server_errors:
        signals[CATEGORY_CONTRACT_VIOLATION] = ["network_5xx_responses"]
    # A single stray 4xx in an otherwise healthy tail (favicon 404, analytics
    # 429, third-party widgets) is noise, not a contract breach: only report the
    # 4xx boost when 4xx responses dominate the tail or real 5xx also occurred.
    if server_errors or client_errors * 3 >= total:
        signals.setdefault(CATEGORY_CONTRACT_VIOLATION, []).append("network_4xx_responses")
    if total and not success and not client_errors and not server_errors:
        signals[CATEGORY_ENVIRONMENT] = ["network_no_successful_requests"]
    if signals:
        logger.info("Network log %s boosted signals: %s", network_log_path, signals)
    return signals


def classify_failure(
    failure_message: str = "",
    final_response: str = "",
    assert_summary: str = "",
    network_log_path: str | None = None,
    planner_thoughts: str = "",
) -> FailureClassification:
    """Classify a failure from raw evidence strings using heuristic signals."""
    # Evidence fields come from parsed junit dicts and may hold non-strings
    # (e.g. int status codes); coerce so the join below cannot raise.
    text = _head_tail(" \n ".join(str(part) for part in (failure_message, final_response, assert_summary, planner_thoughts) if part))
    matches = {category: _match_category(text, patterns) for category, patterns in ALL_PATTERNS.items()}

    # A timeout that is really "waiting for locator(...) to be visible" is
    # selector fragility, not infra: drop the plain environment signal before
    # the priority order can mask the fragility signal with it, and record the
    # fragility signal explicitly so expect()-phrased visibility timeouts
    # classify as fragility rather than as assertion product bugs. expect()-
    # assertion waits (to_have_text/to_have_url/to_contain_text/to_be_enabled)
    # are the opposite: strip the environment timeout but let the already
    # matched assertion_failed signal win for product_bug.
    if "timeout_waiting_for_element" in matches[CATEGORY_TEST_FRAGILITY] or _WAIT_FOR_ELEMENT_RE.search(text) or _ASSERTION_WAIT_RE.search(text):
        if "timeout" in matches[CATEGORY_ENVIRONMENT]:
            matches[CATEGORY_ENVIRONMENT].remove("timeout")
            if "timeout_waiting_for_element" not in matches[CATEGORY_TEST_FRAGILITY] and not _ASSERTION_WAIT_RE.search(text):
                matches[CATEGORY_TEST_FRAGILITY].append("timeout_waiting_for_element")

    # "Element not found" only means fragility when the page itself rendered.
    if "element_not_found" in matches[CATEGORY_TEST_FRAGILITY] and _PAGE_LOADED_RE.search(str(final_response or "")):
        matches[CATEGORY_TEST_FRAGILITY].append("element_not_found_page_loaded")

    if network_log_path:
        for category, signal_names in _network_signals(network_log_path).items():
            matches[category].extend(name for name in signal_names if name not in matches[category])

    return _build_classification(matches)


def _resolve_network_log_path(value: str, proofs_dir: str | None) -> str | None:
    """Resolve the Network Logs property to an existing file, best effort."""
    if value and os.path.isfile(value):
        return value
    if proofs_dir and os.path.isdir(proofs_dir):
        candidates = [os.path.join(proofs_dir, "network_logs.json")]
        if value:
            candidates.insert(0, os.path.join(proofs_dir, os.path.basename(value)))
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
    return value or None


def _emit_safely(on_event: EventCallback | None, event: dict) -> None:
    """Invoke the optional on_event hook; a broken callback never raises."""
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception as exc:
        logger.warning("on_event callback failed: %s", exc)


async def classify_failure_with_context(case: dict, proofs_dir: str | None = None, on_event: EventCallback | None = None) -> FailureClassification:
    """Classify one ``parse_junit_testcases`` dict, boosting with its network log.

    The network log path comes from the case properties ("Network Logs") and
    only the tail of the file is read. Async is kept for API stability; the
    body is sync.
    """
    properties = case.get("properties") or {}
    network_log_path = _resolve_network_log_path(properties.get("Network Logs", "") or "", proofs_dir)
    classification = classify_failure(
        failure_message=case.get("failure_message") or "",
        final_response=case.get("final_response") or case.get("system_out") or "",
        assert_summary=case.get("assert_summary") or properties.get("Assert Summary", "") or "",
        network_log_path=network_log_path,
        planner_thoughts=case.get("planner_thoughts") or properties.get("Planner Thoughts", "") or "",
    )
    _emit_safely(
        on_event,
        {
            "kind": "failure_classified",
            "canonical_name": case.get("name") or "",
            "detail": f"{classification.category} (confidence={classification.confidence:.2f})",
            "status": "ok",
        },
    )
    return classification


def classify_case(case: dict) -> FailureClassification:
    """Sync, pure wrapper: classify using only the case dict fields (no files)."""
    return classify_failure(
        failure_message=case.get("failure_message") or "",
        final_response=case.get("final_response") or case.get("system_out") or "",
        assert_summary=case.get("assert_summary") or (case.get("properties") or {}).get("Assert Summary", "") or "",
        network_log_path=None,
        planner_thoughts=case.get("planner_thoughts") or (case.get("properties") or {}).get("Planner Thoughts", "") or "",
    )


def format_classification_markdown(c: FailureClassification) -> str:
    """Render a classification as a short markdown block."""
    signals = ", ".join(f"`{signal}`" for signal in c.signals) if c.signals else "(none)"
    return "\n".join(
        [
            f"### Failure classification: `{c.category}`",
            f"- **Confidence:** {c.confidence:.2f}",
            f"- **Rationale:** {c.rationale}",
            f"- **Signals:** {signals}",
        ]
    )
