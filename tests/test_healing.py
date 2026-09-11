"""Offline tests for the healing package.

Covers failure classification (categories, priority, confidence bounds,
network-log boosting), the persistent SelectorStore (record/lookup/prune/
persistence/corrupt-file recovery) and heal proposals (store, alternatives,
fake LLM, best-effort application against a fake page). No network, no LLM,
no real browser.
"""

import asyncio
import json
import os
import time
from types import SimpleNamespace

import testzeus_hercules.healing.selector_store as selector_store_module
from testzeus_hercules.healing import (
    CATEGORY_CONTRACT_VIOLATION,
    CATEGORY_ENVIRONMENT,
    CATEGORY_PRODUCT_BUG,
    CATEGORY_TEST_FRAGILITY,
    CATEGORY_UNCLASSIFIED,
    FailureClassification,
    apply_proposal_if_possible,
    classify_case,
    classify_failure,
    classify_failure_with_context,
    format_classification_markdown,
    format_proposal_markdown,
    propose_from_alternatives,
    propose_from_store,
    propose_with_llm,
)
from testzeus_hercules.healing.failure_classifier import (
    EVIDENCE_HEAD_LIMIT,
    MAX_CONFIDENCE,
    MIN_CONFIDENCE,
    UNCLASSIFIED_CONFIDENCE,
)
from testzeus_hercules.healing.heal_proposer import (
    PAGE_TEXT_LIMIT,
    _parse_llm_selectors,
    locator_string,
)
from testzeus_hercules.healing.selector_store import SelectorStore


def _assert_in_bounds(classification: FailureClassification) -> None:
    assert MIN_CONFIDENCE <= classification.confidence <= MAX_CONFIDENCE


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


def test_environment_connection_refused() -> None:
    result = classify_failure(failure_message="playwright: Timeout 30000ms exceeded: net::ERR_CONNECTION_REFUSED at https://api.test")

    assert result.category == CATEGORY_ENVIRONMENT
    assert "connection_refused" in result.signals
    assert "timeout" in result.signals
    _assert_in_bounds(result)


def test_environment_name_resolution_and_proxy() -> None:
    result = classify_failure(failure_message="page.goto: DNS name resolution failed through proxy")

    assert result.category == CATEGORY_ENVIRONMENT
    assert "name_resolution" in result.signals
    assert "proxy" in result.signals


def test_environment_browser_missing() -> None:
    result = classify_failure(failure_message="BrowserType.launch: Executable doesn't exist, please run playwright install")

    assert result.category == CATEGORY_ENVIRONMENT
    assert "browser_missing" in result.signals


def test_environment_browser_crash() -> None:
    result = classify_failure(failure_message='Playwright: Target page, context or browser has been closed\nCall log: clicking locator("#submit")')

    assert result.category == CATEGORY_ENVIRONMENT
    assert "browser_crash" in result.signals


def test_environment_browser_crash_does_not_shadow_launch_failure() -> None:
    result = classify_failure(failure_message="BrowserType.launch: failed to launch browser because the executable doesn't exist")

    assert result.category == CATEGORY_ENVIRONMENT
    assert "browser_missing" in result.signals
    assert "browser_crash" not in result.signals


def test_environment_confidence_hits_cap_when_all_signals_match() -> None:
    result = classify_failure(
        failure_message=(
            "Connection refused; connection timeout; net::ERR_ABORTED; DNS name resolution failed; "
            "proxy error; SSL certificate verify failed; browser executable doesn't exist; navigation interrupted; target crashed"
        )
    )

    assert result.category == CATEGORY_ENVIRONMENT
    assert len(result.signals) == len(
        [
            "connection_refused",
            "timeout",
            "net_err",
            "name_resolution",
            "proxy",
            "ssl_certificate",
            "browser_missing",
            "browser_crash",
            "navigation_interrupted",
        ]
    )
    assert result.confidence == MAX_CONFIDENCE


def test_contract_violation_http_404() -> None:
    result = classify_failure(failure_message="Request to /api/users returned 404")

    assert result.category == CATEGORY_CONTRACT_VIOLATION
    assert "http_status_code" in result.signals
    _assert_in_bounds(result)


def test_contract_violation_schema_and_json_errors() -> None:
    result = classify_failure(failure_message="json decode error: response failed schema validation", assert_summary="expected status 200")

    assert result.category == CATEGORY_CONTRACT_VIOLATION
    assert "json_decode" in result.signals
    assert "schema_validation" in result.signals
    assert "status_code_mention" in result.signals


def test_test_fragility_timeout_waiting_for_element_is_not_environment() -> None:
    result = classify_failure(failure_message="Timeout waiting for element 'login-button' to be visible")

    assert result.category == CATEGORY_TEST_FRAGILITY
    assert "timeout_waiting_for_element" in result.signals
    assert "timeout" not in result.signals


def test_test_fragility_strict_mode_and_visibility() -> None:
    result = classify_failure(failure_message="strict mode violation: locator resolved to 2 elements; element is not visible")

    assert result.category == CATEGORY_TEST_FRAGILITY
    assert "strict_mode_violation" in result.signals
    assert "not_visible" in result.signals


# Real Playwright timeout messages put the wait clause on a later line, which
# the environment pattern's negative lookahead cannot see.
PLAYWRIGHT_TIMEOUT_MESSAGE = "TimeoutError: Timeout 30000ms exceeded.\nwaiting for locator('#submit') to be visible"


def test_test_fragility_playwright_multiline_timeout_is_not_environment() -> None:
    result = classify_failure(failure_message=PLAYWRIGHT_TIMEOUT_MESSAGE)

    assert result.category == CATEGORY_TEST_FRAGILITY
    assert "timeout_waiting_for_element" in result.signals
    assert "timeout" not in result.signals


def test_test_fragility_assert_visible_timeout_variant_is_not_environment() -> None:
    result = classify_failure(failure_message="AssertionError: Locator timed out\nTimeout 5000ms exceeded.\nwaiting for locator('.save-button') to be visible")

    assert result.category == CATEGORY_TEST_FRAGILITY
    assert "timeout_waiting_for_element" in result.signals
    assert "timeout" not in result.signals


def test_environment_plain_timeout_without_wait_clause() -> None:
    result = classify_failure(failure_message="Test timeout of 60000ms exceeded")

    assert result.category == CATEGORY_ENVIRONMENT
    assert "timeout" in result.signals


def test_bare_timeout_kwarg_in_traceback_is_not_environment() -> None:
    """``timeout=timeout`` / ``timeout=30`` name call-site parameters, not a failure reason."""
    result = classify_failure(failure_message='AssertionError: assert body == "ok"\n  File "urllib3/connectionpool.py", in urlopen\n    conn.urlopen(request, timeout=timeout)')

    assert result.category == CATEGORY_PRODUCT_BUG
    assert "assertion_failed" in result.signals
    assert "timeout" not in result.signals


def test_wait_for_element_phrase_is_never_environment() -> None:
    """A wait-for-element phrase (get_by_/frame variant with no fragility signal) must still not be infra."""
    result = classify_failure(failure_message="Timeout 30000ms exceeded.\nwaiting for get_by_label('Email') to be editable")

    assert result.category != CATEGORY_ENVIRONMENT
    assert "timeout" not in result.signals


def test_test_fragility_element_not_found_but_page_loaded() -> None:
    result = classify_failure(failure_message="Element not found: submit-button", final_response="The page loaded and shows the dashboard")

    assert result.category == CATEGORY_TEST_FRAGILITY
    assert "element_not_found" in result.signals
    assert "element_not_found_page_loaded" in result.signals


def test_product_bug_expected_but_page_showed_error() -> None:
    result = classify_failure(
        failure_message="Assertion failed: Expected 'Welcome' but page showed 'Error: invalid data'",
        final_response="Login finished, dashboard content was checked.",
    )

    assert result.category == CATEGORY_PRODUCT_BUG
    assert "assertion_failed" in result.signals
    assert "expected_but_found" in result.signals
    _assert_in_bounds(result)


def test_product_bug_data_not_saved() -> None:
    result = classify_failure(failure_message="The record was not created after saving the form")

    assert result.category == CATEGORY_PRODUCT_BUG
    assert "data_not_saved" in result.signals


def test_environment_wins_over_contract_when_mixed() -> None:
    result = classify_failure(failure_message="Connection refused while /api/health returned 500")

    assert result.category == CATEGORY_ENVIRONMENT
    assert "connection_refused" in result.signals
    _assert_in_bounds(result)


def test_contract_wins_over_product_bug_when_mixed() -> None:
    result = classify_failure(failure_message="Assertion failed: expected 3 bikes, but /api/bikes returned 404")

    assert result.category == CATEGORY_CONTRACT_VIOLATION


def test_unclassified_default() -> None:
    result = classify_failure(failure_message="The scenario could not be completed as described")

    assert result.category == CATEGORY_UNCLASSIFIED
    assert result.signals == []
    # A guess: reported below the matched-signal floor so it can never be
    # confused with a low-confidence matched classification.
    assert result.confidence == UNCLASSIFIED_CONFIDENCE
    assert result.confidence < MIN_CONFIDENCE
    assert "No known failure signals" in result.rationale


def test_huge_evidence_text_classifies_fast_with_same_category() -> None:
    """Regex matching is capped to head+tail of the evidence: 5MB must stay fast and equivalent."""
    big_message = "connection refused; " * 250_000  # ~5MB

    start = time.perf_counter()
    result = classify_failure(failure_message=big_message)
    elapsed = time.perf_counter() - start

    prefix = classify_failure(failure_message=big_message[:EVIDENCE_HEAD_LIMIT])

    assert result.category == prefix.category == CATEGORY_ENVIRONMENT
    assert "connection_refused" in result.signals
    assert elapsed < 0.5


def test_network_log_with_all_500s_boosts_to_contract(tmp_path) -> None:
    network_path = tmp_path / "network_logs.json"
    lines = [json.dumps({"url": f"https://api.test/items/{index}", "method": "GET", "status": 500}) for index in range(3)]
    network_path.write_text("\n".join(lines), encoding="utf-8")

    plain = classify_failure(failure_message="The scenario failed unexpectedly")
    boosted = asyncio.run(classify_failure_with_context({"failure_message": "The scenario failed unexpectedly", "properties": {"Network Logs": str(network_path)}}))

    assert plain.category == CATEGORY_UNCLASSIFIED
    assert boosted.category == CATEGORY_CONTRACT_VIOLATION
    assert "network_5xx_responses" in boosted.signals


def test_network_log_single_stray_4xx_does_not_boost_contract(tmp_path) -> None:
    """A lone favicon 404 in an otherwise healthy tail is noise, not a contract breach."""
    network_path = tmp_path / "network_logs.json"
    lines = [json.dumps({"url": f"https://shop.test/page/{index}", "status": 200}) for index in range(20)]
    lines.append(json.dumps({"url": "https://shop.test/favicon.ico", "status": 404}))
    network_path.write_text("\n".join(lines), encoding="utf-8")

    boosted = asyncio.run(classify_failure_with_context({"failure_message": "Element not found: #checkout-button", "properties": {"Network Logs": str(network_path)}}))

    assert boosted.category == CATEGORY_TEST_FRAGILITY
    assert "network_4xx_responses" not in boosted.signals


def test_network_log_404_storm_still_boosts_contract(tmp_path) -> None:
    network_path = tmp_path / "network_logs.json"
    lines = [json.dumps({"url": f"https://api.test/orders/{index}", "status": 404}) for index in range(5)]
    network_path.write_text("\n".join(lines), encoding="utf-8")

    boosted = asyncio.run(classify_failure_with_context({"failure_message": "Order placement failed", "properties": {"Network Logs": str(network_path)}}))

    assert boosted.category == CATEGORY_CONTRACT_VIOLATION
    assert "network_4xx_responses" in boosted.signals


def test_network_log_without_any_successful_request_boosts_environment(tmp_path) -> None:
    network_path = tmp_path / "network_logs.json"
    lines = [json.dumps({"url": "https://api.test/items", "method": "GET", "status": 0}) for _ in range(3)]
    network_path.write_text("\n".join(lines), encoding="utf-8")

    boosted = asyncio.run(classify_failure_with_context({"failure_message": "Requests failed", "properties": {"Network Logs": str(network_path)}}))

    assert boosted.category == CATEGORY_ENVIRONMENT
    assert "network_no_successful_requests" in boosted.signals


def test_classify_with_context_reads_missing_log_gracefully() -> None:
    case = {
        "name": "Scenario: lonely",
        "status": "error",
        "failure_message": "net::ERR_NAME_NOT_RESOLVED",
        "system_out": "Final Response: nothing loaded",
        "properties": {"Network Logs": "/nowhere/network_logs.json", "Planner Thoughts": ""},
    }

    result = asyncio.run(classify_failure_with_context(case, proofs_dir=None))

    assert result.category == CATEGORY_ENVIRONMENT
    assert "name_resolution" in result.signals


def test_classify_failure_with_context_emits_event() -> None:
    events: list[dict] = []
    case = {"name": "Scenario: X", "failure_message": "Request returned 404", "system_out": "", "properties": {}}

    result = asyncio.run(classify_failure_with_context(case, proofs_dir=None, on_event=events.append))

    assert result.category == CATEGORY_CONTRACT_VIOLATION
    assert len(events) == 1
    assert events[0]["kind"] == "failure_classified"
    assert events[0]["canonical_name"] == "Scenario: X"
    assert events[0]["status"] == "ok"


def test_classify_case_pure_wrapper() -> None:
    case = {
        "name": "Scenario: User list",
        "classname": "Feature: Users",
        "status": "failed",
        "failure_message": "Request to /api/users returned 404",
        "system_out": "Final Response: user list empty",
        "properties": {"Planner Thoughts": "tried clicking the button"},
        "time": 12.5,
    }

    result = classify_case(case)

    assert result.category == CATEGORY_CONTRACT_VIOLATION
    assert "http_status_code" in result.signals


def test_classify_failure_coerces_non_string_evidence() -> None:
    """Evidence fields may hold non-strings (e.g. int codes from junit dicts); coerce, never raise."""
    result = classify_failure(failure_message=123, final_response="Request to /api/users returned 404", assert_summary=None, planner_thoughts=4.5)

    assert result.category == CATEGORY_CONTRACT_VIOLATION
    assert "http_status_code" in result.signals


def test_classify_failure_non_string_final_response_does_not_raise() -> None:
    # The page-loaded boost searches the raw final_response; a non-string must not raise.
    result = classify_failure(failure_message="Element not found: submit-button", final_response=200)

    assert result.category == CATEGORY_TEST_FRAGILITY
    assert "element_not_found" in result.signals


def test_classify_failure_keeps_falsy_filter_for_non_strings() -> None:
    # Falsy non-strings (0, None) stay filtered out of the evidence text.
    result = classify_failure(failure_message=0, final_response="playwright: connection refused")

    assert result.category == CATEGORY_ENVIRONMENT
    assert "connection_refused" in result.signals


def test_classify_case_tolerates_non_string_fields() -> None:
    case = {"name": "Scenario: numeric", "failure_message": 404, "system_out": 500}

    result = classify_case(case)

    assert result.category == CATEGORY_CONTRACT_VIOLATION
    assert "http_status_code" in result.signals


def test_format_classification_markdown() -> None:
    md = format_classification_markdown(
        FailureClassification(
            category=CATEGORY_PRODUCT_BUG,
            confidence=0.62,
            rationale="2 product_bug signal(s) matched.",
            signals=["assertion_failed", "expected_but_found"],
        )
    )

    assert "### Failure classification: `product_bug`" in md
    assert "**Confidence:** 0.62" in md
    assert "`assertion_failed`" in md
    assert "`expected_but_found`" in md


# ---------------------------------------------------------------------------
# SelectorStore
# ---------------------------------------------------------------------------


def test_selector_store_record_lookup_and_merge(tmp_path) -> None:
    store = SelectorStore(str(tmp_path / "store.json"))

    store.record("role=button|name=Submit", {"xpath": "//button[1]"})
    store.record("role=button|name=Submit", {"aria": "Submit", "xpath": "//button[@id='submit']"}, source="healed")

    entry = store.lookup("role=button|name=Submit")
    assert entry is not None
    assert entry["xpath"] == "//button[@id='submit']"  # merged, later value wins
    assert entry["aria"] == "Submit"
    assert entry["source"] == "healed"
    assert entry["hits"] == 1
    assert entry["failures"] == 0
    assert entry["last_seen"]

    assert store.lookup("role=button|name=Submit")["hits"] == 2
    assert store.lookup("role=button|name=Missing") is None


def test_selector_store_persists_across_instances(tmp_path) -> None:
    store_path = str(tmp_path / "store.json")
    first = SelectorStore(store_path)
    first.record("role=link|name=Docs", {"aria": "Docs"})

    second = SelectorStore(store_path)
    entry = second.lookup("role=link|name=Docs")

    assert entry is not None
    assert entry["aria"] == "Docs"
    assert set(second.entries()) == {"role=link|name=Docs"}
    # Hits stay in memory until the next mutation/save; an explicit save
    # flushes the hit counted by the second instance so a third one sees it.
    second.save()
    assert SelectorStore(store_path).lookup("role=link|name=Docs")["hits"] == 2


def test_selector_store_lookup_does_not_write_to_disk(tmp_path, monkeypatch) -> None:
    # Lookup is a hot path: hits are tracked in memory and must not trigger a
    # JSON dump + atomic rename per hit.
    store_path = str(tmp_path / "store.json")
    store = SelectorStore(store_path)
    store.record("role=button|name=Submit", {"css": "#submit"})

    replaced: list[str] = []
    real_replace = selector_store_module.os.replace
    monkeypatch.setattr(selector_store_module.os, "replace", lambda src, dst: replaced.append(dst) or real_replace(src, dst))

    assert store.lookup("role=button|name=Submit")["hits"] == 1
    assert store.lookup("role=button|name=Submit")["hits"] == 2
    assert replaced == []  # lookups never persist

    store.record_failure("role=button|name=Submit")
    assert replaced == [store_path]  # mutations still persist


def test_selector_store_small_store_keeps_save_on_record(tmp_path, monkeypatch) -> None:
    """Stores under _FLUSH_EVERY entries keep the legacy write-on-every-mutation behavior."""
    store_path = str(tmp_path / "store.json")
    store = SelectorStore(store_path)
    writes: list[str] = []
    real_replace = selector_store_module.os.replace
    monkeypatch.setattr(selector_store_module.os, "replace", lambda src, dst: writes.append(dst) or real_replace(src, dst))

    store.record("role=button|name=One", {"css": "#one"})
    store.record("role=button|name=Two", {"css": "#two"})

    assert len(writes) == 2  # one atomic write per record, as before
    assert set(SelectorStore(store_path).entries()) == {"role=button|name=One", "role=button|name=Two"}


def test_selector_store_large_store_batches_record_writes(tmp_path, monkeypatch) -> None:
    """A record burst on a large store must batch whole-store writes, not serialize per record."""
    store_path = str(tmp_path / "store.json")
    seeded = {f"role=button|name=Seeded{index}": {"css": f"#seed{index}", "hits": 0, "failures": 0} for index in range(500)}
    with open(store_path, "w", encoding="utf-8") as handle:
        json.dump(seeded, handle)

    store = SelectorStore(store_path)
    writes: list[str] = []
    real_replace = selector_store_module.os.replace

    def counting_replace(src, dst):
        writes.append(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(selector_store_module.os, "replace", counting_replace)

    for index in range(100):
        store.record(f"role=button|name=Burst{index}", {"css": f"#burst{index}"})

    assert len(writes) <= 6  # cadence flush (every _FLUSH_EVERY=25 mutations), not 100 full dumps
    assert writes, "the cadence must still flush periodically on large stores"
    # The cadence flush made the burst durable.
    assert "role=button|name=Burst99" in SelectorStore(store_path).entries()

    # Mutations after the last flush stay in memory until an explicit save().
    for index in range(100, 110):
        store.record(f"role=button|name=Burst{index}", {"css": f"#burst{index}"})
    writes.clear()
    store.save()
    assert len(writes) == 1  # explicit save() is an unconditional flush
    assert "role=button|name=Burst109" in SelectorStore(store_path).entries()
    assert len(SelectorStore(store_path).entries()) == 610


def test_selector_store_hits_persisted_by_next_mutation(tmp_path) -> None:
    store_path = str(tmp_path / "store.json")
    store = SelectorStore(store_path)
    store.record("role=button|name=Buy", {"css": "#buy"})

    store.lookup("role=button|name=Buy")
    store.lookup("role=button|name=Buy")
    store.record("role=button|name=Other", {"css": "#other"})  # unrelated mutation flushes pending hits

    assert SelectorStore(store_path).lookup("role=button|name=Buy")["hits"] == 3


def test_selector_store_prunes_stale_entry_after_threshold(tmp_path) -> None:
    store = SelectorStore(str(tmp_path / "store.json"))
    store.record("role=button|name=Ghost", {"xpath": "//button[@data-ghost]"})

    for _ in range(5):
        store.record_failure("role=button|name=Ghost")

    # failures >= 5 and failures > hits (0) -> forgotten.
    assert store.lookup("role=button|name=Ghost") is None
    assert store.entries() == {}


def test_selector_store_keeps_popular_entry_despite_failures(tmp_path) -> None:
    store = SelectorStore(str(tmp_path / "store.json"))
    store.record("role=button|name=Popular", {"aria": "Popular"})
    for _ in range(6):
        store.lookup("role=button|name=Popular")

    for _ in range(5):
        store.record_failure("role=button|name=Popular")

    # failures (5) is not greater than hits (6) -> kept.
    assert store.lookup("role=button|name=Popular") is not None


def test_selector_store_recovers_from_corrupt_file(tmp_path) -> None:
    store_path = tmp_path / "store.json"
    store_path.write_text("{ this is not json ]", encoding="utf-8")

    store = SelectorStore(str(store_path))

    assert store.entries() == {}
    store.record("role=button|name=Fresh", {"css": "#fresh"})
    assert SelectorStore(str(store_path)).lookup("role=button|name=Fresh") is not None


def test_selector_store_tolerates_non_int_counters(tmp_path) -> None:
    """Hand-edited stores with junk counters (e.g. "failures": "many") must not crash healing."""
    store_path = tmp_path / "store.json"
    store_path.write_text(
        json.dumps(
            {
                "role=button|name=Broken": {"css": "#broken", "hits": "many", "failures": "many"},
                "role=button|name=Numeric": {"aria": "Numeric", "hits": "3", "failures": None},
            }
        ),
        encoding="utf-8",
    )

    store = SelectorStore(str(store_path))

    # "many" coerced to 0 at load; the in-memory hit then brings it to 1.
    entry = store.lookup("role=button|name=Broken")
    assert entry is not None
    assert entry["hits"] == 1
    assert entry["failures"] == 0

    store.record_failure("role=button|name=Broken")
    assert store.entries()["role=button|name=Broken"]["failures"] == 1

    proposal = propose_from_store("role=button|name=Numeric", store=store)
    assert proposal is not None
    assert proposal.confidence == 0.8  # null failures coerce to 0 -> no decay


def test_selector_store_emits_events(tmp_path) -> None:
    events: list[dict] = []
    store = SelectorStore(str(tmp_path / "store.json"), on_event=events.append)

    store.record("role=button|name=Ok", {"aria": "Ok"})
    store.record_failure("role=button|name=Ok")
    store.record_failure("role=button|name=Unknown")  # unknown name: no event

    assert [event["kind"] for event in events] == ["selector_recorded", "selector_failed"]
    assert events[0]["canonical_name"] == "role=button|name=Ok"
    assert events[1]["status"] == "ok"


# ---------------------------------------------------------------------------
# Heal proposals
# ---------------------------------------------------------------------------


def test_propose_from_store_hit_and_confidence_decay(tmp_path) -> None:
    store_path = str(tmp_path / "store.json")
    store = SelectorStore(store_path)
    store.record("role=button|name=Submit", {"aria": "Submit", "xpath": "//button[@id='submit']"})
    store.record_failure("role=button|name=Submit")

    proposal = propose_from_store("role=button|name=Submit", store=store)

    assert proposal is not None
    assert proposal.basis == "selector_store"
    assert proposal.confidence == 0.8 - 0.1  # one recorded failure
    assert proposal.proposed_selectors["aria"] == "Submit"


def test_propose_from_store_confidence_floors_at_zero(tmp_path) -> None:
    store = SelectorStore(str(tmp_path / "store.json"))
    store.record("role=button|name=Old", {"css": "#old"})
    for _ in range(12):
        store.lookup("role=button|name=Old")
    for _ in range(10):
        store.record_failure("role=button|name=Old")

    # failures (10) never exceed hits (12), so the entry survives pruning;
    # 0.8 - 10 * 0.1 clamps to 0.0.
    proposal = propose_from_store("role=button|name=Old", store=store)

    assert proposal is not None
    assert proposal.confidence == 0.0


def test_propose_from_store_miss_returns_none(tmp_path) -> None:
    store = SelectorStore(str(tmp_path / "store.json"))

    assert propose_from_store("role=button|name=Nobody", store=store) is None


def test_propose_from_alternatives_prefers_aria() -> None:
    proposal = propose_from_alternatives({"xpath": "//div[3]/button[2]", "aria": "Add to cart"}, "role=button|name=AddToCart")

    assert proposal is not None
    assert proposal.basis == "alternative_selectors"
    assert proposal.confidence == 0.6
    assert list(proposal.proposed_selectors) == ["aria", "xpath"]
    assert proposal.proposed_selectors["aria"] == "Add to cart"


def test_propose_from_alternatives_empty_returns_none() -> None:
    assert propose_from_alternatives({}, "role=button|name=X") is None
    assert propose_from_alternatives({"xpath": "   "}, "role=button|name=X") is None


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.content)


class BrokenLLM:
    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        raise RuntimeError("llm down")


def test_propose_with_llm_parses_json_content() -> None:
    llm = FakeLLM('Here you go: ```json\n{"aria": "Submit", "css": "#submit", "junk": true}\n```')

    proposal = asyncio.run(propose_with_llm("Welcome back, user", "role=button|name=Submit", llm=llm))

    assert proposal is not None
    assert proposal.basis == "llm"
    assert proposal.confidence == 0.4
    assert proposal.proposed_selectors == {"aria": "Submit", "css": "#submit"}
    assert "role=button|name=Submit" in llm.prompts[0]
    assert "Welcome back, user" in llm.prompts[0]


def test_propose_with_llm_returns_none_on_garbage_or_errors() -> None:
    assert asyncio.run(propose_with_llm("page text", "role=button|name=X", llm=FakeLLM("no json in sight"))) is None
    assert asyncio.run(propose_with_llm("page text", "role=button|name=X", llm=FakeLLM('{"unknown_key": "v"}'))) is None
    assert asyncio.run(propose_with_llm("page text", "role=button|name=X", llm=BrokenLLM())) is None


def test_parse_llm_selectors_skips_null_values_and_none_content() -> None:
    assert _parse_llm_selectors(None) == {}
    # JSON null selectors must be skipped, not turned into the string "None".
    assert _parse_llm_selectors('{"css": null, "aria": "Submit"}') == {"aria": "Submit"}
    assert _parse_llm_selectors('{"css": "   "}') == {}


def test_propose_with_llm_handles_null_content() -> None:
    assert asyncio.run(propose_with_llm("page text", "role=button|name=X", llm=FakeLLM(None))) is None


def test_llm_prompt_delimits_untrusted_text_and_strips_control_chars() -> None:
    llm = FakeLLM('{"aria": "Submit"}')

    asyncio.run(propose_with_llm("Welcome\x00back\x1fuser\nSign in below", "role=button|name=Sub\x07mit", llm=llm))

    prompt = llm.prompts[0]
    assert "\x00" not in prompt and "\x1f" not in prompt and "\x07" not in prompt
    assert "<canonical_name>role=button|name=Submit</canonical_name>" in prompt
    assert "<page_text>" in prompt and "</page_text>" in prompt
    assert "Welcomebackuser" in prompt  # content kept, control chars stripped


def test_llm_prompt_page_text_is_truncated() -> None:
    llm = FakeLLM('{"aria": "Submit"}')
    huge_page_text = "x" * (PAGE_TEXT_LIMIT * 2)

    asyncio.run(propose_with_llm(huge_page_text, "role=button|name=X", llm=llm))

    prompt = llm.prompts[0]
    assert huge_page_text[:PAGE_TEXT_LIMIT] in prompt
    assert huge_page_text not in prompt


def test_format_proposal_markdown_escapes_selector_html() -> None:
    proposal = propose_from_alternatives({"css": "<script>alert(1)</script>"}, "role=button|name=X")

    md = format_proposal_markdown(proposal)

    assert "<script>" not in md
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in md


def test_format_proposal_markdown_escapes_layout_breaking_selectors() -> None:
    proposal = propose_from_alternatives({"css": "a`b|c\nd"}, "role=button|name=X")

    md = format_proposal_markdown(proposal)

    assert "a\\`b\\|c d" in md  # backticks/pipes escaped, newline collapsed


def test_locator_string_kinds() -> None:
    assert locator_string("aria", 'Say "hi"') == '[aria-label="Say \\"hi\\""]'
    assert locator_string("xpath", "//div[@id='x']") == "//div[@id='x']"  # playwright auto-detects //
    assert locator_string("xpath", "div[@id='x']") == "xpath=div[@id='x']"
    assert locator_string("xpath", "xpath=//div[@id='x']") == "xpath=//div[@id='x']"
    assert locator_string("css", "#submit") == "#submit"


class FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    async def count(self) -> int:
        return self._count


class FakePage:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.probed: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        self.probed.append(selector)
        return FakeLocator(self._counts.get(selector, 0))


def test_apply_proposal_succeeds_with_aria_selector() -> None:
    proposal = propose_from_alternatives({"aria": "Submit", "xpath": "//button[9]"}, "role=button|name=Submit")
    page = FakePage({'[aria-label="Submit"]': 2})

    assert asyncio.run(apply_proposal_if_possible(proposal, page)) is True
    # aria is tried first, so the fragile xpath is never probed.
    assert page.probed == ['[aria-label="Submit"]']


def test_apply_proposal_falls_through_until_a_selector_matches() -> None:
    proposal = propose_from_alternatives({"aria": "Missing", "css": "#real", "xpath": "//gone"}, "role=button|name=X")
    page = FakePage({"#real": 1})

    assert asyncio.run(apply_proposal_if_possible(proposal, page)) is True
    assert page.probed == ['[aria-label="Missing"]', "#real"]


def test_apply_proposal_returns_false_when_nothing_matches() -> None:
    proposal = propose_from_alternatives({"aria": "Nope"}, "role=button|name=X")
    page = FakePage({})

    assert asyncio.run(apply_proposal_if_possible(proposal, page)) is False
    assert asyncio.run(apply_proposal_if_possible(None, FakePage({}))) is False


def test_apply_proposal_swallows_locator_errors() -> None:
    class ExplodingPage:
        def locator(self, selector: str) -> FakeLocator:
            raise RuntimeError("page closed")

    proposal = propose_from_alternatives({"css": "#x"}, "role=button|name=X")

    assert asyncio.run(apply_proposal_if_possible(proposal, ExplodingPage())) is False


def test_apply_proposal_emits_events() -> None:
    events: list[dict] = []
    proposal = propose_from_alternatives({"aria": "Hit"}, "role=button|name=X")
    page = FakePage({'[aria-label="Hit"]': 1})

    assert asyncio.run(apply_proposal_if_possible(proposal, page, on_event=events.append)) is True
    assert events[0]["kind"] == "heal_applied"
    assert events[0]["status"] == "ok"


def test_format_proposal_markdown() -> None:
    proposal = propose_from_alternatives({"aria": "Add to cart", "xpath": "//button[2]"}, "role=button|name=AddToCart")

    md = format_proposal_markdown(proposal)

    assert "### Heal proposal for `role=button|name=AddToCart`" in md
    assert "**Basis:** `alternative_selectors`" in md
    assert "**Confidence:** 0.60" in md
    assert "`aria`: Add to cart" in md
    assert "aria preferred over xpath" in md


def test_format_proposal_markdown_marks_suspect_selector_syntax() -> None:
    """Impossible selectors stay listed but their note carries a suspect-syntax marker; balanced ones are not flagged."""
    proposal = propose_from_alternatives({"css": "#submit[", "xpath": "//button[@id='x", "aria": "Submit"}, "role=button|name=X")

    md = format_proposal_markdown(proposal)

    assert "`css`: #submit[" in md  # still listed
    assert "⚠ suspect selector syntax: xpath, css" in md
    assert "aria preferred over xpath" in md  # original note preserved

    # Balanced css/xpath are never flagged; aria is free text and always exempt.
    ok = format_proposal_markdown(propose_from_alternatives({"css": "a[x='()']", "xpath": "//b[2]", "aria": "it's a deal("}, "role=button|name=X"))
    assert "suspect selector syntax" not in ok


def test_clauseless_visibility_assert_timeout_is_fragility() -> None:
    """expect()-style visibility timeouts without a 'waiting for' call-log line are still fragility, never environment."""
    for message in (
        "expect(received).to_be_visible timed out",
        "TimeoutError: Timeout 30000ms exceeded.\nlocator('button.submit') to be visible",
    ):
        result = classify_failure(failure_message=message)
        assert result.category == CATEGORY_TEST_FRAGILITY, f"{message!r} -> {result.category}"
        assert result.category != CATEGORY_ENVIRONMENT


def test_clauseless_expect_kwarg_timeout_is_assertion_product_bug() -> None:
    """A kwarg-style ``(timeout)`` in an expect() call is a parameter, not an infra timeout.

    The bare-timeout lookahead excludes it from the environment signal, and the
    expect() assertion phrasing makes the failure a product bug -- never environment.
    """
    result = classify_failure(failure_message="expect(locator).to_be_visible(timeout) failed")

    assert result.category == CATEGORY_PRODUCT_BUG
    assert "assertion_failed" in result.signals
    assert "timeout" not in result.signals
