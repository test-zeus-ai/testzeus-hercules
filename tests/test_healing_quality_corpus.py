"""Quality-corpus regression tests for failure classification.

Encodes representative entries of a realistic 40-entry corpus audit: the four
systematic misclassification classes fixed during hardening (expect()-assertion
timeouts, browser crash/lifecycle failures, stray-4xx network tails, bare
kwarg timeouts in tracebacks, selenium element lookups, unclassified confidence)
plus already-correct controls. Every row must classify exactly as the audit
validated -- the table is the accuracy bar for the classifier.
"""

import asyncio
import json
from typing import NamedTuple

import pytest
from testzeus_hercules.healing import (
    CATEGORY_CONTRACT_VIOLATION,
    CATEGORY_ENVIRONMENT,
    CATEGORY_PRODUCT_BUG,
    CATEGORY_TEST_FRAGILITY,
    CATEGORY_UNCLASSIFIED,
)
from testzeus_hercules.healing.failure_classifier import (
    UNCLASSIFIED_CONFIDENCE,
    classify_failure_with_context,
)


class CorpusEntry(NamedTuple):
    """One corpus row: evidence plus the validated classification."""

    id: str
    expected_category: str
    failure_message: str = ""
    final_response: str = ""
    assert_summary: str = ""
    network_requests: tuple[tuple[str, int], ...] = ()  # (url, status) pairs fed as JSON-line network log
    required_signals: tuple[str, ...] = ()
    forbidden_signals: tuple[str, ...] = ()
    expected_confidence: float | None = None


CORPUS = (
    # --- systematic class 1: expect()-assertion timeouts are product bugs, not environment ---
    CorpusEntry(
        "expect_to_have_url_timeout_is_product_bug",
        CATEGORY_PRODUCT_BUG,
        failure_message="TimeoutError: Timeout 30000ms exceeded.\nwaiting for expect(locator).to_have_url('https://app.test/dashboard')\n\nExpect: to_have_url",
        required_signals=("assertion_failed",),
        forbidden_signals=("timeout",),
    ),
    CorpusEntry(
        "expect_to_have_text_timeout_is_product_bug",
        CATEGORY_PRODUCT_BUG,
        failure_message="TimeoutError: Timeout 30000ms exceeded.\nwaiting for expect(locator).to_have_text('Welcome back')\n\nExpect: to_have_text",
        required_signals=("assertion_failed",),
        forbidden_signals=("timeout",),
    ),
    CorpusEntry(
        "expect_to_contain_text_timeout_is_product_bug",
        CATEGORY_PRODUCT_BUG,
        failure_message="TimeoutError: Timeout 30000ms exceeded.\nwaiting for expect(locator).to_contain_text('Order confirmed')\n\nExpect: to_contain_text",
        required_signals=("assertion_failed",),
        forbidden_signals=("timeout",),
    ),
    CorpusEntry(
        "expect_to_be_enabled_timeout_is_product_bug",
        CATEGORY_PRODUCT_BUG,
        failure_message="TimeoutError: Timeout 5000ms exceeded.\nwaiting for expect(locator).to_be_enabled()\n\nExpect: to_be_enabled",
        required_signals=("assertion_failed",),
        forbidden_signals=("timeout",),
    ),
    # --- systematic class 2: browser crash/lifecycle failures are environment ---
    CorpusEntry(
        "target_page_context_browser_closed_is_environment",
        CATEGORY_ENVIRONMENT,
        failure_message='Playwright: Target page, context or browser has been closed\nCall log:\n  - clicking locator("#submit")',
        required_signals=("browser_crash",),
    ),
    CorpusEntry(
        "target_crashed_is_environment",
        CATEGORY_ENVIRONMENT,
        failure_message="Target crashed",
        required_signals=("browser_crash",),
    ),
    CorpusEntry(
        "browser_sigsegv_is_environment",
        CATEGORY_ENVIRONMENT,
        failure_message="Browser has been closed unexpectedly (SIGSEGV); core dumped",
        required_signals=("browser_crash",),
    ),
    # --- systematic class 3: a single stray 4xx in the network tail must not flip to contract_violation ---
    CorpusEntry(
        "favicon_404_tail_with_element_not_found_is_test_fragility",
        CATEGORY_TEST_FRAGILITY,
        failure_message="Element not found: #checkout-button",
        final_response="Final Response: The checkout page was loaded",
        network_requests=tuple((f"https://shop.test/page/{index}", 200) for index in range(20)) + (("https://shop.test/favicon.ico", 404),),
        forbidden_signals=("network_4xx_responses",),
    ),
    CorpusEntry(
        "analytics_429_tail_with_element_not_found_is_test_fragility",
        CATEGORY_TEST_FRAGILITY,
        failure_message="Element not found: #add-to-cart",
        network_requests=tuple((f"https://shop.test/catalog/{index}", 200) for index in range(12)) + (("https://analytics.test/collect", 429),),
        forbidden_signals=("network_4xx_responses",),
    ),
    CorpusEntry(
        "real_api_404_storm_is_contract_violation",
        CATEGORY_CONTRACT_VIOLATION,
        failure_message="Order placement failed",
        network_requests=tuple((f"https://api.test/orders/{index}", 404) for index in range(5)),
        required_signals=("network_4xx_responses",),
    ),
    # --- systematic class 4: bare kwarg timeouts in tracebacks are call-site parameters, not infra ---
    CorpusEntry(
        "urlopen_kwarg_timeout_is_product_bug",
        CATEGORY_PRODUCT_BUG,
        failure_message='AssertionError: assert x == y\n  File "urllib3/connectionpool.py", in urlopen\n    conn.urlopen(request, timeout=timeout)',
        required_signals=("assertion_failed",),
        forbidden_signals=("timeout",),
    ),
    CorpusEntry(
        "db_call_kwarg_timeout_is_product_bug",
        CATEGORY_PRODUCT_BUG,
        failure_message='AssertionError: assert rows == 3\n  File "db/repo.py", in run_report\n    query(q, timeout=30)',
        required_signals=("assertion_failed",),
        forbidden_signals=("timeout",),
    ),
    CorpusEntry(
        "plain_timeout_same_frame_is_environment",
        CATEGORY_ENVIRONMENT,
        failure_message='TimeoutError: Timeout 30000ms exceeded\n  File "urllib3/connectionpool.py", in urlopen\n    conn.urlopen(request, timeout=timeout)',
        required_signals=("timeout",),
    ),
    # --- selenium-style lookups are test fragility ---
    CorpusEntry(
        "selenium_no_such_element_is_test_fragility",
        CATEGORY_TEST_FRAGILITY,
        failure_message='selenium.common.exceptions.NoSuchElementException: Message: no such element: Unable to locate element: {"method":"css selector","selector":"#save-btn"}',
        required_signals=("element_not_found",),
    ),
    # --- no evidence at all is an explicit low-confidence guess ---
    CorpusEntry(
        "empty_evidence_is_unclassified_guess",
        CATEGORY_UNCLASSIFIED,
        expected_confidence=UNCLASSIFIED_CONFIDENCE,
    ),
    # --- already-correct controls ---
    CorpusEntry(
        "control_connection_refused_is_environment",
        CATEGORY_ENVIRONMENT,
        failure_message="playwright: Timeout 30000ms exceeded: net::ERR_CONNECTION_REFUSED at https://api.test",
        required_signals=("connection_refused", "timeout"),
    ),
    CorpusEntry(
        "control_pytest_assert_mismatch_is_product_bug",
        CATEGORY_PRODUCT_BUG,
        failure_message="AssertionError: assert 5 == 3\n +  where 5 = add(2, 3)",
        required_signals=("assertion_failed",),
    ),
    CorpusEntry(
        "control_nginx_502_is_contract_violation",
        CATEGORY_CONTRACT_VIOLATION,
        failure_message="<html><body><h1>502 Bad Gateway</h1><hr><center>nginx</center></body></html>",
        required_signals=("http_status_code",),
    ),
)


@pytest.mark.parametrize("entry", CORPUS, ids=[entry.id for entry in CORPUS])
def test_quality_corpus_entry(entry: CorpusEntry, tmp_path) -> None:
    network_log_path = None
    if entry.network_requests:
        network_log_path = tmp_path / "network_logs.json"
        network_log_path.write_text("\n".join(json.dumps({"url": url, "method": "GET", "status": status}) for url, status in entry.network_requests), encoding="utf-8")

    result = asyncio.run(
        classify_failure_with_context(
            {
                "failure_message": entry.failure_message,
                "final_response": entry.final_response,
                "properties": {"Assert Summary": entry.assert_summary, "Network Logs": str(network_log_path) if network_log_path else ""},
            }
        )
    )

    assert result.category == entry.expected_category, f"{entry.id}: classified {result.category} via {result.signals}"
    for signal in entry.required_signals:
        assert signal in result.signals, f"{entry.id}: missing signal {signal} in {result.signals}"
    for signal in entry.forbidden_signals:
        assert signal not in result.signals, f"{entry.id}: forbidden signal {signal} in {result.signals}"
    if entry.expected_confidence is not None:
        assert result.confidence == entry.expected_confidence, f"{entry.id}: confidence {result.confidence} != {entry.expected_confidence}"
    if entry.expected_category == CATEGORY_UNCLASSIFIED:
        assert result.signals == []
