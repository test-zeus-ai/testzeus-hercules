"""Offline hardening tests for testzeus_hercules.utils.junit_helper.

Covers verified crash/robustness findings plus the honest-reporting verdict
policy:
1. feature/scenario names containing "/" must not escape the junit base path
   (the generated file name is fully sanitized and the directory is created);
2. control characters in scenario names / failure messages / final responses
   must be stripped instead of aborting XML construction;
3. merge_junit_xml must skip unreadable inputs, keep them on disk, and only
   delete the successfully parsed inputs after the merged XML was written;
4. verdict strictness (opt-in, STRICT_VERDICT=true): a case is green only when it terminated cleanly AND
   recorded a verified pass — an aborted, cancelled, unproven or harness-failed
   scenario must produce a Failure carrying the scenario's own reason.
"""

import asyncio
import os

import pytest
from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator, build_junit_xml


class _FakeConf:
    """Minimal stand-in for the global config used by junit_helper."""

    def __init__(self, tmp_path) -> None:
        self.junit_base = tmp_path / "junit"
        self.junit_base.mkdir(parents=True, exist_ok=True)

    def get_junit_xml_base_path(self) -> str:
        return str(self.junit_base)

    def get_mode(self) -> str:
        return "prod"


@pytest.fixture()
def junit_env(monkeypatch, tmp_path):
    conf = _FakeConf(tmp_path)
    # build_junit_xml / merge_junit_xml resolve paths and MODE through this
    # module-level name, so patch it to the same fake conf.
    import testzeus_hercules.utils.junit_helper as junit_helper

    monkeypatch.setattr(junit_helper, "get_global_conf", lambda: conf)
    return conf


_JSON_DATA = {
    "terminate": "yes",
    "final_response": "ok",
    "is_assert": False,
    "is_passed": True,
}


def test_build_junit_xml_sanitizes_slashes_in_names(junit_env) -> None:
    path = asyncio.run(
        build_junit_xml(
            dict(_JSON_DATA),
            execution_time=1.0,
            cost_metric={},
            feature="Feature A",
            scenario="GET /users/1 list",
        )
    )

    base = junit_env.get_junit_xml_base_path()
    assert os.path.isfile(path), f"expected junit file at {path}"
    # The name must stay a single path component under the junit base dir.
    assert os.path.dirname(path) == base
    assert "/" not in os.path.basename(path).replace("_results.xml", "")
    # A short hash of the unsanitized names is appended so distinct scenarios
    # whose sanitized names collide keep distinct result files.
    assert os.path.basename(path).startswith("Feature_A_GET__users_1_list_")
    assert os.path.basename(path).endswith("_results.xml")


def test_build_junit_xml_strips_control_characters(junit_env) -> None:
    path = asyncio.run(
        build_junit_xml(
            {"terminate": "yes", "final_response": "ok\x0b", "is_assert": False, "is_passed": True},
            execution_time=1.0,
            cost_metric={},
            feature="Feature\x07 A",
            scenario="Scenario: bells\x07 and\x0bwhistles",
        )
    )

    assert os.path.isfile(path)
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    for control_char in ("\x00", "\x07", "\x0b"):
        assert control_char not in content
    assert "Scenario: bells andwhistles" in content
    assert "Final Response: ok" in content


def _write_file(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def test_merge_junit_xml_survives_malformed_and_missing_inputs(junit_env, tmp_path) -> None:
    ok_xml = _write_file(
        str(tmp_path / "ok.xml"),
        '<testsuites><testsuite name="Feature: demo" tests="1" failures="0" errors="0" time="0.1">'
        '<testcase name="Scenario: demo" classname="Feature: demo" time="0.1"/>'
        "</testsuite></testsuites>",
    )
    bad_xml = _write_file(str(tmp_path / "bad.xml"), "<testsuites><testsuite name='x'>")
    missing_xml = str(tmp_path / "missing.xml")
    merged_dir = tmp_path / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    merged = str(merged_dir / "merged_result.xml")

    # Must not raise despite one malformed and one missing input.
    asyncio.run(JUnitXMLGenerator.merge_junit_xml([ok_xml, bad_xml, missing_xml], merged))

    with open(merged, "r", encoding="utf-8") as handle:
        merged_content = handle.read()
    assert "Scenario: demo" in merged_content

    # ok.xml was consumed by the merge (existing semantics outside debug mode)…
    assert not os.path.exists(ok_xml)
    # …while the malformed input is preserved for debugging, and a missing file
    # obviously stays missing.
    assert os.path.isfile(bad_xml)
    assert not os.path.exists(missing_xml)


def test_merge_junit_xml_keeps_inputs_when_write_fails(junit_env, tmp_path, monkeypatch) -> None:
    ok_xml = _write_file(
        str(tmp_path / "ok.xml"),
        '<testsuites><testsuite name="Feature: demo" tests="1" failures="0" errors="0" time="0.1">'
        '<testcase name="Scenario: demo" classname="Feature: demo" time="0.1"/>'
        "</testsuite></testsuites>",
    )
    merged = str(tmp_path / "merged" / "merged_result.xml")

    def _explode(*args, **kwargs):
        raise OSError("destination unavailable")

    monkeypatch.setattr("aiofiles.open", _explode)

    with pytest.raises(OSError):
        asyncio.run(JUnitXMLGenerator.merge_junit_xml([ok_xml], merged))

    # The merged file could not be written, so the input must survive.
    assert os.path.isfile(ok_xml)


def test_junit_xml_copy_and_merge_preserve_unicode_names(junit_env, tmp_path) -> None:
    """junitparser writes UTF-8; the copy/merge must not re-decode with the
    locale codec (mojibake or UnicodeEncodeError on non-Latin-1 names)."""
    scenario = "Тест 测试 🚀"
    feature = "Фича 功能"
    path = asyncio.run(
        build_junit_xml(
            dict(_JSON_DATA),
            execution_time=1.0,
            cost_metric={},
            feature=feature,
            scenario=scenario,
        )
    )

    assert os.path.isfile(path)
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert scenario in content and feature in content
    assert [case["name"] for case in parse_junit_testcases(path)] == [scenario]

    merged = str(tmp_path / "merged" / "merged_result.xml")
    os.makedirs(os.path.dirname(merged), exist_ok=True)
    asyncio.run(JUnitXMLGenerator.merge_junit_xml([path], merged))

    with open(merged, "r", encoding="utf-8") as handle:
        merged_content = handle.read()
    assert scenario in merged_content
    assert [case["name"] for case in parse_junit_testcases(merged)] == [scenario]


# ── honest-reporting verdict policy ───────────────────────────────────────────
# Green requires terminate == "yes" AND a verified pass; anything else (aborted,
# cancelled, unproven, harness-failed) must come out as a Failure carrying the
# scenario's own reason — never a generic constant that masks it.


def _build_and_parse(junit_env, json_data: dict) -> dict:
    """Drive the real build_junit_xml and return the single parsed testcase."""
    path = asyncio.run(
        build_junit_xml(
            dict(json_data),
            execution_time=1.0,
            cost_metric={},
            feature="Feature: Verdict",
            scenario="Scenario: verdict probe",
        )
    )
    cases = parse_junit_testcases(path)
    assert len(cases) == 1
    return cases[0]


def test_verdict_assert_branch_pass_matrix(junit_env) -> None:
    case = _build_and_parse(
        junit_env,
        {"terminate": "yes", "final_response": "ok", "is_assert": True, "is_passed": True, "assert_summary": "verified ok"},
    )
    assert case["status"] == "passed"
    assert case["failure_message"] is None
    assert "verified ok" in (case["system_out"] or "")


def test_verdict_assert_branch_failure_matrix(junit_env) -> None:
    # Failed assertion and missing is_passed are red in both verdict modes.
    for json_data in (
        {"terminate": "yes", "is_assert": True, "is_passed": False, "assert_summary": "expected 200 got 500"},
        {"terminate": "yes", "is_assert": True, "assert_summary": "no is_passed recorded"},
    ):
        case = _build_and_parse(junit_env, json_data)
        assert case["status"] == "failed", json_data
        assert json_data["assert_summary"] in (case["failure_message"] or ""), json_data


def test_verdict_strict_assert_with_non_clean_terminate_is_failure(junit_env, monkeypatch) -> None:
    monkeypatch.setenv("STRICT_VERDICT", "true")
    case = _build_and_parse(junit_env, {"terminate": "no", "is_assert": True, "is_passed": True, "assert_summary": "did not terminate cleanly"})
    assert case["status"] == "failed"
    assert "did not terminate cleanly" in (case["failure_message"] or "")


def test_verdict_legacy_assert_pass_ignores_terminate(junit_env, monkeypatch) -> None:
    monkeypatch.delenv("STRICT_VERDICT", raising=False)
    case = _build_and_parse(junit_env, {"terminate": "no", "is_assert": True, "is_passed": True, "assert_summary": "asserted"})
    assert case["status"] == "passed"


def test_verdict_non_assert_terminate_yes_without_is_passed_is_failure_when_strict(junit_env, monkeypatch) -> None:
    # STRICT_VERDICT: a scenario that asserted nothing and terminated cleanly is NOT a pass:
    # the planner's own summary must surface as the failure message.
    monkeypatch.setenv("STRICT_VERDICT", "true")
    case = _build_and_parse(
        junit_env,
        {"terminate": "yes", "final_response": "ended after the browser session dropped", "is_assert": False},
    )
    assert case["status"] == "failed"
    assert "ended after the browser session dropped" in (case["failure_message"] or "")


def test_verdict_non_assert_terminate_yes_without_is_passed_passes_by_default(junit_env, monkeypatch) -> None:
    # Legacy default: no explicit assertion + clean terminate keeps passing.
    monkeypatch.delenv("STRICT_VERDICT", raising=False)
    case = _build_and_parse(
        junit_env,
        {"terminate": "yes", "final_response": "completed the flow", "is_assert": False},
    )
    assert case["status"] == "passed"


def test_verdict_non_assert_explicit_fail_is_failure_when_strict(junit_env, monkeypatch) -> None:
    monkeypatch.setenv("STRICT_VERDICT", "true")
    case = _build_and_parse(
        junit_env,
        {"terminate": "yes", "is_assert": False, "is_passed": False, "assert_summary": "run aborted mid-way"},
    )
    assert case["status"] == "failed"
    assert "run aborted mid-way" in (case["failure_message"] or "")


def test_verdict_non_assert_explicit_fail_passes_by_default(junit_env, monkeypatch) -> None:
    monkeypatch.delenv("STRICT_VERDICT", raising=False)
    case = _build_and_parse(
        junit_env,
        {"terminate": "yes", "is_assert": False, "is_passed": False, "assert_summary": "run aborted mid-way"},
    )
    assert case["status"] == "passed"


def test_verdict_non_assert_terminate_no_is_failure(junit_env) -> None:
    case = _build_and_parse(
        junit_env,
        {"terminate": "no", "final_response": "still working", "is_assert": False},
    )
    assert case["status"] == "failed"
    assert "still working" in (case["failure_message"] or "")


def test_verdict_non_assert_verified_complete_is_pass(junit_env) -> None:
    case = _build_and_parse(
        junit_env,
        {"terminate": "yes", "final_response": "ok", "is_assert": False, "is_passed": True},
    )
    assert case["status"] == "passed"
    assert case["failure_message"] is None


def test_verdict_missing_json_data_is_failure(junit_env) -> None:
    case = _build_and_parse(junit_env, {})
    assert case["status"] == "failed"
    assert "No scenario result" in (case["failure_message"] or "")


def test_verdict_aborted_crash_result_is_failure(junit_env) -> None:
    # The shape synthesized by the scenario loop for a crashed/cancelled run.
    case = _build_and_parse(
        junit_env,
        {
            "terminate": "yes",
            "is_assert": True,
            "is_passed": False,
            "final_response": "Scenario crashed with an unexpected error: RuntimeError: boom",
            "assert_summary": "EXPECTED RESULT: the scenario runs to completion.\nACTUAL RESULT: it aborted with RuntimeError: boom",
        },
    )
    assert case["status"] == "failed"
    assert "it aborted with RuntimeError: boom" in (case["failure_message"] or "")


def test_verdict_structured_execution_failure_beats_claimed_pass(junit_env) -> None:
    case = _build_and_parse(
        junit_env,
        {
            "terminate": "yes",
            "is_assert": True,
            "is_passed": True,
            "execution_failure": {
                "category": "agent_chain_failure",
                "stage": "scenario_execution",
                "error_type": "RuntimeError",
                "message": "agent chain collapsed",
                "evidence_path": "/logs/errors/agent_chain_failure_e1.log",
            },
        },
    )
    assert case["status"] == "failed"
    message = case["failure_message"] or ""
    assert "Execution failure: agent chain collapsed" in message
    assert "category=agent_chain_failure" in message
    assert "stage=scenario_execution" in message
    assert "type=RuntimeError" in message
    assert "evidence=/logs/errors/agent_chain_failure_e1.log" in message


def test_format_failure_record_renders_present_fields_only() -> None:
    from testzeus_hercules.utils.junit_helper import _format_failure_record

    assert _format_failure_record("Execution failure", {"message": "boom"}) == "Execution failure: boom"
    assert _format_failure_record("Execution failure", {"message": "boom", "stage": "harness_cleanup", "error_type": "OSError"}) == "Execution failure: boom [stage=harness_cleanup; type=OSError]"
    # A record with no message at all still reads sensibly.
    assert _format_failure_record("Cleanup failure", {}) == "Cleanup failure: no detail recorded"


# ── hostile property payloads must not abort the run ──────────────────────────
# Planner summaries are bound to case properties verbatim; a control character
# or lone surrogate in ANY of them used to make lxml abort XML construction,
# and since build_junit_xml sits outside the per-scenario try/except, one bad
# LLM response killed the whole batch with zero junit output.


def test_nasty_property_summary_produces_valid_xml(junit_env) -> None:
    one_mb = "x" * (1024 * 1024)
    nasty = {
        "terminate": "yes",
        "is_assert": True,
        "is_passed": False,
        "assert_summary": "EXPECTED RESULT: a report is written.\nACTUAL RESULT: bells\x07 \x1b[2J\ud800 del\x7f\x9f c1\udfff and ]]> markers.",
        "final_response": "verdict kept ]]> ok",
        "plan": "do step\x07 two",
        "meta\udfffkey": one_mb + "\x00tail",
    }

    path = asyncio.run(
        build_junit_xml(
            nasty,
            execution_time=1.0,
            cost_metric={},
            feature="Feature: Nasty\x7f payload",
            scenario="Scenario: nasty \ud800summary]]>",
        )
    )

    assert os.path.isfile(path)
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()

    # The XML must be well-formed and parseable, with the verdict preserved.
    cases = parse_junit_testcases(path)
    assert len(cases) == 1
    assert cases[0]["status"] == "failed"
    assert "a report is written" in (cases[0]["failure_message"] or "")
    assert "verdict kept ]]> ok" in (cases[0]["system_out"] or "")

    # Nothing incompatible survives into the serialized XML.
    for bad_char in ("\x00", "\x07", "\x0b", "\x7f", "\x9f", "\ud800", "\udfff"):
        assert bad_char not in content
    # Sanitized property-bound summary key/value are still reported.
    assert cases[0]["properties"].get("plan") == "do step two"
    assert cases[0]["properties"].get("metakey") == one_mb + "tail"


def test_nasty_cost_metric_property_produces_valid_xml(junit_env) -> None:
    path = asyncio.run(
        build_junit_xml(
            dict(_JSON_DATA),
            execution_time=1.0,
            cost_metric={"usage_including_cached_inference": {"total_cost": 0.5, "gpt-4o\x07": {"total_tokens": 42, "cost": 0.5}}},
            feature="Feature: Cost metric",
            scenario="Scenario: nasty cost metric",
        )
    )

    assert os.path.isfile(path)
    cases = parse_junit_testcases(path)
    assert len(cases) == 1
    assert cases[0]["status"] == "passed"


def test_hostile_scenario_name_paths_produce_valid_xml(junit_env) -> None:
    """stake_id (built in __main__) only replaces space/colon/slash/backslash/
    dot, so a control character or lone surrogate in the scenario NAME survives
    into the proof/log paths that are reported verbatim as fixed properties.
    Unsanitized, lxml aborts XML construction and the scenario is misreported
    as the generic "JUnit report generation failed" fallback instead of its
    real verdict."""
    proofs = "/proofs/bells\x07\ud800/run"
    logs = "/logs/bells\x07\ud800/run"

    path = asyncio.run(
        build_junit_xml(
            {"terminate": "yes", "final_response": "ok", "is_assert": True, "is_passed": True, "assert_summary": "verified ok"},
            execution_time=1.0,
            cost_metric={},
            feature="Feature: bells\x07 and\ud800whistles",
            scenario="Scenario: bells\x07 and\ud800whistles",
            feature_file_path=f"{proofs}.feature",
            output_file_path="",
            proofs_path=proofs,
            proofs_video_path=f"{proofs}.webm",
            proofs_screenshot_path=f"{proofs}/screenshots",
            network_logs_path=f"{logs}/net\x07.log",
            logs_path=logs,
            planner_thoughts_path=f"{logs}/agent_inner_thoughts.json",
        )
    )

    assert os.path.isfile(path)
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    for bad_char in ("\x00", "\x07", "\ud800", "\udfff"):
        assert bad_char not in content

    # The scenario's real verdict is preserved — this is NOT the generic
    # "JUnit report generation failed" fallback entry.
    cases = parse_junit_testcases(path)
    assert len(cases) == 1
    assert cases[0]["status"] == "passed"
    assert "verified ok" in (cases[0]["system_out"] or "")

    # Every fixed path property is still reported, with the incompatible
    # characters stripped from name and value.
    props = cases[0]["properties"]
    long_name = "Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs"
    for prop_name in ("Feature File", "Output File", "Proofs Video", long_name, "Proofs Screenshot", "Network Logs", "Agents Internal Logs", "Planner Thoughts"):
        assert prop_name in props, prop_name
    assert props["Feature File"] == "/proofs/bells/run.feature"
    assert props["Proofs Video"] == "/proofs/bells/run.webm"
    assert props["Proofs Screenshot"] == "/proofs/bells/run/screenshots"
    assert props[long_name] == "/proofs/bells/run"
    assert props["Network Logs"] == "/logs/bells/run/net.log"
    assert props["Agents Internal Logs"] == "/logs/bells/run"
    assert props["Planner Thoughts"] == "/logs/bells/run/agent_inner_thoughts.json"


# ── sanitized-name collisions must not swallow failures ───────────────────────


def test_colliding_sanitized_names_keep_both_reports(junit_env, tmp_path) -> None:
    """'transfer funds'/'move money' and 'transfer_funds'/'move_money' sanitize
    to the same stem; the second build used to overwrite the first result file,
    so the merged XML lost the failure and the exit code went green."""
    fail_path = asyncio.run(
        build_junit_xml(
            {
                "terminate": "yes",
                "is_assert": True,
                "is_passed": False,
                "assert_summary": "expected status code 200 but got 500",
                "final_response": "expected status code 200 but got 500",
            },
            execution_time=1.0,
            cost_metric={},
            feature="transfer funds",
            scenario="move money",
        )
    )
    pass_path = asyncio.run(
        build_junit_xml(
            dict(_JSON_DATA),
            execution_time=1.0,
            cost_metric={},
            feature="transfer_funds",
            scenario="move_money",
        )
    )

    # Distinct scenarios must never share one results file.
    assert fail_path != pass_path
    assert os.path.isfile(fail_path) and os.path.isfile(pass_path)

    merged = str(tmp_path / "merged" / "final_result.xml")
    os.makedirs(os.path.dirname(merged), exist_ok=True)
    asyncio.run(JUnitXMLGenerator.merge_junit_xml([fail_path, pass_path], merged))

    cases = parse_junit_testcases(merged)
    statuses = {case["name"]: case["status"] for case in cases}
    assert statuses == {"move money": "failed", "move_money": "passed"}

    # The surviving failure must still gate the CI exit code.
    from testzeus_hercules.__main__ import compute_exit_code

    failed = sum(1 for case in cases if case["status"] == "failed")
    errors = sum(1 for case in cases if case["status"] == "error")
    assert compute_exit_code([{"failed": failed, "errors": errors}]) == 1
