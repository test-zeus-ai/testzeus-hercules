"""Tests for the deterministic UI assertion tools (core.tools.ui_assertions).

All behavior is covered offline against pure string payloads — no browser, no
LLM. The assertion ledger is pointed at a tmp proof folder and every test
verifies the verdict the tool returns — not what an agent would narrate
about it. The capture round-trip uses the same per-test-id variable store as
the API tools.
"""

import asyncio
import hashlib
import json

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.api_calls import (
    _VARIABLES,
    interpolate_value,
    set_variable_value,
)
from testzeus_hercules.core.tools.tool_registry import tool_registry
from testzeus_hercules.core.tools.ui_assertions import assert_ui, capture_ui_value

# Noise-free page text (so ledger fingerprints can be computed directly).
PAGE = "Checkout\n" "Welcome back, Ada.\n" "\n" "Order summary\n" "Order total: 42.50\n" "Status: paid\n" "\n" "Shipping\n" "Carrier: Pony Express\n"


@pytest.fixture
def proof_env(tmp_path):
    """Point the proof folder at tmp_path; restore the config afterwards."""
    conf = get_global_conf()
    original_config = dict(conf.get_config())
    original_test_id = conf.get_default_test_id()
    conf.get_config()["PROJECT_SOURCE_ROOT"] = str(tmp_path)
    conf.set_default_test_id("ui-assertion-tests")
    _VARIABLES.pop("ui-assertion-tests", None)
    yield conf
    _VARIABLES.pop("ui-assertion-tests", None)
    conf.get_config().clear()
    conf.get_config().update(original_config)
    conf.set_default_test_id(original_test_id)


def _assert_ui(payload, mode="contains", **kwargs):
    verdict = asyncio.run(assert_ui(payload, mode, **kwargs))
    assert isinstance(verdict, str) and verdict.startswith("{")
    return json.loads(verdict)


def _capture(payload, path_hint, as_name, scope=None):
    verdict = asyncio.run(capture_ui_value(payload, path_hint, as_name, scope))
    assert isinstance(verdict, str) and verdict.startswith("{")
    return json.loads(verdict)


def _ledger_lines(conf):
    log_path = f"{conf.get_proof_path()}/assertion_logs.log"
    with open(log_path, "r", encoding="utf-8") as log_file:
        return [json.loads(line) for line in log_file if line.strip()]


# ── contains ─────────────────────────────────────────────────────────────────


def test_contains_pass_and_fail(proof_env) -> None:
    passed = _assert_ui(PAGE, "contains", expected="Order total")
    assert passed["assertion"] == "PASSED"
    assert passed["actual"] == "visible"
    failed = _assert_ui(PAGE, "contains", expected="refund issued")
    assert failed["assertion"] == "FAILED"
    assert failed["actual"] == "not visible"


def test_contains_on_json_payload_searches_string_values_only(proof_env) -> None:
    payload = json.dumps({"page": {"heading": "Order shipped", "meta": {"trace_id": "abc"}}})
    assert _assert_ui(payload, "contains", expected="Order shipped")["assertion"] == "PASSED"
    assert _assert_ui(payload, "contains", expected="abc")["assertion"] == "PASSED"
    # A key name is structure, not visible text.
    verdict = _assert_ui(payload, "contains", expected="trace_id")
    assert verdict["assertion"] == "FAILED"


def test_contains_on_already_parsed_payload_searches_string_values_only(proof_env) -> None:
    # A payload handed over already parsed (not as a JSON string) obeys the
    # same rule: keys are structure, only string values are visible text.
    payload = {"page": {"heading": "Order shipped", "meta": {"trace_id": "abc"}}}
    assert _assert_ui(payload, "contains", expected="Order shipped")["assertion"] == "PASSED"
    assert _assert_ui(payload, "contains", expected="abc")["assertion"] == "PASSED"
    verdict = _assert_ui(payload, "contains", expected="trace_id")
    assert verdict["assertion"] == "FAILED"
    # A list payload keeps matching through its string items.
    assert _assert_ui(["Order shipped", "Signed for by Ada"], "contains", expected="Signed for by Ada")["assertion"] == "PASSED"


def test_contains_requires_expected(proof_env) -> None:
    verdict = _assert_ui(PAGE, "contains")
    assert verdict["assertion"] == "ERROR"
    assert "expected" in verdict["reason"]


# ── noise stripping ──────────────────────────────────────────────────────────


def test_ids_testids_and_urls_are_stripped_before_matching(proof_env) -> None:
    payload = 'data-testid="login-btn"\nhttps://example.com/secret-path\nSign in'
    assert _assert_ui(payload, "contains", expected="Sign in")["assertion"] == "PASSED"
    # The values exist only in hidden attributes / link targets.
    assert _assert_ui(payload, "contains", expected="login")["assertion"] == "FAILED"
    assert _assert_ui(payload, "contains", expected="example.com")["assertion"] == "FAILED"


def test_attribute_noise_inside_json_string_values_is_stripped(proof_env) -> None:
    payload = json.dumps({"html": '<div id="footer-123">Footer</div>'})
    assert _assert_ui(payload, "contains", expected="Footer")["assertion"] == "PASSED"
    assert _assert_ui(payload, "contains", expected="footer-123")["assertion"] == "FAILED"


def test_prose_with_attribute_word_and_equals_survives_stripping(proof_env) -> None:
    # Natural prose phrased as "word = word" is not an attribute assignment.
    payload = "Travel class = economy is included\nFree rebooking\n"
    assert _assert_ui(payload, "contains", expected="economy is included")["assertion"] == "PASSED"
    # Glued attribute-looking assignments (id=..., class=...) are still noise.
    dump = 'class="economy"\nid=seat-7\nSeat selection'
    assert _assert_ui(dump, "contains", expected="Seat selection")["assertion"] == "PASSED"
    assert _assert_ui(dump, "contains", expected="economy")["assertion"] == "FAILED"
    assert _assert_ui(dump, "contains", expected="seat-7")["assertion"] == "FAILED"


# ── absent (positive control) ────────────────────────────────────────────────


def test_absent_without_must_contain_is_error(proof_env) -> None:
    verdict = _assert_ui(PAGE, "absent", forbidden="refund")
    assert verdict["assertion"] == "ERROR"
    assert "must_contain" in verdict["reason"]
    assert "positive control" in verdict["reason"]


def test_absent_passes_with_control(proof_env) -> None:
    verdict = _assert_ui(PAGE, "absent", forbidden="refund", must_contain="Welcome back, Ada")
    assert verdict["assertion"] == "PASSED"
    assert verdict["actual"] == "absent"


def test_absent_fails_when_forbidden_visible(proof_env) -> None:
    verdict = _assert_ui(PAGE, "absent", forbidden="Ada", must_contain="Order summary")
    assert verdict["assertion"] == "FAILED"
    assert verdict["actual"] == "visible"


def test_absent_positive_control_failure_is_failed(proof_env) -> None:
    verdict = _assert_ui(PAGE, "absent", forbidden="refund", must_contain="not on this page")
    assert verdict["assertion"] == "FAILED"
    assert "Positive control" in verdict["reason"]


def test_absent_falls_back_to_expected_and_requires_text(proof_env) -> None:
    assert _assert_ui(PAGE, "absent", expected="refund", must_contain="Ada")["assertion"] == "PASSED"
    verdict = _assert_ui(PAGE, "absent", must_contain="Ada")
    assert verdict["assertion"] == "ERROR"
    assert "forbidden" in verdict["reason"]


# ── count_eq ─────────────────────────────────────────────────────────────────


def test_count_eq_pass_fail_and_actual_count(proof_env) -> None:
    assert _assert_ui(PAGE, "count_eq", expected="Status", count=1)["assertion"] == "PASSED"
    failed = _assert_ui(PAGE, "count_eq", expected="Status", count=2)
    assert failed["assertion"] == "FAILED"
    assert failed["actual"] == 1
    assert failed["expected_count"] == 2
    # A string count is tolerated (LLMs often pass one).
    assert _assert_ui(PAGE, "count_eq", expected="Status", count="1")["assertion"] == "PASSED"


def test_count_eq_input_judgment_rules(proof_env) -> None:
    assert _assert_ui(PAGE, "count_eq", expected="Status")["assertion"] == "ERROR"
    assert _assert_ui(PAGE, "count_eq", expected="Status", count="lots")["assertion"] == "ERROR"
    assert _assert_ui(PAGE, "count_eq", expected="Status", count=-1)["assertion"] == "ERROR"


# ── no_placeholder ───────────────────────────────────────────────────────────


def test_no_placeholder_catches_all_documented_shapes(proof_env) -> None:
    payload = "Total: undefined\n" "Discount: NaN\n" "Cart: [object Object]\n" "Hello {{user.name}}\n" "Amount ${amount}\n" "# Billing History\n" "Error checkout.payment.declined\n"
    verdict = _assert_ui(payload, "no_placeholder")
    assert verdict["assertion"] == "FAILED"
    kinds = {finding["kind"] for finding in verdict["actual"]}
    assert kinds == {
        "undefined_token",
        "nan_token",
        "object_object",
        "unsubstituted_template",
        "raw_markdown_header",
        "i18n_key",
    }
    assert "placeholder shapes" in verdict["reason"]


def test_no_placeholder_is_conservative(proof_env) -> None:
    payload = "She left the question undefinedness aside.\n" "#1 ranked seller\n" "Visit www.example.com for help\n" "Ratio a.b.c and version v1.2.3 listed\n"
    verdict = _assert_ui(payload, "no_placeholder")
    assert verdict["assertion"] == "PASSED"
    assert verdict["actual"] == []
    assert _assert_ui(PAGE, "no_placeholder")["assertion"] == "PASSED"


def test_no_placeholder_spares_ordinary_prose_that_resembles_shapes(proof_env) -> None:
    # "undefined" used as an English adjective and a dotted file name are
    # ordinary copy, not leaked placeholders.
    payload = "The function has undefined behavior\nDownload config.settings.json\n"
    verdict = _assert_ui(payload, "no_placeholder")
    assert verdict["assertion"] == "PASSED"
    assert verdict["actual"] == []


def test_no_placeholder_still_flags_standalone_placeholder_tokens(proof_env) -> None:
    payload = "Hi {{name}}\nSum ${tot}\nGot [object Object]\nTotal: NaN\nResult: undefined\n"
    verdict = _assert_ui(payload, "no_placeholder")
    assert verdict["assertion"] == "FAILED"
    kinds = {finding["kind"] for finding in verdict["actual"]}
    assert {"unsubstituted_template", "object_object", "nan_token", "undefined_token"} <= kinds


def test_no_placeholder_word_boundary_and_object_object(proof_env) -> None:
    assert _assert_ui("NaN", "no_placeholder")["assertion"] == "FAILED"
    assert _assert_ui("UnNaN like text", "no_placeholder")["assertion"] == "PASSED"
    assert _assert_ui("got [object Object] back", "no_placeholder")["assertion"] == "FAILED"


def test_no_placeholder_ignores_expected_with_note(proof_env) -> None:
    verdict = _assert_ui(PAGE, "no_placeholder", expected="whatever")
    assert verdict["assertion"] == "PASSED"
    assert "ignores 'expected'" in verdict["note"]


# ── input judgment rules ─────────────────────────────────────────────────────


@pytest.mark.parametrize("payload", ["", "   ", "\n\t \n", None])
def test_empty_payload_is_error_never_silent_pass(proof_env, payload) -> None:
    for mode in ("contains", "absent", "count_eq", "no_placeholder"):
        verdict = _assert_ui(payload, mode, expected="anything", forbidden="x", must_contain="y")
        assert verdict["assertion"] == "ERROR", f"mode={mode}"
        assert "empty or blank" in verdict["reason"]


def test_bad_mode_is_error(proof_env) -> None:
    verdict = _assert_ui(PAGE, "equals", expected="Order total")
    assert verdict["assertion"] == "ERROR"
    assert "Unknown mode" in verdict["reason"]
    for mode in ("contains", "absent", "count_eq", "no_placeholder"):
        assert mode in verdict["reason"]


def test_unresolved_placeholder_in_expected_is_error(proof_env) -> None:
    verdict = _assert_ui(PAGE, "contains", expected="${never_captured}")
    assert verdict["assertion"] == "ERROR"
    assert "never_captured" in verdict["reason"]


def test_placeholder_interpolation_in_expected(proof_env) -> None:
    # The value below was stored through the API-side helper on purpose:
    # both tool families must read the same per-test-id store.
    set_variable_value("carrier", "Pony Express")
    assert _assert_ui(PAGE, "contains", expected="${carrier}")["assertion"] == "PASSED"
    set_variable_value("ghost", "Casper")
    assert _assert_ui(PAGE, "contains", expected="${ghost}")["assertion"] == "FAILED"


# ── scope ────────────────────────────────────────────────────────────────────


def test_scope_narrows_matching_to_a_paragraph_window(proof_env) -> None:
    # "paid" appears only in the Order summary paragraph.
    verdict = _assert_ui(PAGE, "absent", forbidden="paid", must_contain="Carrier", scope="Shipping")
    assert verdict["assertion"] == "PASSED"
    # Without the scope the same forbidden text is visible.
    verdict = _assert_ui(PAGE, "absent", forbidden="paid", must_contain="Order total")
    assert verdict["assertion"] == "FAILED"
    # Counting is scoped too.
    scoped = _assert_ui(PAGE, "count_eq", expected=":", count=1, scope="Shipping")
    assert scoped["assertion"] == "PASSED"
    unscoped = _assert_ui(PAGE, "count_eq", expected=":", count=1)
    assert unscoped["assertion"] == "FAILED"
    assert unscoped["actual"] == 3


def test_scope_not_found_is_error(proof_env) -> None:
    verdict = _assert_ui(PAGE, "contains", expected="Order total", scope="Refunds")
    assert verdict["assertion"] == "ERROR"
    assert "Refunds" in verdict["reason"]


# ── capture_ui_value ─────────────────────────────────────────────────────────


def test_capture_exact_match_strategy(proof_env) -> None:
    verdict = _capture(PAGE, "42.50", "total")
    assert verdict["assertion"] == "PASSED"
    assert verdict["strategy"] == "exact"
    assert verdict["value"] == "42.50"
    assert verdict["reference"] == "${total}"
    # Stored under the current test id in the shared store.
    assert _VARIABLES["ui-assertion-tests"]["total"] == "42.50"
    assert interpolate_value("${total}") == "42.50"


def test_capture_adjacent_label_strategy(proof_env) -> None:
    verdict = _capture(PAGE, "Order total", "total")
    assert verdict["assertion"] == "PASSED"
    assert verdict["strategy"] == "adjacent"
    assert verdict["value"] == "42.50"
    verdict = _capture(PAGE, "Carrier", "carrier")
    assert verdict["assertion"] == "PASSED"
    assert verdict["value"] == "Pony Express"


def test_capture_glued_label_extracts_value_not_the_label(proof_env) -> None:
    # "Total42" glues the label to its value: the label itself must never be
    # captured as if it were the value.
    verdict = _capture("Total42\n", "Total", "total")
    assert verdict["assertion"] == "PASSED"
    assert verdict["value"] == "42"
    # The spaced label forms still capture what follows them.
    assert _capture("Total: 42", "Total", "t")["value"] == "42"
    assert _capture("Total 42", "Total", "t")["value"] == "42"
    # A hint equal to the whole line still IS the value.
    whole = _capture("Total", "Total", "t")
    assert whole["strategy"] == "exact"
    assert whole["value"] == "Total"


def test_capture_underscore_glued_label_strips_the_glue_char(proof_env) -> None:
    # "Total_42" glues the label to its value with an underscore: the glue
    # character must not leak into the captured value.
    verdict = _capture("Total_42\n", "Total", "total")
    assert verdict["assertion"] == "PASSED"
    assert verdict["value"] == "42"
    # The separated form is unchanged.
    assert _capture("Total:42", "Total", "t")["value"] == "42"


def test_capture_scope_disambiguates_repeated_labels(proof_env) -> None:
    payload = "Profile\nStatus: active\n\nBilling\nStatus: overdue\n"
    assert _capture(payload, "Status", "status", scope="Profile")["value"] == "active"
    assert _capture(payload, "Status", "status", scope="Billing")["value"] == "overdue"


def test_capture_absent_is_error_naming_the_search(proof_env) -> None:
    verdict = _capture(PAGE, "unicorn", "beast")
    assert verdict["assertion"] == "ERROR"
    assert "unicorn" in verdict["reason"]
    scoped = _capture(PAGE, "Order total", "total", scope="Shipping")
    assert scoped["assertion"] == "ERROR"
    assert "Shipping" in scoped["reason"]


def test_capture_requires_hint_and_name(proof_env) -> None:
    assert _capture(PAGE, "", "x")["assertion"] == "ERROR"
    assert _capture(PAGE, "Carrier", "  ")["assertion"] == "ERROR"
    assert _capture("", "Carrier", "carrier")["assertion"] == "ERROR"


def test_capture_round_trip_with_ui_assertion_and_api_store(proof_env) -> None:
    captured = _capture(PAGE, "Carrier", "carrier")
    assert captured["assertion"] == "PASSED"
    # The captured value feeds a later UI assertion by name ...
    assert _assert_ui(PAGE, "contains", expected="${carrier}")["assertion"] == "PASSED"
    # ... and is visible through the API-side store, keyed by the same test id.
    assert interpolate_value("${carrier}") == "Pony Express"
    set_variable_value("api_only", "from-api")
    assert _assert_ui(PAGE, "contains", expected="${api_only}")["assertion"] == "FAILED"


# ── audit ledger ─────────────────────────────────────────────────────────────


def test_every_verdict_is_recorded_with_payload_fingerprint(proof_env) -> None:
    _assert_ui(PAGE, "contains", expected="Order total")  # PASSED
    _assert_ui(PAGE, "contains", expected="refund")  # FAILED
    _assert_ui(PAGE, "equals", expected="x")  # ERROR (unknown mode)
    _capture(PAGE, "42.50", "total")  # PASSED

    lines = _ledger_lines(proof_env)
    assert [entry["verdict"] for entry in lines] == ["PASSED", "FAILED", "ERROR", "PASSED"]
    assert [entry["source"] for entry in lines] == ["assert_ui", "assert_ui", "assert_ui", "capture_ui_value"]
    expected_subject = hashlib.sha1(PAGE.strip().encode("utf-8")).hexdigest()[:12]
    for entry in lines[:2] + [lines[3]]:
        assert entry["subject"] == expected_subject
        assert entry["test_id"] == "ui-assertion-tests"
    assert lines[2]["subject"] == hashlib.sha1(b"").hexdigest()[:12]
    capture_entry = lines[3]
    assert capture_entry["as_name"] == "total"
    assert capture_entry["value"] == "42.50"


def test_ledger_records_falsy_fields_like_zero_actual(proof_env) -> None:
    _assert_ui(PAGE, "count_eq", expected="refund", count=0)  # PASSED with actual=0
    entry = _ledger_lines(proof_env)[-1]
    assert entry["verdict"] == "PASSED"
    assert entry["mode"] == "count_eq"
    assert entry["actual"] == 0


# ── raw mode (URLs / attributes kept) ────────────────────────────────────────


def test_urls_are_stripped_by_default_but_kept_in_raw_mode(proof_env) -> None:
    payload = "Order confirmed\nTrack: https://shop.test/orders/42/track\n"
    assert _assert_ui(payload, "contains", expected="https://shop.test/orders/42/track")["assertion"] == "FAILED"
    raw = _assert_ui(payload, "contains", expected="https://shop.test/orders/42/track", raw=True)
    assert raw["assertion"] == "PASSED"


def test_attribute_assignments_are_kept_in_raw_mode(proof_env) -> None:
    payload = 'Sign in\ndata-testid="login-btn"'
    assert _assert_ui(payload, "contains", expected='data-testid="login-btn"')["assertion"] == "FAILED"
    assert _assert_ui(payload, "contains", expected='data-testid="login-btn"', raw=True)["assertion"] == "PASSED"


def test_capture_can_keep_a_url_in_raw_mode(proof_env) -> None:
    payload = "Receipt\nLink: https://shop.test/r/9\n"
    captured = _capture(payload, "Link", "receipt_link")
    assert captured.get("value") != "https://shop.test/r/9"  # the URL was stripped from the searched text
    verdict = json.loads(asyncio.run(capture_ui_value(payload, "Link", "receipt_link", None, True)))
    assert verdict["assertion"] == "PASSED"
    assert verdict["value"] == "https://shop.test/r/9"


# ── ledger redaction ─────────────────────────────────────────────────────────


def test_ledger_redacts_secret_captures_and_values(proof_env) -> None:
    page = "Account\nPassword: hunter2-pass\nSession note: id hunter2-sess seen\n"
    _capture(page, "Password", "login_password")
    set_variable_value("session_token", "hunter2-sess")
    _assert_ui(page, "contains", expected="${session_token}")

    raw = open(f"{proof_env.get_proof_path()}/assertion_logs.log", encoding="utf-8").read()
    assert "hunter2-pass" not in raw
    assert "hunter2-sess" not in raw
    assert "[REDACTED]" in raw
    # The captured value itself is still usable by later steps.
    assert interpolate_value("${login_password}") == "hunter2-pass"


# ── opt-in registration (ENABLE_UI_ASSERTIONS) ───────────────────────────────

UI_AGENTS = ("browser_nav_agent", "mcp_nav_agent", "mobile_nav_agent")

_REGISTRY_PROBE = (
    "import json;"
    "import testzeus_hercules.core.tools.ui_assertions;"
    "from testzeus_hercules.core.tools.tool_registry import tool_registry;"
    "print('PROBE:' + json.dumps({a: sorted(e['name'] for e in tool_registry[a] if e['name'] in ('assert_ui','capture_ui_value')) for a in %r}))"
) % (UI_AGENTS,)


def _probe_registry(flag):
    import os
    import subprocess
    import sys

    env = {key: value for key, value in os.environ.items() if key != "ENABLE_UI_ASSERTIONS"}
    if flag is not None:
        env["ENABLE_UI_ASSERTIONS"] = flag
    env["IS_TEST_ENV"] = "true"
    completed = subprocess.run([sys.executable, "-c", _REGISTRY_PROBE], capture_output=True, text=True, env=env, timeout=120)
    line = next(item for item in completed.stdout.splitlines() if item.startswith("PROBE:"))
    return json.loads(line[len("PROBE:") :])


def test_ui_tools_are_not_registered_by_default() -> None:
    assert _probe_registry(None) == {agent: [] for agent in UI_AGENTS}


def test_ui_tools_are_registered_for_the_nav_agents_when_enabled() -> None:
    assert _probe_registry("true") == {agent: ["assert_ui", "capture_ui_value"] for agent in UI_AGENTS}


@pytest.fixture
def clean_ui_registry():
    saved = {agent: list(tool_registry[agent]) for agent in UI_AGENTS}
    for agent in UI_AGENTS:
        tool_registry[agent] = [entry for entry in tool_registry[agent] if entry["name"] not in ("assert_ui", "capture_ui_value")]
    yield
    for agent in UI_AGENTS:
        tool_registry[agent] = saved[agent]


def test_config_flag_defaults_off_and_parses_truthy_values(monkeypatch) -> None:
    config = get_global_conf()
    monkeypatch.delitem(config.get_config(), "ENABLE_UI_ASSERTIONS", raising=False)
    assert config.get_enable_ui_assertions() is False
    for value, expected in (("true", True), ("TRUE", True), ("1", True), ("yes", True), ("false", False), ("", False), ("0", False)):
        monkeypatch.setitem(config.get_config(), "ENABLE_UI_ASSERTIONS", value)
        assert config.get_enable_ui_assertions() is expected, value


def test_prompt_mentions_ui_tools_only_when_they_are_registered(clean_ui_registry) -> None:
    from testzeus_hercules.core.agents.base_nav_agent import ui_assertion_prompt_addendum
    from testzeus_hercules.core.tools.ui_assertions import register_ui_assertion_tools

    for agent in UI_AGENTS:
        assert ui_assertion_prompt_addendum(agent) == ""
    register_ui_assertion_tools()
    for agent in UI_AGENTS:
        addendum = ui_assertion_prompt_addendum(agent)
        assert "assert_ui" in addendum and "capture_ui_value" in addendum
    assert ui_assertion_prompt_addendum("api_nav_agent") == ""
