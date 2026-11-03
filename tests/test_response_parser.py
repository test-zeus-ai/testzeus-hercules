"""Unit tests for utils.response_parser (parse_response).

Covers: valid planner JSON still parses (code fences, prose prefix, multiple
objects, braces inside string values) and pathological malformed output — a
message of unclosed "{" characters, which previously scanned quadratically —
now fails fast via the char-budget / candidate-cap bounds. Also covers the
intact-first parse (escaped ``\\n`` inside string values keeps all fields) and
the plain-text terminal normalizer (PASS/FAIL verdicts and the terminate
marker).
"""

import time

from testzeus_hercules.utils.response_parser import (
    TERMINATE_TASK_MARKER,
    _parse_terminal_text,
    parse_response,
)


def test_empty_and_blank_messages_return_empty_dict() -> None:
    assert parse_response("") == {}
    assert parse_response("   ") == {}


def test_plain_json_object_parses() -> None:
    assert parse_response('{"plan": "a", "next_step": "b"}') == {"plan": "a", "next_step": "b"}


def test_json_code_fence_with_prose_parses() -> None:
    message = 'Sure, here is the plan:\n```json\n{"plan": "do x", "next_step": "click", "terminate": "no"}\n```'
    parsed = parse_response(message)
    assert parsed["plan"] == "do x"
    assert parsed["next_step"] == "click"
    assert parsed["terminate"] == "no"


def test_last_complete_object_wins() -> None:
    message = '{"plan": "first", "next_step": "a"} {"plan": "second", "next_step": "b"}'
    parsed = parse_response(message)
    assert parsed["plan"] == "second"
    assert parsed["next_step"] == "b"


def test_braces_inside_string_values_do_not_break_candidate_scan() -> None:
    # The scan is string-aware: a "}" inside a string value no longer closes
    # the candidate early (the old depth-only scan produced broken candidates).
    message = '{"plan": "show } brace", "next_step": "y", "terminate": "no"}'
    parsed = parse_response(message)
    assert parsed["plan"] == "show } brace"
    assert parsed["next_step"] == "y"


def test_non_json_message_without_keys_returns_empty_dict() -> None:
    assert parse_response("just chat, no json and no planner keys here") == {}


def test_pathological_brace_run_fails_fast() -> None:
    # 100k unclosed "{" used to cost an O(n^2) scan (the planner path was
    # measured at ~2.3s for only 8k braces); the budget bounds it.
    message = "{" * 100000
    start = time.perf_counter()
    result = parse_response(message)
    elapsed = time.perf_counter() - start

    assert result == {}
    assert elapsed < 0.5


def test_large_valid_json_still_parses() -> None:
    plan_text = "step " * 5000  # comfortably large but under the scan budget
    message = '{"plan": "' + plan_text.strip() + '", "next_step": "x", "terminate": "no"}'
    parsed = parse_response(message)
    assert parsed["plan"] == plan_text.strip()
    assert parsed["terminate"] == "no"


def test_malformed_prefix_then_valid_object_parses() -> None:
    message = 'garbage { not json at all then {"plan": "ok", "next_step": "go"}'
    parsed = parse_response(message)
    assert parsed["plan"] == "ok"


# ── intact-first parse: escaped sequences must survive ────────────────────────


def test_escaped_newline_json_keeps_target_helper() -> None:
    # The raw text contains a JSON-escaped backslash-n sequence inside a string
    # value; rewriting it before parsing corrupts the escape and drops fields.
    message = '{"plan": "step one\\\\nstep two", "next_step": "go", "target_helper": "api", "terminate": "no"}'
    parsed = parse_response(message)
    assert parsed["plan"] == "step one\\nstep two"
    assert parsed["next_step"] == "go"
    assert parsed["target_helper"] == "api"
    assert parsed["terminate"] == "no"


def test_fenced_json_with_escaped_newline_keeps_fields() -> None:
    message = '```json\n{"plan": "a\\\\nb", "next_step": "click", "target_helper": "sec", "terminate": "no"}\n```'
    parsed = parse_response(message)
    assert parsed["target_helper"] == "sec"
    assert parsed["next_step"] == "click"


def test_narration_then_json_takes_last_object() -> None:
    message = 'Let me think.\n{"plan": "old", "next_step": "a"}\n{"plan": "final", "next_step": "b", "target_helper": "sql"}'
    parsed = parse_response(message)
    assert parsed["plan"] == "final"
    assert parsed["next_step"] == "b"
    assert parsed["target_helper"] == "sql"


def test_non_planner_json_still_falls_through_to_repaired_scan() -> None:
    # A JSON object without planner keys must not short-circuit intact; the
    # repaired scan remains the fallback (same outcome as before).
    message = '{"note\\\\nline": 1}'
    assert parse_response(message) == {}


# ── plain-text terminal normalizer ────────────────────────────────────────────


def test_terminal_pass_text_becomes_terminal_decision() -> None:
    parsed = parse_response("PASS: checkout completed successfully")
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is True
    assert parsed["assert_summary"] == "PASS: checkout completed successfully"


def test_terminal_fail_text_marks_failure() -> None:
    parsed = parse_response("FAIL: checkout could not complete")
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is False


def test_terminal_pass_with_failure_marker_is_not_passed() -> None:
    parsed = parse_response("PASS: step finished, but the flow is incomplete")
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is False


def test_terminate_marker_only_text_normalizes() -> None:
    parsed = parse_response(f"Checkout completed\n{TERMINATE_TASK_MARKER}")
    assert parsed["terminate"] == "yes"
    assert parsed["final_response"] == "Checkout completed"
    assert "is_assert" not in parsed


def test_plain_text_without_terminal_signal_returns_empty() -> None:
    assert parse_response("just narrating progress, nothing terminal yet") == {}


def test_json_containing_pass_substring_still_parses_as_json() -> None:
    message = '{"plan": "PASS the login check", "next_step": "click", "terminate": "no"}'
    parsed = parse_response(message)
    assert parsed["plan"] == "PASS the login check"
    assert parsed["terminate"] == "no"


def test_parse_terminal_text_unit_marker_only_has_no_assert_fields() -> None:
    result = _parse_terminal_text(f"done\n{TERMINATE_TASK_MARKER}")
    assert result == {"terminate": "yes", "final_response": "done"}
    assert _parse_terminal_text("nothing to see here") == {}


# ── canonical helper verdict sections (### Success: / ### Error or Uncertainty:) ──


CANONICAL_SUCCESS_TEXT = "### Success:\n" "previous_step: verify checkout\n" "current_output: Order placed successfully\n" "Data: order id 42\n" f"{TERMINATE_TASK_MARKER}"

CANONICAL_ERROR_TEXT = "### Error or Uncertainty:\n" "previous_step: verify checkout\n" "current_output: The submit button never enabled the form\n" f"{TERMINATE_TASK_MARKER}"


def test_canonical_success_section_is_terminal_pass() -> None:
    parsed = parse_response(CANONICAL_SUCCESS_TEXT)
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is True
    # The summary is the full message minus the terminate marker (newlines are
    # collapsed by the plain-text parse path).
    expected_summary = CANONICAL_SUCCESS_TEXT.replace("\n", " ").replace(TERMINATE_TASK_MARKER, "").strip()
    assert parsed["final_response"] == expected_summary


def test_canonical_error_or_uncertainty_section_is_terminal_fail() -> None:
    parsed = parse_response(CANONICAL_ERROR_TEXT)
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is False


def test_canonical_success_section_still_consults_failure_markers() -> None:
    message = f"### Success:\ncurrent_output: finished, but the flow is incomplete\n{TERMINATE_TASK_MARKER}"
    parsed = parse_response(message)
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is False


def test_canonical_both_sections_fail_wins() -> None:
    message = "### Success:\ncurrent_output: clicked through the flow\n\n### Error or Uncertainty:\ncurrent_output: the final total did not match\n" f"{TERMINATE_TASK_MARKER}"
    parsed = parse_response(message)
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is False


def test_canonical_section_without_terminate_marker_is_still_terminal() -> None:
    parsed = parse_response("### Success:\ncurrent_output: done\nData: 5 rows")
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is True


def test_canonical_section_with_no_content_is_not_a_verdict() -> None:
    # An empty Success section carries no verdict: the message keeps the
    # marker-only behavior (terminate yes, no assert fields).
    result = _parse_terminal_text(f"### Success:\n{TERMINATE_TASK_MARKER}")
    assert result["terminate"] == "yes"
    assert "is_assert" not in result


def test_pass_fail_prefix_still_works_alongside_canonical_sections() -> None:
    parsed = parse_response("PASS: checkout completed successfully")
    assert parsed["is_passed"] is True
    parsed = parse_response("FAIL: checkout could not complete")
    assert parsed["is_passed"] is False


# ── verdict-prefix precision and marker scoping ───────────────────────────────


def test_pass_fail_legend_prefix_is_not_terminal() -> None:
    # "PASS/FAIL legend: ..." narrates a legend — it is not a verdict. The
    # prefix pattern requires a separator (colon/dash) right after PASS/FAIL.
    assert _parse_terminal_text("PASS/FAIL legend: which is which") == {}
    assert parse_response("PASS/FAIL legend: which is which") == {}


def test_pass_fail_dash_prefix_is_still_terminal() -> None:
    parsed = parse_response("PASS - checkout completed successfully")
    assert parsed["terminate"] == "yes"
    assert parsed["is_passed"] is True


def test_pass_fail_em_dash_prefix_is_terminal() -> None:
    # The em dash is a verdict separator too: "FAIL — ..." must be terminal
    # (with the right verdict), not silently non-terminal narration.
    parsed = parse_response("FAIL — payment declined")
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is False
    parsed = parse_response("PASS — checkout completed successfully")
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is True


def test_success_section_pass_survives_uncertain_word_elsewhere() -> None:
    # A "### Success:" verdict is judged on its own section content: the
    # failure-marker scan must not flip it on unrelated narration elsewhere
    # in the message.
    message = "Earlier probes looked uncertain but the final check settled it.\n### Success:\ncurrent_output: order placed\nData: order id 42"
    parsed = parse_response(message)
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is True


def test_marker_inside_success_section_still_fails() -> None:
    # Existing behavior preserved: a failure marker inside the Success section
    # itself still fails the verdict.
    message = "### Success:\ncurrent_output: the flow stayed uncertain after retrying"
    parsed = parse_response(message)
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is False


def test_success_section_ends_at_terminate_marker_not_trailing_narration() -> None:
    # Narration AFTER the terminate marker is not part of the Success section:
    # an "[error]" mention there must not flip the verdict to a fail.
    message = "### Success:\ncurrent_output: order placed\n" f"{TERMINATE_TASK_MARKER}\n(earlier we saw an [error] but it resolved)"
    parsed = parse_response(message)
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is True


def test_success_section_failure_marker_before_terminate_marker_still_fails() -> None:
    # A failure marker INSIDE the section (before the terminate marker) still
    # fails the verdict — only post-marker narration is out of scope.
    message = "### Success:\ncurrent_output: an [error] surfaced mid-flow\n" f"{TERMINATE_TASK_MARKER}\nnarration afterwards"
    parsed = parse_response(message)
    assert parsed["terminate"] == "yes"
    assert parsed["is_assert"] is True
    assert parsed["is_passed"] is False
